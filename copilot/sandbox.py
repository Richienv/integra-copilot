"""The red-team sandbox: a throwaway database for replaying attacks without the L3 lock.

Attacks replayed without the read-only role (stacks L1 and L1+L2) run here, as the role redteam_sandbox:
a login role that is NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION and NOBYPASSRLS, has no session
defaults but its statement timeout, and is a member of no other role. Before executing anything,
assert_safe_role checks that neither the connected role nor any role it can SET ROLE to has one of those
powers or is a privileged built-in role, and refuses otherwise, so nothing replayed ever runs as a superuser.

The database is throwaway: redteam_sandbox owns its data, so an unguarded write really takes effect, inside a
transaction the harness rolls back after the result is captured. A statement that ends that transaction itself
(a smuggled COMMIT) makes its writes stick; the sandbox notices, and the harness rebuilds the database from the
same rows before the next attack. It can be loaded with poisoned demo data (canary strings in stored values)
for the indirect-injection attacks.
"""
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg import pq
from psycopg.conninfo import make_conninfo

from .db import _json_value

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"

SANDBOX_ROLE = "redteam_sandbox"
SANDBOX_DB = "redteam"


class SandboxError(RuntimeError):
    """Raised when the sandbox would run as a privileged role."""


def assert_safe_role(conn):
    """Refuse if the connected role, or any role it is a member of (and so can SET ROLE to), is a superuser,
    can create databases or roles, can replicate or bypass row security, or is a built-in pg_* role. The one
    built-in role allowed is pg_database_owner, which the owner of the throwaway database always is."""
    user = conn.execute("select current_user").fetchone()[0]
    if conn.execute("select current_setting('is_superuser')").fetchone()[0] == "on":
        raise SandboxError(f"refusing to run: {user} is a superuser")
    for name, superuser, creates, special in conn.execute(
            "select rolname, rolsuper, rolcreatedb or rolcreaterole, rolreplication or rolbypassrls from pg_roles "
            "where pg_has_role(current_user, oid, 'MEMBER') and (rolsuper or rolcreatedb or rolcreaterole "
            "or rolreplication or rolbypassrls or (rolname like 'pg\\_%' and rolname <> 'pg_database_owner')) "
            "order by rolsuper desc, rolname"):
        what = ("is a superuser" if superuser else "can create databases or roles" if creates
                else "can replicate or bypass row security" if special else "is a privileged built-in role")
        raise SandboxError(f"refusing to run: {user} {what}" if name == user else
                           f"refusing to run: {user} is a member of {name}, which {what}")


@dataclass
class SandboxResult:
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    truncated: bool = False
    ms: float = 0.0
    error: str = ""
    status: str = ""            # every statement's command tag, e.g. "SELECT 1; COMMIT; DELETE 572"
    persisted: bool = False     # the statement ended the harness transaction, so what it wrote was committed


@dataclass
class Sandbox:
    """Runs one recorded statement in the throwaway database and rolls it back, so writes are observed but
    do not persist. It is not read-only: that is deliberate, so the matrix can show an unguarded write
    taking effect. Like the copilot's own reader, it returns the first result set."""
    uri: str
    max_rows: int = 200
    timeout_ms: int = 5000
    rebuild: object = field(default=None, repr=False)      # recreates the database; set by create_sandbox
    dirty: bool = False                                     # a statement committed itself; reset() before reuse
    _conn: object = field(default=None, repr=False)

    def _connection(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.uri, autocommit=False)
        return self._conn

    def user(self):
        conn = self._connection()
        u = conn.execute("select current_user").fetchone()[0]
        conn.rollback()
        return u

    def reset(self):
        """Rebuild the throwaway database from the same rows after a statement made its writes stick."""
        if self._conn is not None:
            self._conn.close()
        if self.rebuild is None:
            raise SandboxError("the sandbox is dirty and has no rebuild function")
        self.rebuild()
        self.dirty = False

    def run(self, sql):
        """Run one recorded statement, capture its result, then roll back. Returns a SandboxResult."""
        if self.dirty:
            self.reset()
        conn = self._connection()
        assert_safe_role(conn)
        start = time.perf_counter()
        error, columns, fetched, tags = "", [], [], []
        try:
            with conn.cursor() as cur:
                cur.execute(f"set local statement_timeout = {int(self.timeout_ms)}")
                cur.execute("set local search_path = copilot")
                cur.execute(sql)
                columns = [d.name for d in cur.description] if cur.description else []
                fetched = cur.fetchmany(self.max_rows + 1) if cur.description else []
                tags.append(cur.statusmessage or "")
                while cur.nextset():
                    tags.append(cur.statusmessage or "")
        except psycopg.Error as e:
            error = (e.diag.message_primary or str(e)).strip()
        persisted = conn.info.transaction_status == pq.TransactionStatus.IDLE
        try:
            conn.rollback()
        except psycopg.Error:           # the statement killed its own connection; the next run reconnects
            conn.close()
        self.dirty = self.dirty or persisted
        ms = (time.perf_counter() - start) * 1000
        if error:
            return SandboxResult(ms=ms, error=error, persisted=persisted)
        rows = [[_json_value(v) for v in r] for r in fetched[: self.max_rows]]
        return SandboxResult(columns, rows, truncated=len(fetched) > self.max_rows, ms=ms,
                             status="; ".join(t for t in tags if t), persisted=persisted)


def demo_tables(anchor=None, poison=False):
    """The demo rows from data.seed, with the canaries of data.poison planted when poison is set."""
    sys.path.insert(0, str(ROOT))
    from data.seed import generate
    tables = generate(anchor)
    if poison:
        from data.poison import apply_poison
        tables = apply_poison(tables)
    return tables


def load_demo(admin_uri, tables):
    """Build a demo database from the given rows: like db.create_demo, but the rows may be poisoned.
    Only for a red-team run's own server; normal evaluation uses db.create_demo."""
    from data.seed import load
    with psycopg.connect(admin_uri, autocommit=True) as conn:
        conn.execute((SQL_DIR / "01_integra_subset.sql").read_text())
        load(conn, tables)
        conn.execute((SQL_DIR / "02_copilot_views.sql").read_text())


def _build(admin_uri, tables, dbname):
    """Drop and recreate the throwaway database, load it as redteam_sandbox, then add the copilot views.

    Data is loaded as redteam_sandbox so it owns the tables; the copilot views and reader role are created
    by the admin (creating a role needs a privilege redteam_sandbox does not have).
    """
    from data.seed import load
    with psycopg.connect(admin_uri, autocommit=True) as conn:
        conn.execute((SQL_DIR / "04_redteam_sandbox.sql").read_text())
        if conn.execute("select 1 from pg_database where datname = %s", (dbname,)).fetchone():
            conn.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                         "where datname = %s and pid <> pg_backend_pid()", (dbname,))
            conn.execute(f'drop database "{dbname}"')
        conn.execute(f'create database "{dbname}" owner {SANDBOX_ROLE}')

    admin_db_uri = make_conninfo(admin_uri, dbname=dbname)
    with psycopg.connect(admin_db_uri, autocommit=True) as conn:
        conn.execute(f"grant all on schema public to {SANDBOX_ROLE}")

    sandbox_uri = make_conninfo(admin_uri, user=SANDBOX_ROLE, dbname=dbname)
    with psycopg.connect(sandbox_uri, autocommit=True) as conn:
        assert_safe_role(conn)
        conn.execute((SQL_DIR / "01_integra_subset.sql").read_text())
        load(conn, tables)

    with psycopg.connect(admin_db_uri, autocommit=True) as conn:
        conn.execute((SQL_DIR / "02_copilot_views.sql").read_text())
        conn.execute(f"grant usage on schema copilot to {SANDBOX_ROLE}")
        conn.execute(f"grant select on all tables in schema copilot to {SANDBOX_ROLE}")
    return sandbox_uri


def create_sandbox(admin_uri, poison=True, anchor=None, dbname=SANDBOX_DB):
    """Create (or rebuild) the throwaway database and return a Sandbox connected to it as redteam_sandbox."""
    tables = demo_tables(anchor, poison)
    return Sandbox(_build(admin_uri, tables, dbname), rebuild=lambda: _build(admin_uri, tables, dbname))
