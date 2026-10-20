"""Shared fixtures. Integration tests need a disposable Postgres database:

    export TEST_DATABASE_URL=postgresql://postgres@localhost:5432/bluff_test

They are skipped when TEST_DATABASE_URL is not set. NEVER point this at your
production Supabase database: tests truncate every game table.
"""
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bluff import service  # noqa: E402
from bluff.db import Database, apply_migrations  # noqa: E402

TEST_DSN = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def db():
    if not TEST_DSN:
        pytest.skip("TEST_DATABASE_URL not set; skipping database integration tests")
    apply_migrations(TEST_DSN)
    database = Database(TEST_DSN, min_size=1, max_size=10)
    yield database
    database.close()


@pytest.fixture(autouse=True)
def clean(request):
    if "db" in request.fixturenames:
        database = request.getfixturevalue("db")
        with database.tx() as conn:
            conn.execute("truncate bluff.rooms, bluff.event_log, bluff.private_log cascade")
    yield


@pytest.fixture
def room4(db):
    """A lobby with four joined (not yet ready) players. Returns (code, [tokens by seat])."""
    code, t1 = service.create_room(db, "Ada")
    tokens = [t1] + [service.join_room(db, code, n) for n in ("Bo", "Cy", "Di")]
    return code, tokens


@pytest.fixture
def game(db, room4):
    """A started game. Returns (code, [tokens by seat])."""
    code, tokens = room4
    for t in tokens:
        service.set_ready(db, code, t, True)
    return code, tokens


def rid() -> str:
    return str(uuid.uuid4())
