"""CSRF middleware tests (audit fix: validate_csrf was dead code).

Self-contained: uses FastAPI TestClient (in-process), no live server needed.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(db):
    """TestClient bound to the fixture's fresh DB (same DB_PATH via settings)."""
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        ("officer@test.com", hash_password("Test1234!"), "T", "O", "she_officer", 1),
    )
    db.commit()
    from sheplatform.main import app
    return TestClient(app)


def _login(client) -> str | None:
    """Login via the real form; returns the CSRF cookie value ('' if absent)."""
    resp = client.post("/login", data={"email": "officer@test.com", "password": "Test1234!"})
    assert resp.status_code in (200, 303), f"login failed: {resp.status_code}"
    return client.cookies.get("she_csrf", "")


def _post(client, url, data=None, headers=None):
    return client.post(url, data=data or {}, headers=headers or {})


class TestCSRFMiddleware:
    def test_post_without_token_blocked(self, client):
        _login(client)
        resp = _post(client, "/incidents/api/create",
                     {"title": "x", "description": "d", "severity": "low",
                      "incident_type": "accident", "occurred_at": "2026-08-13T10:00:00"})
        assert resp.status_code == 403

    def test_post_with_token_allowed(self, client):
        token = _login(client)
        assert token, "CSRF cookie should be set after login"
        resp = _post(client, "/incidents/api/create",
                     {"title": "CSRF test incident", "description": "d",
                      "severity": "low", "incident_type": "accident",
                      "occurred_at": "2026-08-13T10:00:00"},
                     {"X-CSRF-Token": token})
        assert resp.status_code in (200, 201), f"expected 200/201, got {resp.status_code}"

    def test_form_field_token_allowed(self, client):
        """Server-rendered forms (logout) send csrf_token as a form field."""
        token = _login(client)
        resp = _post(client, "/logout", {"csrf_token": token})
        assert resp.status_code in (200, 303)

    def test_wrong_token_blocked(self, client):
        _login(client)
        resp = _post(client, "/incidents/api/create",
                     {"title": "x", "description": "d", "severity": "low",
                      "incident_type": "accident", "occurred_at": "2026-08-13T10:00:00"},
                     {"X-CSRF-Token": "wrong-token-value"})
        assert resp.status_code == 403
