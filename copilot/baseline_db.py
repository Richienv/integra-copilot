"""The naive baseline's own database role, baseline_reader (sql/03_baseline_reader.sql).

It can read Integra's base tables in schema public, personal data included, and nothing in the copilot schema.
It runs its SQL through the same ReadOnlyDB as the copilot (read-only transaction, 5-second statement timeout,
row cap). With no guard in front, a statement that smuggles in its own SET can lift the timeout, as for
copilot_reader without the guard (see sql/02_copilot_views.sql).
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
