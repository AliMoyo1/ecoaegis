"""Shared test fixtures for HTTP routes."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sheplatform.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """HTTP TestClient with a fresh SQLite DB per test."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("sheplatform.config.settings.DATABASE_URL", "")
    monkeypatch.setattr("sheplatform.config.settings.DB_PATH", db_path)
    monkeypatch.setattr("sheplatform.config.settings.DEBUG", True)
    monkeypatch.setattr("sheplatform.config.settings.SECRET_KEY", "test-secret")

    from sheplatform.core import event_handlers  # noqa: F401
    from sheplatform.database import init_db

    init_db()
    db = __import__("sheplatform.database", fromlist=["get_db"]).get_db()
    db.execute("INSERT INTO organisations (name, slug) VALUES ('Test Org', 'test-org')")
    db.commit()
    db.close()

    with TestClient(app) as c:
        yield c
