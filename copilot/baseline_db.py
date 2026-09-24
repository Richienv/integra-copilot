"""The naive baseline's own database role, baseline_reader (sql/03_baseline_reader.sql).

It can read Integra's base tables in schema public, personal data included, and nothing in the copilot schema.
Like copilot_reader, every transaction is read-only and every statement stops after 5 seconds; the baseline
also runs its SQL through the same ReadOnlyDB (read-only transaction, statement timeout, row cap).
For the fictional demo database only.
"""
import psycopg
from psycopg.conninfo import make_conninfo

from .db import SQL_DIR

ROLE = "baseline_reader"


def create_role(admin_uri):
    """Create (or refresh) baseline_reader on a database made by db.create_demo. Returns the role's URI."""
    with psycopg.connect(admin_uri, autocommit=True) as conn:
        conn.execute((SQL_DIR / "03_baseline_reader.sql").read_text())
    return baseline_uri(admin_uri)


def baseline_uri(uri):
    """The same server, logged in as baseline_reader. Like db.reader_uri, this works for a local socket."""
    return make_conninfo(uri, user=ROLE)
