"""Statutory report assembly tests (B4).

Uses the `client` fixture from conftest_http.py, which sets up a fresh temp DB,
runs init_db() via the app startup event, and seeds a 'Test Org'. The previous
version created a module-level TestClient(app) with no context manager, so the
startup event never fired and the schema was never created ("no such table:
organisations"); these tests therefore never actually exercised the feature.
"""
from __future__ import annotations

import json

from sheplatform.core.auth import hash_password
from sheplatform.database import get_db


def _seed_user(role_key="she_manager"):
    """Add the test user to the org the client fixture already seeded."""
    db = get_db()
    try:
        org = db.execute("SELECT id FROM organisations WHERE slug = %s", ("test-org",)).fetchone()
        existing = db.execute("SELECT id FROM users WHERE email = %s", ("sr_user@example.com",)).fetchone()
        if existing:
            db.execute("UPDATE users SET password_hash = %s, role_key = %s, org_id = %s WHERE id = %s",
                       (hash_password("Password123!"), role_key, org["id"], existing["id"]))
        else:
            db.execute(
                "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                ("sr_user@example.com", hash_password("Password123!"), "SR", "User", role_key, org["id"]))
        db.commit()
    finally:
        db.close()


def _login(client, role_key="she_manager"):
    _seed_user(role_key)
    client.get("/login")
    csrf = client.cookies.get("she_csrf") or ""
    client.post("/login", data={"email": "sr_user@example.com", "password": "Password123!"},
                headers={"X-CSRF-Token": csrf})
    return client.cookies.get("she_csrf") or ""


def test_templates_seeded(client):
    csrf = _login(client)
    r = client.get("/statutory-reports/api/templates", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    data = r.json()
    keys = {t["template_key"] for t in data["templates"]}
    assert "nssa_critical_incident" in keys
    assert "ema_environmental_monthly" in keys


def test_create_report_autofills(client):
    csrf = _login(client)
    db = get_db()
    try:
        org = db.execute("SELECT id FROM organisations WHERE slug = %s", ("test-org",)).fetchone()
        db.execute(
            "INSERT OR REPLACE INTO incidents (id, incident_ref, title, severity, status, "
            "location, occurred_at, description, immediate_cause, org_id, created_by) "
            "VALUES (990, 'INC-2026-999', 'Test critical', 'critical', 'open', 'Plant A', "
            "'2026-08-10T08:00:00', 'Desc', 'Isolation', %s, 1)", (org["id"],))
        db.commit()
    finally:
        db.close()
    r = client.post("/statutory-reports/api/reports", data={
        "template_key": "nssa_critical_incident",
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
    }, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["ok"]
    assert data["report_ref"].startswith("NSSA-")
    assert data["data"]["incident_ref"] == "INC-2026-999"


def test_update_and_lock(client):
    csrf = _login(client)
    r = client.post("/statutory-reports/api/reports", data={
        "template_key": "ema_environmental_monthly",
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
    }, headers={"X-CSRF-Token": csrf})
    report_id = r.json()["report_id"]
    r2 = client.post(f"/statutory-reports/api/reports/{report_id}/update", data={
        "updates_json": json.dumps({"co2_tco2e": 123.4})
    }, headers={"X-CSRF-Token": csrf})
    assert r2.status_code == 200, r2.text
    r3 = client.post(f"/statutory-reports/api/reports/{report_id}/lock", headers={"X-CSRF-Token": csrf})
    assert r3.status_code == 200, r3.text
    r4 = client.post(f"/statutory-reports/api/reports/{report_id}/update", data={
        "updates_json": json.dumps({"co2_tco2e": 999})
    }, headers={"X-CSRF-Token": csrf})
    assert r4.status_code == 400
    assert r4.json()["error"] == "report_locked"


def test_submit_report(client):
    csrf = _login(client)
    r = client.post("/statutory-reports/api/reports", data={
        "template_key": "zrp_monthly",
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
    }, headers={"X-CSRF-Token": csrf})
    report_id = r.json()["report_id"]
    r2 = client.post(f"/statutory-reports/api/reports/{report_id}/submit", headers={"X-CSRF-Token": csrf})
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "submitted"
    r3 = client.get(f"/statutory-reports/api/reports/{report_id}", headers={"X-CSRF-Token": csrf})
    assert r3.json()["report"]["status"] == "submitted"


def test_export_json_and_text(client):
    csrf = _login(client)
    r = client.post("/statutory-reports/api/reports", data={
        "template_key": "ema_environmental_monthly",
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
    }, headers={"X-CSRF-Token": csrf})
    report_id = r.json()["report_id"]
    rj = client.get(f"/statutory-reports/api/reports/{report_id}/export.json", headers={"X-CSRF-Token": csrf})
    assert rj.status_code == 200
    assert "data" in rj.json()
    rt = client.get(f"/statutory-reports/api/reports/{report_id}/export.txt", headers={"X-CSRF-Token": csrf})
    assert rt.status_code == 200
    assert "EMA" in rt.text
