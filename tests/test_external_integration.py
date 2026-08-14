"""B5 external integration and portal submission tests."""
from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient
from sheplatform.database import get_db


def _login(client: TestClient, email: str = "admin@test.com", password: str = "ChangeMe!123"):
    client.post("/login", data={"email": email, "password": password}, follow_redirects=False)


def _get_csrf(client: TestClient) -> str:
    raw = client.cookies.get("she_csrf", "")
    token = raw.decode() if isinstance(raw, bytes) else str(raw)
    if not token:
        client.get("/login")
        raw = client.cookies.get("she_csrf", "")
        token = raw.decode() if isinstance(raw, bytes) else str(raw)
    return token


def _seed_user_org(client: TestClient):
    from sheplatform.database import get_db
    with get_db() as db:
        org = db.execute("SELECT id FROM organisations WHERE slug='test-org'").fetchone()
        if not org:
            cur = db.execute(
                "INSERT INTO organisations (name, slug) VALUES (%s, %s) RETURNING id",
                ("Test Org", "test-org"),
            )
            org_id = cur.fetchone()["id"]
        else:
            org_id = org["id"]
        user = db.execute("SELECT id FROM users WHERE email=%s", ("admin@test.com",)).fetchone()
        if not user:
            from sheplatform.core.auth import hash_password
            db.execute(
                """INSERT INTO users (email, password_hash, first_name, last_name,
                     role_key, org_id, is_active)
                   VALUES (%s, %s, %s, %s, %s, %s, TRUE)""",
                ("admin@test.com", hash_password("ChangeMe!123"), "Admin", "User", "super_admin", org_id),
            )
    return org_id


def _seed_statutory_report(client, org_id):
    from sheplatform.modules.statutory_reporting.data_service import seed_templates
    with get_db() as db:
        seed_templates(db)
    with get_db() as db:
        tpl = db.execute("SELECT template_key FROM statutory_report_templates WHERE template_key='nssa_critical_incident'").fetchone()
    _login(client)
    # Create a report from the NSSA template
    if not tpl:
        raise AssertionError("NSSA template not seeded")
    resp = client.post(
        "/statutory-reports/api/reports",
        headers={"X-CSRF-Token": _get_csrf(client)},
        data={"template_key": "nssa_critical_incident", "period_start": "2026-08-01", "period_end": "2026-08-31"},
    )
    assert resp.status_code in (200, 201), resp.text
    report_id = resp.json()["report_id"]
    # Move to submitted
    resp = client.post(f"/statutory-reports/api/reports/{report_id}/lock", headers={"X-CSRF-Token": _get_csrf(client)})
    assert resp.status_code in (200, 204), resp.text
    resp = client.post(f"/statutory-reports/api/reports/{report_id}/submit", headers={"X-CSRF-Token": _get_csrf(client)})
    assert resp.status_code in (200, 204), resp.text
    return report_id


def test_integrations_page_requires_auth(client: TestClient):
    resp = client.get("/integrations", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers.get("location", "")


def test_list_endpoints_auth_guard(client: TestClient):
    _seed_user_org(client)
    resp = client.get("/integrations/api/endpoints", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers.get("location", "")


def test_create_and_list_endpoint(client: TestClient):
    org_id = _seed_user_org(client)
    _login(client)
    csrf = _get_csrf(client)
    resp = client.post(
        "/integrations/api/endpoints",
        headers={"X-CSRF-Token": csrf},
        json={
            "endpoint_key": "themisiq_prod",
            "name": "ThemisIQ Production",
            "system_type": "themisiq",
            "direction": "outbound",
            "base_url": "https://themisiq.example.com",
            "auth_type": "api_key",
            "auth_config": {"header_name": "X-API-Key"},
            "headers": {"Accept": "application/json"},
            "timeout_seconds": 30,
            "rate_limit_per_minute": 60,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"]

    resp = client.get("/integrations/api/endpoints")
    assert resp.status_code == 200
    eps = resp.json()["endpoints"]
    assert any(e["endpoint_key"] == "themisiq_prod" for e in eps)


def test_themisiq_webhook_signature_verification(client: TestClient):
    org_id = _seed_user_org(client)
    _login(client)
    csrf = _get_csrf(client)
    # Create an inbound endpoint and store secret
    resp = client.post(
        "/integrations/api/endpoints",
        headers={"X-CSRF-Token": csrf},
        json={
            "endpoint_key": "themisiq_inbound",
            "name": "ThemisIQ Inbound",
            "system_type": "themisiq",
            "direction": "inbound",
            "base_url": "",
            "auth_type": "hmac",
        },
    )
    endpoint_id = resp.json()["id"]
    client.post(
        f"/integrations/api/endpoints/{endpoint_id}/secrets",
        headers={"X-CSRF-Token": csrf},
        json={"secret_name": "api_key", "secret_value": "shared-secret"},
    )

    payload = {
        "event_type": "erm.risk.escalated",
        "source_module": "erm",
        "source_entity_type": "risk",
        "source_entity_id": 42,
        "timestamp": "2026-08-14T10:00:00+00:00",
        "organisation_id": None,
        "triggered_by_user": 7,
        "data": {"title": "Enterprise risk escalated"},
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(b"shared-secret", body, hashlib.sha256).hexdigest()

    resp = client.post(
        "/webhooks/themisiq",
        content=body,
        headers={"X-ThemisIQ-Signature": signature, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["handled"] is True


def test_themisiq_webhook_rejects_bad_signature(client: TestClient):
    org_id = _seed_user_org(client)
    _login(client)
    csrf = _get_csrf(client)
    resp = client.post(
        "/integrations/api/endpoints",
        headers={"X-CSRF-Token": csrf},
        json={
            "endpoint_key": "themisiq_inbound_bad",
            "name": "ThemisIQ Inbound Bad",
            "system_type": "themisiq",
            "direction": "inbound",
            "base_url": "",
            "auth_type": "hmac",
        },
    )
    endpoint_id = resp.json()["id"]
    client.post(
        f"/integrations/api/endpoints/{endpoint_id}/secrets",
        headers={"X-CSRF-Token": csrf},
        json={"secret_name": "api_key", "secret_value": "shared-secret"},
    )

    body = b'{"event_type":"erm.risk.identified"}'
    resp = client.post(
        "/webhooks/themisiq",
        content=body,
        headers={"X-ThemisIQ-Signature": "sha256=bad", "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_seed_default_channels(client: TestClient):
    _seed_user_org(client)
    _login(client)
    csrf = _get_csrf(client)
    resp = client.post("/integrations/api/channels/seed", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200
    resp = client.get("/integrations/api/channels")
    keys = {ch["channel_key"] for ch in resp.json()["channels"]}
    assert "nssa_email" in keys
    assert "ema_portal" in keys
    assert "zrp_email" in keys


def test_submit_report_to_channel(client: TestClient):
    org_id = _seed_user_org(client)
    _login(client)
    csrf = _get_csrf(client)
    client.post("/integrations/api/channels/seed", headers={"X-CSRF-Token": csrf})
    report_id = _seed_statutory_report(client, org_id)

    resp = client.post(
        f"/integrations/api/reports/{report_id}/submit/nssa_email",
        headers={"X-CSRF-Token": _get_csrf(client)},
    )
    assert resp.status_code == 200, f"submit integration failed {resp.status_code}: {resp.text[:500]}"
    data = resp.json()
    assert data["status"] == "queued"
    assert data["delivery_id"]

    resp = client.get("/integrations/api/submissions")
    assert resp.status_code == 200
    subs = resp.json()["submissions"]
    assert any(s["report_id"] == report_id and s["channel_key"] == "nssa_email" for s in subs)


def test_process_queue(client: TestClient):
    org_id = _seed_user_org(client)
    _login(client)
    csrf = _get_csrf(client)
    client.post("/integrations/api/channels/seed", headers={"X-CSRF-Token": csrf})
    report_id = _seed_statutory_report(client, org_id)
    csrf = _get_csrf(client)
    client.post(
        f"/integrations/api/reports/{report_id}/submit/ema_portal",
        headers={"X-CSRF-Token": csrf},
    )
    csrf = _get_csrf(client)
    resp = client.post("/integrations/api/queue/process?limit=20", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200, resp.text
    processed = resp.json()["processed"]
    assert len(processed) >= 1

    resp = client.get("/integrations/api/submissions")
    subs = resp.json()["submissions"]
    assert any(s["report_id"] == report_id and s["status"] == "sent" for s in subs)