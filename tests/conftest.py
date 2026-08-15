"""Test fixtures.

Default: a fresh SQLite database per test (guide 26 conftest pattern).

When ``TEST_DATABASE_URL`` is set, the SAME fixtures run against a real
PostgreSQL instance instead: the schema is built once per session, and every
table is truncated (identities reset) before each test. This lets the whole
suite exercise the production backend, not just SQLite. To run:

    docker run -d -e POSTGRES_PASSWORD=x -e POSTGRES_DB=y -p 55432:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql://postgres:x@127.0.0.1:55432/y \
        .venv/Scripts/python.exe -m pytest
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sheplatform.main import app

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
_USE_PG = bool(TEST_DATABASE_URL)


def _seed_org(db) -> None:
    db.execute(
        "INSERT INTO organisations (name, slug) VALUES ('Test Org', 'test-org') "
        "ON CONFLICT DO NOTHING")
    db.commit()


def _pg_reset(db) -> None:
    """Empty every table and reset identity sequences for a clean test slate."""
    tables = [r[0] for r in db.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()]
    if tables:
        quoted = ", ".join(f'"{t}"' for t in tables)
        db.execute(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE")
        db.commit()


@pytest.fixture(scope="session", autouse=True)
def _pg_session():
    """Build the schema once against real PostgreSQL and dispose the pool at the
    end. No-op in the default SQLite mode."""
    if not _USE_PG:
        yield
        return
    from sheplatform import database
    from sheplatform.config import settings

    settings.DATABASE_URL = TEST_DATABASE_URL
    with database._connect() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.commit()
    database.init_db()
    yield
    pool = getattr(database._local, "pg_pool", None)
    if pool is not None:
        pool.closeall()
        database._local.pg_pool = None


def _prepare_backend(tmp_path, monkeypatch):
    """Point get_db() at the right backend, empty + seed it, and return an open
    connection. PostgreSQL truncates the shared schema; SQLite builds a fresh
    per-test file."""
    monkeypatch.setattr("sheplatform.config.settings.DEBUG", True)
    monkeypatch.setattr("sheplatform.config.settings.SECRET_KEY", "test-secret")

    from sheplatform.core import event_handlers  # noqa: F401  (register handlers)
    from sheplatform.database import get_db, init_db

    if _USE_PG:
        conn = get_db()
        _pg_reset(conn)
        _seed_org(conn)
        return conn

    monkeypatch.setattr("sheplatform.config.settings.DATABASE_URL", "")
    monkeypatch.setattr("sheplatform.config.settings.DB_PATH", str(tmp_path / "test.db"))
    init_db()
    conn = get_db()
    _seed_org(conn)
    return conn


@pytest.fixture
def client(tmp_path, monkeypatch):
    """HTTP TestClient with a fresh per-test database (SQLite file, or truncated
    PostgreSQL schema when TEST_DATABASE_URL is set)."""
    conn = _prepare_backend(tmp_path, monkeypatch)
    conn.close()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A per-test database connection (SQLite file, or truncated PostgreSQL
    schema when TEST_DATABASE_URL is set)."""
    conn = _prepare_backend(tmp_path, monkeypatch)
    yield conn
    if _USE_PG:
        # Return the connection to the pool so 404 tests don't exhaust it.
        try:
            conn.close()
        except Exception:
            pass


@pytest.fixture
def org_id(db) -> int:
    """The seeded organisation's id, for test helpers that create users."""
    return db.execute("SELECT id FROM organisations LIMIT 1").fetchone()["id"]


@pytest.fixture
def she_manager(db):
    """SHE Manager user."""
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        ("manager@test.com", hash_password("Test1234!"), "Test", "Manager", "she_manager", 1),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE email = 'manager@test.com'").fetchone()
    return dict(row)


@pytest.fixture
def she_officer(db):
    """SHE Officer user."""
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        ("officer@test.com", hash_password("Test1234!"), "Test", "Officer", "she_officer", 1),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE email = 'officer@test.com'").fetchone()
    return dict(row)


@pytest.fixture
def board_chair(db):
    """Board Chair user."""
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        ("chair@test.com", hash_password("Test1234!"), "Test", "Chair", "board_chair", 1),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE email = 'chair@test.com'").fetchone()
    return dict(row)
