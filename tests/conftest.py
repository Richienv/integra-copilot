import datetime as dt
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from copilot import db  # noqa: E402

ANCHOR = dt.date(2026, 9, 24)


@pytest.fixture(scope="session")
def admin_uri():
    uri = db.local_server(tempfile.mkdtemp(prefix="copilot-test-"), cleanup_mode="delete")
    db.create_demo(uri, anchor=ANCHOR)
    return uri


@pytest.fixture(scope="session")
def reader(admin_uri):
    return db.ReadOnlyDB(db.reader_uri(admin_uri))
