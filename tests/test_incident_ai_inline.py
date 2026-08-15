"""Tests for inline incident AI detail page and AI routes."""
from unittest.mock import patch

import pytest

from sheplatform.core.auth import hash_password
from sheplatform.database import get_db
from sheplatform.modules.incidents import data_service


@pytest.fixture
def investigator_client(client):
    """Client logged in as a SHE manager with incident.investigate."""
    db = get_db()
    try:
        db.execute(
            "INSERT INTO users "
            "(email, password_hash, first_name, last_name, role_key, org_id, is_active) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            ("mgr@test.com", hash_password("Test1234!"), "Test", "Manager", "she_manager", 1, True),
        )
        db.commit()
    finally:
        db.close()
    client.post("/login", data={"email": "mgr@test.com", "password": "Test1234!"})
    # Seed one incident for this org.
    db = get_db()
    try:
        data_service.create_incident(
            db, title="Lathe guard missing", description="Operator exposed to rotating chuck",
            severity="high", incident_type="accident", occurred_at="2026-08-01T09:00:00+00:00",
            location="Machine shop", reported_by=1, org_id=1)
        db.commit()
    finally:
        db.close()
    return client


def csrf_header(client):
    token = client.cookies.get("she_csrf", "")
    return {"X-CSRF-Token": token}


def test_incident_detail_page_renders(investigator_client):
    resp = investigator_client.get("/incidents/1")
    assert resp.status_code == 200
    assert b"AI: investigation help" in resp.content
    assert b"AI: root cause" in resp.content
    assert b"AI: draft corrective actions" in resp.content


def test_incident_detail_page_404_redirects(investigator_client):
    resp = investigator_client.get("/incidents/99999", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/incidents"


def test_api_incident_detail_includes_timeline(investigator_client):
    investigator_client.post(
        "/incidents/api/1/timeline",
        data={"event_text": "worker reported dizziness"},
        headers=csrf_header(investigator_client))
    resp = investigator_client.get("/incidents/api/1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["incident"]["id"] == 1
    assert any("worker reported dizziness" in t["event_text"] for t in data["incident"]["timeline"])


@patch("sheplatform.modules.ai.service.ask_ai", return_value="1. What PPE was worn?")
def test_incident_copilot_endpoint(mock_ai, investigator_client):
    resp = investigator_client.post("/ai/api/incident-copilot/1", headers=csrf_header(investigator_client))
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "What PPE was worn" in data["result"]


@patch("sheplatform.modules.ai.service.ask_ai", return_value="Why 1: machine was unguarded.")
def test_root_cause_endpoint(mock_ai, investigator_client):
    resp = investigator_client.post("/ai/api/root-cause/1", headers=csrf_header(investigator_client))
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "unguarded" in data["result"]


@patch("sheplatform.modules.ai.service.ask_ai", return_value='[{"title":"Fix guard","description":"Install interlock","type":"corrective","suggested_role":"SHE Officer","due_in_days":7}]')
def test_draft_actions_endpoint(mock_ai, investigator_client):
    resp = investigator_client.post("/ai/api/draft-actions/1", headers=csrf_header(investigator_client))
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert len(data["draft_actions"]) == 1
    assert data["draft_actions"][0]["title"] == "Fix guard"


def test_incident_copilot_cross_org_not_found(investigator_client):
    resp = investigator_client.post(
        "/incidents/api/create",
        data={"title": "cross-org test", "description": "x", "severity": "low", "incident_type": "near_miss"},
        headers=csrf_header(investigator_client))
    assert resp.status_code == 201
    inc_id = resp.json()["incident"]["id"]

    db = get_db()
    try:
        db.execute("INSERT INTO organisations (name, slug) VALUES ('Org 2', 'org-2') ON CONFLICT DO NOTHING")
        db.execute("UPDATE users SET org_id = 2 WHERE email = 'mgr@test.com'")
        db.commit()
        resp = investigator_client.post(f"/ai/api/incident-copilot/{inc_id}", headers=csrf_header(investigator_client))
        assert resp.status_code == 404
    finally:
        db.execute("UPDATE users SET org_id = 1 WHERE email = 'mgr@test.com'")
        db.commit()
        db.close()
