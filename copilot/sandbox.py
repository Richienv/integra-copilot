"""The red-team sandbox: a throwaway database for replaying attacks without the L3 lock.

Attacks replayed without the read-only role (stacks L1 and L1+L2) run here, as the role redteam_sandbox:
a login role that is NOSUPERUSER, NOCREATEDB, NOCREATEROLE and not a member of pg_execute_server_program,
pg_read_server_files, pg_write_server_files or pg_signal_backend. Before executing anything, assert_safe_role
checks the connected role is not a superuser and has none of those memberships, and refuses otherwise, so
nothing replayed ever runs as the postgres superuser.

The database is throwaway: redteam_sandbox owns its data, so an unguarded write really takes effect (and is
rolled back by the harness after it is classified). It can be loaded with poisoned demo data (canary strings
in stored values) for the indirect-injection attacks.
"""
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo

from .db import _json_value

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"

SANDBOX_ROLE = "redteam_sandbox"
SANDBOX_DB = "redteam"
DANGEROUS_ROLES = ("pg_execute_server_program", "pg_read_server_files", "pg_write_server_files",
                   "pg_signal_backend")


class SandboxError(RuntimeError):
    """Raised when the sandbox would run as a privileged role."""


def assert_safe_role(conn):
    """Refuse if the connected role is a superuser or a member of a privileged built-in role."""
    if conn.execute("select current_setting('is_superuser')").fetchone()[0] == "on":
        raise SandboxError(f"refusing to run: {conn.execute('select current_user').fetchone()[0]} is a superuser")
    bad = conn.execute("select rolname from pg_roles where rolname = any(%s) "
                       "and pg_has_role(current_user, oid, 'MEMBER')",
                       (list(DANGEROUS_ROLES),)).fetchall()
    if bad:
        raise SandboxError(f"refusing to run: current role is a member of {[b[0] for b in bad]}")


@dataclass
class SandboxResult:
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    truncated: bool = False
    ms: float = 0.0
    error: str = ""
    status: str = ""            # the command tag, e.g. "DELETE 5" or "SELECT 10"


@dataclass
class Sandbox:
    """Runs one recorded statement in the throwaway database and rolls it back, so writes are observed but
    do not persist. It is not read-only: that is deliberate, so the matrix can show an unguarded write
    taking effect."""
    uri: str
    max_rows: int = 200
    timeout_ms: int = 5000
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

    def run(self, sql):
        """Run one recorded statement, capture its result, then roll back. Returns a SandboxResult."""
        conn = self._connection()
        assert_safe_role(conn)
        start = time.perf_counter()
        try:
            with conn.cursor() as cur:
                cur.execute(f"set local statement_timeout = {int(self.timeout_ms)}")
                cur.execute("set local search_path = copilot")
                cur.execute(sql)
                status = cur.statusmessage or ""
                columns = [d.name for d in cur.description] if cur.description else []
                fetched = cur.fetchmany(self.max_rows + 1) if cur.description else []
            conn.rollback()
        except psycopg.Error as e:
            conn.rollback()
            return SandboxResult(ms=(time.perf_counter() - start) * 1000,
                                 error=(e.diag.message_primary or str(e)).strip())
        rows = [[_json_value(v) for v in r] for r in fetched[: self.max_rows]]
        return SandboxResult(columns, rows, truncated=len(fetched) > self.max_rows,
                             ms=(time.perf_counter() - start) * 1000, status=status)


def _generate(anchor, poison):
    sys.path.insert(0, str(ROOT))
    from data.seed import generate
    tables = generate(anchor)
    if poison:
        from data.poison import apply_poison
        tables = apply_poison(tables)
    return tables


def create_sandbox(admin_uri, poison=True, anchor=None, dbname=SANDBOX_DB, fresh=True):
    """Create (or rebuild) the throwaway database and return a Sandbox connected to it as redteam_sandbox.

    Data is loaded as redteam_sandbox so it owns the tables; the copilot views and reader role are created
    by the admin (creating a role needs a privilege redteam_sandbox does not have).
    """
    from data.seed import load
    tables = _generate(anchor, poison)

    with psycopg.connect(admin_uri, autocommit=True) as conn:
        conn.execute((SQL_DIR / "04_redteam_sandbox.sql").read_text())
        exists = conn.execute("select 1 from pg_database where datname = %s", (dbname,)).fetchone()
        if exists and fresh:
            conn.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                         "where datname = %s and pid <> pg_backend_pid()", (dbname,))
            conn.execute(f'drop database "{dbname}"')
            exists = None
        if not exists:
            conn.execute(f'create database "{dbname}" owner {SANDBOX_ROLE}')

    admin_db_uri = make_conninfo(admin_uri, dbname=dbname)
    with psycopg.connect(admin_db_uri, autocommit=True) as conn:
        conn.execute(f"grant all on schema public to {SANDBOX_ROLE}")

    sandbox_uri = make_conninfo(admin_uri, user=SANDBOX_ROLE, dbname=dbname)
    with psycopg.connect(sandbox_uri, autocommit=True) as conn:
        if not conn.execute("select to_regclass('public.invoices') is not null").fetchone()[0]:
            conn.execute((SQL_DIR / "01_integra_subset.sql").read_text())
            load(conn, tables)

    with psycopg.connect(admin_db_uri, autocommit=True) as conn:
        conn.execute((SQL_DIR / "02_copilot_views.sql").read_text())
        conn.execute(f"grant usage on schema copilot to {SANDBOX_ROLE}")
        conn.execute(f"grant select on all tables in schema copilot to {SANDBOX_ROLE}")

    return Sandbox(sandbox_uri)
