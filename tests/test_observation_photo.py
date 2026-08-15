"""Tests for photo hazard classification (A3)."""
import io
from unittest.mock import AsyncMock, patch

import pytest

from sheplatform.core.auth import hash_password
from sheplatform.database import get_db


SAMPLE_PHOTO = (b"\xff\xd8\xff" + b"0" * 512)  # fake JPEG magic bytes


def csrf_header(client):
    return {"X-CSRF-Token": client.cookies.get("she_csrf", "")}


@pytest.fixture
def reporter_client(client):
    db = get_db()
    try:
        db.execute(
            "INSERT INTO organisations (id, name, slug) VALUES (1, 'Omni', 'omni') ON CONFLICT DO NOTHING")
        pw = hash_password("password")
        db.execute(
            "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
            "VALUES ('reporter@test.com', %s, 'Re', 'Porter', 'employee', 1) "
            "ON CONFLICT(email) DO UPDATE SET password_hash=excluded.password_hash",
            (pw,))
        db.commit()
    finally:
        db.close()
    resp = client.post("/login", data={"email": "reporter@test.com", "password": "password"},
                       follow_redirects=False)
    assert resp.status_code == 303
    return client


def test_from_photo_vision_classifies_and_creates_observation(reporter_client):
    with patch("sheplatform.modules.observations.routes.classify_photo",
               new=AsyncMock(return_value={
                   "title": "Missing guard rail",
                   "obs_type": "hazard",
                   "severity": "high",
                   "description": "Worker near unguarded edge.",
                   "controls": ["Install guard rail"],
                   "confidence": 0.9,
                   "raw_text": "...",
                   "data_uri": "data:image/jpeg;base64,xxx",
               })):
        resp = reporter_client.post(
            "/observations/api/from-photo",
            data={"location": "Warehouse A"},
            files={"file": ("hazard.jpg", io.BytesIO(SAMPLE_PHOTO), "image/jpeg")},
            headers=csrf_header(reporter_client))
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"]
    obs = data["observation"]
    assert obs["obs_type"] == "hazard"
    assert obs["severity"] == "high"
    assert obs["title"] == "Missing guard rail"
    assert obs["location"] == "Warehouse A"
    assert "vision" in obs["ai_metadata"]


def test_from_photo_rejects_non_image(reporter_client):
    resp = reporter_client.post(
        "/observations/api/from-photo",
        data={"location": "Warehouse A"},
        files={"file": ("bad.txt", io.BytesIO(b"not an image"), "text/plain")},
        headers=csrf_header(reporter_client))
    assert resp.status_code == 400


def test_from_photo_requires_login(client):
    resp = client.post(
        "/observations/api/from-photo",
        data={"location": "Warehouse A"},
        files={"file": ("hazard.jpg", io.BytesIO(SAMPLE_PHOTO), "image/jpeg")})
    assert resp.status_code == 403
