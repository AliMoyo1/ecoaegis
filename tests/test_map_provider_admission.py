"""Phase 4 provider admission, privacy, idempotency, and HTTP boundaries."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from sheplatform.core.auth import hash_password
from sheplatform.modules.map import provider_admission_service as service


def _user(db, email: str = "mapbox@test.com", org_id: int = 1,
          role: str = "she_officer") -> dict:
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s,%s,'Map','User',%s,%s)",
        (email, hash_password("Test1234!"), role, org_id),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _nonce(user: dict, session: str = "session-one") -> str:
    return service.issue_page_nonce(
        user_id=user["id"], org_id=user["org_id"], session_token=session)


def test_nonce_is_bound_to_user_tenant_and_session(db):
    user = _user(db)
    nonce = _nonce(user)
    with pytest.raises(service.InvalidProviderNonce):
        service.admit_provider_session(
            db, nonce=nonce, user_id=user["id"], org_id=user["org_id"],
            session_token="another-session")


def test_admission_is_idempotent_and_increments_once(db):
    user = _user(db)
    nonce = _nonce(user)
    first = service.admit_provider_session(
        db, nonce=nonce, user_id=user["id"], org_id=1, session_token="session-one")
    repeated = service.admit_provider_session(
        db, nonce=nonce, user_id=user["id"], org_id=1, session_token="session-one")
    usage = db.execute(
        "SELECT admitted_loads FROM map_provider_monthly_usage WHERE provider = 'mapbox'"
    ).fetchone()
    admissions = db.execute("SELECT COUNT(*) FROM map_provider_admissions").fetchone()[0]
    assert first.admitted is True and first.repeated is False
    assert repeated.admitted is True and repeated.repeated is True
    assert usage["admitted_loads"] == 1
    assert admissions == 1


def test_limit_denies_without_incrementing_or_leaking_request_data(db, monkeypatch):
    user = _user(db)
    monkeypatch.setattr("sheplatform.config.settings.MAP_PROVIDER_MONTHLY_LIMIT", 1)
    first = _nonce(user, "first")
    second = _nonce(user, "second")
    assert service.admit_provider_session(
        db, nonce=first, user_id=user["id"], org_id=1,
        session_token="first").admitted is True
    denied = service.admit_provider_session(
        db, nonce=second, user_id=user["id"], org_id=1,
        session_token="second")
    usage = db.execute(
        "SELECT admitted_loads, blocked_recorded_at FROM map_provider_monthly_usage"
    ).fetchone()
    from sheplatform.config import settings
    if settings.is_postgres():
        columns = {row[0] for row in db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'map_provider_admissions'"
        ).fetchall()}
    else:
        columns = {row[1] for row in db.execute(
            "PRAGMA table_info(map_provider_admissions)").fetchall()}
    assert denied.admitted is False
    assert usage["admitted_loads"] == 1
    assert usage["blocked_recorded_at"] is not None
    assert columns == {"admission_id", "provider", "billing_month_utc", "org_id",
                       "decision", "created_at"}


def test_warning_and_critical_crossings_are_recorded_once(db, monkeypatch):
    user = _user(db)
    monkeypatch.setattr("sheplatform.config.settings.MAP_PROVIDER_WARNING_LOADS", 1)
    monkeypatch.setattr("sheplatform.config.settings.MAP_PROVIDER_CRITICAL_LOADS", 2)
    monkeypatch.setattr("sheplatform.config.settings.MAP_PROVIDER_MONTHLY_LIMIT", 3)
    for index in range(2):
        session = f"threshold-{index}"
        service.admit_provider_session(
            db, nonce=_nonce(user, session), user_id=user["id"], org_id=1,
            session_token=session)
    row = db.execute(
        "SELECT admitted_loads, warning_recorded_at, critical_recorded_at "
        "FROM map_provider_monthly_usage"
    ).fetchone()
    assert row["admitted_loads"] == 2
    assert row["warning_recorded_at"] is not None
    assert row["critical_recorded_at"] is not None


def test_concurrent_admissions_never_exceed_limit(db, monkeypatch):
    from sheplatform.database import get_db
    user = _user(db)
    monkeypatch.setattr("sheplatform.config.settings.MAP_PROVIDER_WARNING_LOADS", 2)
    monkeypatch.setattr("sheplatform.config.settings.MAP_PROVIDER_CRITICAL_LOADS", 4)
    monkeypatch.setattr("sheplatform.config.settings.MAP_PROVIDER_MONTHLY_LIMIT", 5)
    requests = []
    for index in range(10):
        session = f"concurrent-{index}"
        requests.append((session, _nonce(user, session)))

    def admit(item):
        session, nonce = item
        connection = get_db()
        try:
            return service.admit_provider_session(
                connection, nonce=nonce, user_id=user["id"], org_id=1,
                session_token=session).admitted
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=10) as pool:
        decisions = list(pool.map(admit, requests))
    usage = db.execute("SELECT admitted_loads FROM map_provider_monthly_usage").fetchone()
    assert decisions.count(True) == 5
    assert decisions.count(False) == 5
    assert usage["admitted_loads"] == 5


@pytest.mark.parametrize("loads,expected", [
    (0, 0.0), (50_000, 0.0), (100_000, 250.0),
    (150_000, 450.0), (175_000, 550.0), (180_000, 570.0),
])
def test_current_mapbox_cost_tiers(loads, expected):
    assert service.estimated_monthly_cost_usd(loads) == expected


class TestProviderSessionHttp:
    def _login(self, client, email: str) -> str:
        client.post("/login", data={"email": email, "password": "Test1234!"})
        return client.cookies.get("she_csrf", "")

    def test_page_contains_nonce_but_not_token_then_admission_releases_token(
            self, client, monkeypatch):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _user(db, "browser-mapbox@test.com")
        finally:
            db.close()
        monkeypatch.setattr("sheplatform.config.settings.MAP_ENGINE", "mapbox")
        monkeypatch.setattr("sheplatform.config.settings.MAPBOX_PUBLIC_TOKEN", "pk.browser-test")
        csrf = self._login(client, "browser-mapbox@test.com")
        page = client.get("/map")
        assert page.status_code == 200
        assert "pk.browser-test" not in page.text
        policy = page.headers["content-security-policy"]
        assert "script-src 'self' 'wasm-unsafe-eval'" in policy
        assert "'unsafe-eval'" not in policy
        assert "worker-src 'self'" in policy
        assert "https://api.mapbox.com" in policy
        assert "https://events.mapbox.com" in policy
        match = re.search(r'data-provider-page-nonce="([^"]+)"', page.text)
        assert match and match.group(1)
        response = client.post(
            "/map/api/provider-session",
            data={"page_nonce": match.group(1)},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["token"] == "pk.browser-test"
        assert "admitted_loads" not in response.text

    def test_provider_session_requires_csrf(self, client, monkeypatch):
        monkeypatch.setattr("sheplatform.config.settings.MAP_ENGINE", "mapbox")
        response = client.post("/map/api/provider-session", data={"page_nonce": "x"})
        assert response.status_code == 403

    def test_disabled_engine_never_releases_token(self, client, monkeypatch):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _user(db, "leaflet@test.com")
        finally:
            db.close()
        monkeypatch.setattr("sheplatform.config.settings.MAP_ENGINE", "leaflet")
        monkeypatch.setattr("sheplatform.config.settings.MAPBOX_PUBLIC_TOKEN", "pk.must-not-leak")
        csrf = self._login(client, "leaflet@test.com")
        response = client.post(
            "/map/api/provider-session", data={"page_nonce": "unused"},
            headers={"X-CSRF-Token": csrf})
        assert response.status_code == 409
        assert "pk.must-not-leak" not in response.text

    def test_budget_summary_is_admin_only_and_never_returns_token(self, client, monkeypatch):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _user(db, "budget-manager@test.com", role="she_manager")
            _user(db, "budget-officer@test.com", role="she_officer")
        finally:
            db.close()
        monkeypatch.setattr("sheplatform.config.settings.MAP_ENGINE", "mapbox")
        monkeypatch.setattr("sheplatform.config.settings.MAPBOX_PUBLIC_TOKEN", "pk.no-budget-leak")
        self._login(client, "budget-officer@test.com")
        assert client.get("/map/api/provider-budget").status_code == 403
        client.cookies.clear()
        self._login(client, "budget-manager@test.com")
        response = client.get("/map/api/provider-budget")
        assert response.status_code == 200, response.text
        assert response.json()["monthly_limit"] == 180_000
        assert response.json()["estimated_cost_usd"] == 0.0
        assert "pk.no-budget-leak" not in response.text
        page = client.get("/map")
        assert 'id="map-budget-admin"' in page.text
