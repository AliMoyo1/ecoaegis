"""Phase 5 Mapbox security, privacy, retention, and release-gate coverage."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from sheplatform.core.auth import hash_password
from sheplatform.modules.map import provider_admission_service as admission


ROOT = Path(__file__).resolve().parents[1]
HARARE_BBOX = "30,-19,32,-16"


def _user(db, email: str, *, role: str = "she_officer", org_id=1) -> dict:
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s,%s,'Phase','Five',%s,%s)",
        (email, hash_password("Test1234!"), role, org_id),
    )
    db.commit()
    return dict(db.execute(
        "SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _login(client, email: str) -> str:
    response = client.post(
        "/login", data={"email": email, "password": "Test1234!"})
    assert response.status_code in (200, 303)
    return client.cookies.get("she_csrf", "")


def _page_nonce(page_text: str) -> str:
    match = re.search(r'data-provider-page-nonce="([^"]*)"', page_text)
    assert match is not None
    return match.group(1)


def test_map_shell_and_operational_api_are_private_and_hardened(client):
    from sheplatform.database import get_db

    db = get_db()
    try:
        _user(db, "private-map@test.com", role="she_manager")
    finally:
        db.close()
    _login(client, "private-map@test.com")

    for response in (
        client.get("/map"),
        client.get(f"/map/api/manifest?bbox={HARARE_BBOX}"),
        client.get(f"/map/api/layer/incidents?bbox={HARARE_BBOX}"),
    ):
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["x-content-type-options"] == "nosniff"
        policy = response.headers["content-security-policy"]
        assert "default-src 'self'" in policy
        assert "object-src 'none'" in policy
        assert "frame-ancestors 'none'" in policy
        assert "script-src-attr 'none'" in policy
        assert "'unsafe-eval'" not in policy


def test_role_without_map_capability_receives_no_page_layer_or_token(
        client, monkeypatch):
    from sheplatform.database import get_db

    db = get_db()
    try:
        _user(db, "employee-map@test.com", role="employee")
    finally:
        db.close()
    monkeypatch.setattr("sheplatform.config.settings.MAP_ENGINE", "mapbox")
    monkeypatch.setattr(
        "sheplatform.config.settings.MAPBOX_PUBLIC_TOKEN", "pk.capability-test")
    _login(client, "employee-map@test.com")

    page = client.get("/map")
    layer = client.get(f"/map/api/layer/incidents?bbox={HARARE_BBOX}")
    assert page.status_code == 403
    assert layer.status_code == 403
    assert "pk.capability-test" not in page.text
    assert "pk.capability-test" not in layer.text


def test_missing_organisation_fails_closed_and_never_issues_provider_token(
        client, monkeypatch):
    corrupt_session_user = {
        "id": 9001,
        "email": "no-org-map@test.com",
        "first_name": "No",
        "last_name": "Organisation",
        "role_key": "she_officer",
        "org_id": None,
        "mfa_enabled": False,
        "mfa_verified": False,
    }
    monkeypatch.setattr(
        "sheplatform.core.middleware.get_current_user",
        lambda request: dict(corrupt_session_user),
    )
    monkeypatch.setattr("sheplatform.config.settings.MAP_ENGINE", "mapbox")
    monkeypatch.setattr(
        "sheplatform.config.settings.MAPBOX_PUBLIC_TOKEN", "pk.no-org-test")
    client.cookies.set("she_session", "corrupt-session")
    client.cookies.set("she_csrf", "corrupt-csrf")
    csrf = "corrupt-csrf"

    page = client.get("/map")
    assert page.status_code == 200
    assert _page_nonce(page.text) == ""
    assert "pk.no-org-test" not in page.text

    admission_response = client.post(
        "/map/api/provider-session",
        data={"page_nonce": ""},
        headers={"X-CSRF-Token": csrf},
    )
    assert admission_response.status_code == 400
    assert "token" not in admission_response.json()
    layer = client.get(f"/map/api/layer/incidents?bbox={HARARE_BBOX}")
    assert layer.status_code == 200
    assert layer.json()["features"] == []


def test_budget_denial_never_releases_token(client, monkeypatch):
    from sheplatform.database import get_db

    db = get_db()
    try:
        _user(db, "budget-stop@test.com", role="she_manager")
    finally:
        db.close()
    monkeypatch.setattr("sheplatform.config.settings.MAP_ENGINE", "mapbox")
    monkeypatch.setattr(
        "sheplatform.config.settings.MAPBOX_PUBLIC_TOKEN", "pk.budget-stop-test")
    monkeypatch.setattr("sheplatform.config.settings.MAP_PROVIDER_WARNING_LOADS", 0)
    monkeypatch.setattr("sheplatform.config.settings.MAP_PROVIDER_CRITICAL_LOADS", 1)
    monkeypatch.setattr("sheplatform.config.settings.MAP_PROVIDER_MONTHLY_LIMIT", 1)
    csrf = _login(client, "budget-stop@test.com")

    first_page = client.get("/map")
    admitted = client.post(
        "/map/api/provider-session",
        data={"page_nonce": _page_nonce(first_page.text)},
        headers={"X-CSRF-Token": csrf},
    )
    assert admitted.status_code == 200

    second_page = client.get("/map")
    denied = client.post(
        "/map/api/provider-session",
        data={"page_nonce": _page_nonce(second_page.text)},
        headers={"X-CSRF-Token": csrf},
    )
    assert denied.status_code == 429
    assert denied.json()["decision"] == "denied"
    assert "token" not in denied.json()
    assert "pk.budget-stop-test" not in denied.text


def test_budget_threshold_transitions_are_audited_once(db, monkeypatch):
    user = _user(db, "threshold-audit@test.com")
    monkeypatch.setattr("sheplatform.config.settings.MAP_PROVIDER_WARNING_LOADS", 1)
    monkeypatch.setattr("sheplatform.config.settings.MAP_PROVIDER_CRITICAL_LOADS", 2)
    monkeypatch.setattr("sheplatform.config.settings.MAP_PROVIDER_MONTHLY_LIMIT", 2)

    def attempt(index: int):
        session = f"threshold-audit-{index}"
        nonce = admission.issue_page_nonce(
            user_id=user["id"], org_id=user["org_id"], session_token=session)
        return admission.admit_provider_session(
            db, nonce=nonce, user_id=user["id"], org_id=user["org_id"],
            session_token=session)

    assert attempt(1).admitted is True
    assert attempt(2).admitted is True
    assert attempt(3).admitted is False
    assert attempt(4).admitted is False

    actions = [row["action"] for row in db.execute(
        "SELECT action FROM audit_log WHERE action LIKE 'map.provider.%' "
        "ORDER BY id").fetchall()]
    assert actions == [
        "map.provider.warning",
        "map.provider.critical",
        "map.provider.blocked",
    ]


def test_admission_retention_keeps_current_and_prior_utc_month(db, monkeypatch):
    user = _user(db, "retention-map@test.com")
    fixed_now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(admission, "_utc_now", lambda: fixed_now)
    for index, month in enumerate(("2026-08", "2026-07", "2026-06"), start=1):
        db.execute(
            "INSERT INTO map_provider_admissions "
            "(admission_id, provider, billing_month_utc, org_id, decision, created_at) "
            "VALUES (%s,'mapbox',%s,%s,'admitted',%s)",
            (f"retention-{index:02d}-opaque-identifier", month, user["org_id"],
             fixed_now.isoformat()),
        )
    db.commit()

    assert admission.prune_old_admissions(db) == 1
    remaining = [row["billing_month_utc"] for row in db.execute(
        "SELECT billing_month_utc FROM map_provider_admissions "
        "ORDER BY billing_month_utc DESC").fetchall()]
    assert remaining == ["2026-08", "2026-07"]


def test_retention_scheduler_registers_one_daily_utc_job():
    from sheplatform.modules.map.scheduler import start_scheduler

    scheduler = start_scheduler(lambda: None)
    try:
        jobs = scheduler.get_jobs()
        assert [job.id for job in jobs] == ["map_provider_admission_retention"]
        assert "hour='3'" in str(jobs[0].trigger)
        assert "minute='20'" in str(jobs[0].trigger)
    finally:
        scheduler.shutdown(wait=False)


def test_application_source_contains_no_committed_provider_credentials():
    token_pattern = re.compile(r"\b(?:pk|sk)\.[A-Za-z0-9._-]{20,}\b")
    paths = [
        *ROOT.joinpath("sheplatform").rglob("*.py"),
        *ROOT.joinpath("sheplatform").rglob("*.html"),
        *ROOT.joinpath("sheplatform", "static", "js").glob("*.js"),
        ROOT / ".env.example",
    ]
    findings = []
    for path in paths:
        source = path.read_text(encoding="utf-8", errors="ignore")
        findings.extend((str(path.relative_to(ROOT)), match.group(0))
                        for match in token_pattern.finditer(source))
    assert findings == []


def test_operational_geojson_requests_remain_same_origin():
    source = (ROOT / "sheplatform" / "static" / "js" /
              "mapbox-command-map.js").read_text(encoding="utf-8")
    assert 'fetch("https://' not in source
    assert "api.mapbox.com" not in source
    assert "events.mapbox.com" not in source
    assert "transformRequest" not in source
    assert 'fetch("/map/api/provider-session"' in source
    assert "function manifestUrl(bounds)" in source
    assert "fetch(manifestUrl(bounds))" in source
    assert "function layerUrl(spec, bounds)" in source
    assert "fetch(layerUrl(spec, bounds)" in source
