"""Tests for offline sync and idempotency (B1)."""
from __future__ import annotations

import pytest


@pytest.fixture
def logged_in_client(client, she_officer):
    resp = client.post(
        "/login", data={"email": "officer@test.com", "password": "Test1234!"}, follow_redirects=False
    )
    assert resp.status_code in (302, 303), resp.text
    csrf = client.cookies.get("she_csrf")
    if csrf:
        client.headers["X-CSRF-Token"] = csrf
    return client


def test_offline_sync_creates_incident(logged_in_client):
    payload = [
        {
            "type": "incident",
            "idempotencyKey": "offline-inc-001",
            "data": {
                "title": "Offline incident",
                "description": "Captured with no signal",
                "severity": "high",
                "incident_type": "accident",
                "location": "Warehouse A",
                "occurred_at": "2026-08-14T10:00:00",
            },
        }
    ]
    resp = logged_in_client.post("/api/offline-sync", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["processed"] == 1
    assert data["results"][0]["ok"] is True
    assert data["results"][0]["ref"].startswith("INC-")

    # Replay with same idempotency key must return existing record, not duplicate
    resp2 = logged_in_client.post("/api/offline-sync", json=payload)
    data2 = resp2.json()
    assert data2["results"][0]["idempotent"] is True
    assert data2["results"][0]["id"] == data["results"][0]["id"]


def test_offline_sync_creates_observation(logged_in_client):
    payload = [
        {
            "type": "observation",
            "idempotencyKey": "offline-obs-001",
            "data": {
                "obs_type": "hazard",
                "title": "Loose cable",
                "description": "Cable across walkway",
                "location": "Office 2B",
                "severity": "medium",
            },
        }
    ]
    resp = logged_in_client.post("/api/offline-sync", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["results"][0]["ref"].startswith("OBS-")


def test_offline_sync_rejects_unsupported_type(logged_in_client):
    resp = logged_in_client.post("/api/offline-sync", json=[{"type": "unknown", "data": {}}])
    assert resp.status_code == 200
    assert resp.json()["results"][0]["ok"] is False


def test_offline_sync_requires_auth(client):
    # Provide matching CSRF cookie + header so middleware lets us reach auth guard
    client.cookies.set("she_csrf", "test-csrf")
    client.headers["X-CSRF-Token"] = "test-csrf"
    resp = client.post("/api/offline-sync", json=[], follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/login" in (resp.headers.get("location") or "")
