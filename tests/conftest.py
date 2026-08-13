"""Test fixtures: fresh SQLite DB per test (guide 26 conftest pattern)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Fresh SQLite database per test."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("sheplatform.config.settings.DATABASE_URL", "")
    monkeypatch.setattr("sheplatform.config.settings.DB_PATH", db_path)
    monkeypatch.setattr("sheplatform.config.settings.DEBUG", True)
    monkeypatch.setattr("sheplatform.config.settings.SECRET_KEY", "test-secret")

    # Register event handlers before any test emits (guide 21)
    from sheplatform.core import event_handlers  # noqa: F401

    from sheplatform.database import init_db, get_db
    init_db()
    return get_db()


@pytest.fixture
def she_manager(db):
    """SHE Manager user."""
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key) "
        "VALUES (%s, %s, %s, %s, %s)",
        ("manager@test.com", hash_password("Test1234!"), "Test", "Manager", "she_manager"),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE email = 'manager@test.com'").fetchone()
    return dict(row)


@pytest.fixture
def she_officer(db):
    """SHE Officer user."""
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key) "
        "VALUES (%s, %s, %s, %s, %s)",
        ("officer@test.com", hash_password("Test1234!"), "Test", "Officer", "she_officer"),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE email = 'officer@test.com'").fetchone()
    return dict(row)


@pytest.fixture
def board_chair(db):
    """Board Chair user."""
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key) "
        "VALUES (%s, %s, %s, %s, %s)",
        ("chair@test.com", hash_password("Test1234!"), "Test", "Chair", "board_chair"),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE email = 'chair@test.com'").fetchone()
    return dict(row)
