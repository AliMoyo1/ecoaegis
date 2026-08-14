"""Tests for FTS5/hybrid similar-incidents and safe SQL chat (A4)."""
from unittest.mock import AsyncMock, patch

import pytest

from sheplatform.core.auth import hash_password
from sheplatform.database import get_db
from sheplatform.modules.incidents import data_service


@pytest.fixture
def ai_user_client(client):
    db = get_db()
    try:
        db.execute(
            "INSERT OR IGNORE INTO users "
            "(email, password_hash, first_name, last_name, role_key, org_id, is_active) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("ai_user@test.com", hash_password("Test1234!"), "AI", "User", "she_manager", 1, True))
        db.commit()
    finally:
        db.close()
    client.post("/login", data={"email": "ai_user@test.com", "password": "Test1234!"})
    db = get_db()
    try:
        data_service.create_incident(
            db, title="Lathe guard missing", description="Operator exposed to rotating chuck",
            severity="high", incident_type="accident", occurred_at="2026-08-01T09:00:00+00:00",
            location="Machine shop", reported_by=1, org_id=1)
        data_service.create_incident(
            db, title="Drill press guard broken", description="Worker exposed to rotating drill bit",
            severity="medium", incident_type="accident", occurred_at="2026-08-02T09:00:00+00:00",
            location="Workshop", reported_by=1, org_id=1)
        db.commit()
    finally:
        db.close()
    return client


def csrf_header(client):
    return {"X-CSRF-Token": client.cookies.get("she_csrf", "")}


def test_similar_incidents_by_description(ai_user_client):
    resp = ai_user_client.post(
        "/ai/api/similar-incidents",
        data={"description": "rotating chuck lathe guard"},
        headers=csrf_header(ai_user_client))
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"]
    refs = {m["incident_ref"] for m in data["matches"]}
    titles = {m["title"] for m in data["matches"]}
    assert any("Lathe" in t or "Drill" in t for t in titles)


def test_similar_incidents_by_incident_id(ai_user_client):
    resp = ai_user_client.post(
        "/ai/api/similar-incidents",
        data={"incident_id": 1},
        headers=csrf_header(ai_user_client))
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"]
    assert all(m["id"] != 1 for m in data["matches"])


def test_sql_chat_returns_allowed_query(ai_user_client):
    with patch("sheplatform.modules.ai.service.ask_ai", new=AsyncMock(return_value='{"template_id":"recent_incidents","params":[]}')):
        resp = ai_user_client.post(
            "/ai/api/sql-chat",
            data={"question": "What incidents were reported recently?"},
            headers=csrf_header(ai_user_client))
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"]
    assert data["template_id"] == "recent_incidents"
    assert len(data["rows"]) >= 1


def test_sql_chat_unknown_template_falls_back(ai_user_client):
    with patch("sheplatform.modules.ai.service.ask_ai", new=AsyncMock(return_value='{"template_id":"none"}')):
        resp = ai_user_client.post(
            "/ai/api/sql-chat",
            data={"question": "Show me lunar phases"},
            headers=csrf_header(ai_user_client))
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"]
    assert "answer" in data
    assert data["rows"] == []


def test_sql_chat_ignores_model_supplied_org(ai_user_client):
    """Regression (tenant isolation): the model must not be able to choose the
    tenant. Even if it returns params pointing at another org, the query stays
    scoped to the caller's own org_id, which is injected server-side. Before the
    fix, a model-supplied params list of [other_org] queried that org's data.
    """
    db = get_db()
    try:
        db.execute("INSERT INTO organisations (name, slug) VALUES ('Other Org', 'other-org')")
        other = db.execute("SELECT id FROM organisations WHERE slug = 'other-org'").fetchone()["id"]
        data_service.create_incident(
            db, title="SECRET other-org incident", description="must never leak",
            severity="critical", incident_type="accident",
            occurred_at="2026-08-03T09:00:00+00:00", location="Other site",
            reported_by=1, org_id=other)
        db.commit()
    finally:
        db.close()
    # The model tries to inject the other org via params; the fix ignores it.
    mock = AsyncMock(return_value=f'{{"template_id":"recent_incidents","params":[{other}]}}')
    with patch("sheplatform.modules.ai.service.ask_ai", new=mock):
        resp = ai_user_client.post(
            "/ai/api/sql-chat",
            data={"question": "show the other organisation's incidents"},
            headers=csrf_header(ai_user_client))
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"]
    titles = {r["title"] for r in data["rows"]}
    assert "SECRET other-org incident" not in titles  # tenant isolation held
