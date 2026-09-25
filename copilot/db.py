"""Database access. Two connections, two jobs:

- an admin connection, used once to create the demo database and the view layer;
- a reader connection, as the `copilot_reader` role, which is the only thing the agent ever uses.
  That role has no grant on Integra's base tables and cannot write. ReadOnlyDB.run makes every transaction
  read-only and sets a statement timeout for it. A statement sent without the guard can still read the
  system catalogue and lift its own timeout with a smuggled SET: the guard stops those, not the role
  (see sql/02_copilot_views.sql and the red-team section of the README).
"""
import datetime as dt
import decimal
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.sql import SQL, Identifier

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"


def default_data_dir():
    """Where the demo database lives: COPILOT_DATA_DIR, else ~/.cache/integra-copilot/pg.
    Not inside the project, because pgserver cannot start from a path that contains a space
    (it passes the socket directory to postgres unquoted), and project folders often have one."""
    d = Path(os.environ.get("COPILOT_DATA_DIR") or Path.home() / ".cache" / "integra-copilot" / "pg")
    return d if not any(c.isspace() for c in str(d)) else Path(tempfile.gettempdir()) / "integra-copilot-pg"


def local_server(data_dir=None, cleanup_mode="stop"):
    """Start (or reuse) a private PostgreSQL 16 from the pgserver package. Returns the admin URI."""
    import pgserver                                    # imported here: only the local demo needs it
    data_dir = Path(data_dir or default_data_dir())
    if any(c.isspace() for c in str(data_dir)):
        raise ValueError(f"PostgreSQL cannot start from a folder whose path has a space: {data_dir}")
    data_dir.mkdir(parents=True, exist_ok=True)
    return pgserver.get_server(str(data_dir), cleanup_mode=cleanup_mode).get_uri()


def reader_uri(admin_uri):
    """The same server, logged in as copilot_reader. Works for a local socket; for a remote database
    set COPILOT_DATABASE_URL to the reader's own URI with its password instead."""
    return make_conninfo(admin_uri, user="copilot_reader")


def _seed(company):
    """data/seed.py, after checking that it knows the demo company."""
    import sys
    sys.path.insert(0, str(ROOT))
    from data import seed
    if company not in seed.COMPANIES:
        raise ValueError(f"Unknown demo company {company!r}; choose one of {', '.join(seed.COMPANIES)}.")
    return seed


def database(admin_uri, name):
    """The admin URI of database `name` on the same server, created if it is missing."""
    with psycopg.connect(admin_uri, autocommit=True) as conn:
        if not conn.execute("select 1 from pg_database where datname = %s", (name,)).fetchone():
            conn.execute(SQL("create database {}").format(Identifier(name)))
    return make_conninfo(admin_uri, dbname=name)


def demo_database(admin_uri, company="A"):
    """Where a demo company lives on the local server: company A in the default database, as always, and any
    other company in a database of its own."""
    _seed(company)
    return admin_uri if company == "A" else database(admin_uri, f"company_{company.lower()}")


def create_demo(admin_uri, anchor=None, company="A", poison=False):
    """Create Integra's tables, load one demo company ("A" or "B"), then create the copilot views and reader
    role. One company per database: a database that already holds other data is refused. With poison, the
    canary strings of data/poison.py are planted in the new data (red-team runs only)."""
    seed = _seed(company)
    with psycopg.connect(admin_uri, autocommit=True) as conn:
        exists = conn.execute("select to_regclass('public.invoices') is not null").fetchone()[0]
        if not exists:
            conn.execute((SQL_DIR / "01_integra_subset.sql").read_text())
            tables = seed.generate(anchor, company=company)
            if poison:
                from data.poison import apply_poison
                tables = apply_poison(tables)
            seed.load(conn, tables)
        elif not conn.execute("select 1 from public.warehouses where code = %s",
                              (seed.COMPANIES[company]["warehouses"][0][0],)).fetchone():
            raise ValueError(f"This database already holds other data; company {company} needs its own database.")
        conn.execute((SQL_DIR / "02_copilot_views.sql").read_text())
        return not exists


def _json_value(v):
    """A database value as JSON: numbers stay numbers, dates and times become ISO text, an interval becomes its
    length in days, and a UUID or bytes become text."""
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return v.total_seconds() / 86400
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, memoryview)):
        return bytes(v).hex()
    return v


@dataclass
class Result:
    columns: list
    rows: list
    truncated: bool = False
    ms: float = 0.0
    error: str = ""


@dataclass
class ReadOnlyDB:
    uri: str
    max_rows: int = 200
    timeout_ms: int = 5000
    _conn: object = field(default=None, repr=False)

    def _connection(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.uri, autocommit=True)
        return self._conn

    def run(self, sql, timezone=None):
        """Run one already-checked SELECT. Returns rows as JSON-safe lists, or the database's error text.
        timezone, if given, is the session time zone for this transaction (evaluation fixes it; see dates.py)."""
        start = time.perf_counter()
        conn = self._connection()
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("set transaction read only")
                    cur.execute(f"set local statement_timeout = {int(self.timeout_ms)}")
                    if timezone:
                        cur.execute("select set_config('TimeZone', %s, true)", (timezone,))
                    cur.execute(sql)
                    columns = [d.name for d in cur.description] if cur.description else []
                    fetched = cur.fetchmany(self.max_rows + 1)
        except psycopg.Error as e:
            return Result([], [], ms=(time.perf_counter() - start) * 1000,
                          error=(e.diag.message_primary or str(e)).strip())
        rows = [[_json_value(v) for v in r] for r in fetched[: self.max_rows]]
        return Result(columns, rows, truncated=len(fetched) > self.max_rows, ms=(time.perf_counter() - start) * 1000)

    def user(self):
        return conninfo_to_dict(self.uri).get("user")
