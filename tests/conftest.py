import datetime as dt
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from copilot import db  # noqa: E402

ANCHOR = dt.date(2026, 9, 24)
LATE = dt.date(2026, 10, 15)      # mid-month, so "this month" spans two weeks; company B is frozen at this date


@pytest.fixture(scope="session")
def admin_uri():
    uri = db.local_server(tempfile.mkdtemp(prefix="copilot-test-"), cleanup_mode="delete")
    db.create_demo(uri, anchor=ANCHOR)
    return uri


@pytest.fixture(scope="session")
def reader(admin_uri):
    return db.ReadOnlyDB(db.reader_uri(admin_uri))


@pytest.fixture(scope="session")
def company_b_uri(admin_uri):
    """Company B at LATE, in its own database on the same server."""
    uri = db.database(admin_uri, "company_b_20261015")
    db.create_demo(uri, anchor=LATE, company="B")
    return uri


@pytest.fixture(scope="session")
def company_b_reader(company_b_uri):
    return db.ReadOnlyDB(db.reader_uri(company_b_uri))


@pytest.fixture(params=["A", "B"])
def any_reader(request):
    """Company A's reader, then company B's: the locks must hold on both."""
    return request.getfixturevalue("reader" if request.param == "A" else "company_b_reader")
