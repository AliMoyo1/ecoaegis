"""Tests for AI auto-classification on incident intake."""
from unittest.mock import patch

import pytest

from sheplatform.core.auth import hash_password
from sheplatform.database import get_db


@pytest.fixture
def reporter_client(client):
    """Client logged in as employee with incident.create capability."""
    db = get_db()
    try:
        db.execute(
            "INSERT INTO users "
            "(email, password_hash, first_name, last_name, role_key, org_id, is_active) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            ("rep@test.com", hash_password("Test1234!"), "Test", "Reporter", "employee", 1, True),
        )
        db.commit()
    finally:
        db.close()
    client.post("/login", data={"email": "rep@test.com", "password": "Test1234!"})
    return client


def csrf_header(client):
    token = client.cookies.get("she_csrf", "")
    return {"X-CSRF-Token": token}


@patch("sheplatform.modules.ai.service.ask_ai", return_value='{"title":"Forklift near miss","severity":"high","incident_type":"near_miss","summary":"Forklift almost struck a pedestrian in aisle 3."}')
def test_classify_incident_endpoint(mock_ai, reporter_client):
    resp = reporter_client.post("/ai/api/classify-incident",
                                data={"description": "Forklift nearly hit someone in aisle 3"},
                                headers=csrf_header(reporter_client))
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["suggestion"]["severity"] == "high"
    assert data["suggestion"]["incident_type"] == "near_miss"


@patch("sheplatform.modules.ai.service.ask_ai", return_value='{"title":"Forklift near miss","severity":"high","incident_type":"near_miss","summary":"Forklift almost struck a pedestrian in aisle 3."}')
def test_create_incident_with_ai_classification(mock_ai, reporter_client):
    resp = reporter_client.post("/incidents/api/create", data={
        "title": "Placeholder",
        "description": "Forklift nearly hit someone in aisle 3",
        "severity": "low",
        "incident_type": "accident",
        "ai_classify": "true",
        "accept_ai": "true",
    }, headers=csrf_header(reporter_client))
    assert resp.status_code == 201
    data = resp.json()
    assert data["incident"]["severity"] == "high"
    assert data["incident"]["incident_type"] == "near_miss"
    assert "ai_metadata" in data["incident"]


@patch("sheplatform.modules.ai.service.ask_ai", return_value='{"title":"Forklift near miss","severity":"high","incident_type":"near_miss","summary":"Forklift almost struck a pedestrian in aisle 3."}')
def test_create_incident_ai_suggestion_not_accepted(mock_ai, reporter_client):
    resp = reporter_client.post("/incidents/api/create", data={
        "title": "Placeholder",
        "description": "Forklift nearly hit someone in aisle 3",
        "severity": "low",
        "incident_type": "accident",
        "ai_classify": "true",
    }, headers=csrf_header(reporter_client))
    assert resp.status_code == 201
    data = resp.json()
    # User did not accept AI, so original values remain
    assert data["incident"]["severity"] == "low"
    assert data["incident"]["incident_type"] == "accident"
    # But metadata still records the suggestion
    assert "ai_metadata" in data["incident"]
