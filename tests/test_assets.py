"""C4: asset register + telemetry ingestion.

Data-service tests use the plain `db` fixture. API-key auth and org
isolation on the telemetry endpoint are tested through the real HTTP
route (client fixture), matching the established convention - the tenant
boundary and the auth guard both live at that layer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sheplatform.core.auth import hash_password
from sheplatform.modules.assets import data_service


def _mk_user(db, role, email, org_id=1):
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "T", "U", role, org_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
    return dict(row)


def _mk_asset(db, org_id=1, ref="GEN-001", interval=None, esg_kpi_code=""):
    return data_service.create_asset(
        db, asset_ref=ref, name="Test Generator", asset_type="generator",
        service_interval_hours=interval, esg_kpi_code=esg_kpi_code, org_id=org_id)


def _ts(minutes_from_now: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)).isoformat()


class TestAssetCrud:
    def test_org_isolation(self, db):
        db.execute("INSERT INTO organisations (name, slug) VALUES ('Other Org', 'other-assets')")
        db.commit()
        other_org = db.execute("SELECT id FROM organisations WHERE slug = 'other-assets'").fetchone()["id"]
        _mk_asset(db, org_id=1, ref="MINE-1")
        _mk_asset(db, org_id=other_org, ref="THEIRS-1")
        assets = data_service.list_assets(db, 1)
        assert [a["asset_ref"] for a in assets] == ["MINE-1"]

    def test_fails_closed_without_org(self, db):
        _mk_asset(db, org_id=1)
        assert data_service.list_assets(db, None) == []


class TestRecordTelemetry:
    def test_unknown_asset_rejected(self, db):
        result = data_service.record_telemetry(db, asset_ref="NOPE", run_hours=10, org_id=1)
        assert result["ok"] is False

    def test_valid_reading_updates_run_hours(self, db):
        asset = _mk_asset(db)
        result = data_service.record_telemetry(
            db, asset_ref="GEN-001", run_hours=120.5, fuel_level_pct=80, org_id=1)
        assert result["ok"] is True
        updated = data_service.get_asset(db, asset["id"])
        assert updated["total_run_hours"] == 120.5

    def test_negative_run_hours_rejected(self, db):
        _mk_asset(db)
        result = data_service.record_telemetry(db, asset_ref="GEN-001", run_hours=-1, org_id=1)
        assert result["ok"] is False

    def test_fuel_out_of_range_rejected(self, db):
        _mk_asset(db)
        result = data_service.record_telemetry(db, asset_ref="GEN-001", fuel_level_pct=150, org_id=1)
        assert result["ok"] is False

    def test_sub_minute_reading_rejected(self, db):
        _mk_asset(db)
        t0 = _ts(0)
        data_service.record_telemetry(db, asset_ref="GEN-001", run_hours=1, recorded_at=t0, org_id=1)
        t1 = (datetime.fromisoformat(t0) + timedelta(seconds=30)).isoformat()
        result = data_service.record_telemetry(db, asset_ref="GEN-001", run_hours=2, recorded_at=t1, org_id=1)
        assert result["ok"] is False

    def test_service_interval_exceeded_creates_one_maintenance_task(self, db):
        _mk_user(db, "she_manager", "asset-mgr@test.com")
        _mk_asset(db, interval=100)
        data_service.record_telemetry(db, asset_ref="GEN-001", run_hours=50, recorded_at=_ts(0), org_id=1)
        r1 = data_service.record_telemetry(db, asset_ref="GEN-001", run_hours=150, recorded_at=_ts(2), org_id=1)
        assert r1["maintenance_task"] is not None
        # A second reading still over the interval must not create a duplicate open task.
        r2 = data_service.record_telemetry(db, asset_ref="GEN-001", run_hours=160, recorded_at=_ts(4), org_id=1)
        assert r2["maintenance_task"] is None
        tasks = data_service.list_maintenance_tasks(db, 1, status="open")
        assert len(tasks) == 1

    def test_fuel_theft_anomaly_flagged(self, db):
        _mk_asset(db)
        data_service.record_telemetry(
            db, asset_ref="GEN-001", run_hours=100, fuel_level_pct=90, recorded_at=_ts(0), org_id=1)
        result = data_service.record_telemetry(
            db, asset_ref="GEN-001", run_hours=100.1, fuel_level_pct=60, recorded_at=_ts(2), org_id=1)
        assert result["reading"]["is_anomaly"]  # SQLite stores BOOLEAN as 0/1, not True/False
        assert "theft" in result["reading"]["anomaly_reason"]

    def test_fuel_drop_with_real_runtime_not_flagged(self, db):
        _mk_asset(db)
        data_service.record_telemetry(
            db, asset_ref="GEN-001", run_hours=100, fuel_level_pct=90, recorded_at=_ts(0), org_id=1)
        result = data_service.record_telemetry(
            db, asset_ref="GEN-001", run_hours=108, fuel_level_pct=60, recorded_at=_ts(2), org_id=1)
        assert not result["reading"]["is_anomaly"]

    def test_esg_kpi_fed_when_code_matches(self, db):
        db.execute(
            "INSERT INTO esg_kpis (kpi_code, category, name, unit, org_id) "
            "VALUES ('ESG-ENV-05', 'environmental', 'Energy consumption', 'MWh', 1)")
        db.commit()
        _mk_asset(db, esg_kpi_code="ESG-ENV-05")
        data_service.record_telemetry(db, asset_ref="GEN-001", run_hours=42, org_id=1)
        period = datetime.now(timezone.utc).strftime("%Y-%m")
        entry = db.execute(
            "SELECT e.* FROM esg_kpi_entries e JOIN esg_kpis k ON k.id = e.kpi_id "
            "WHERE k.kpi_code = 'ESG-ENV-05' AND e.period = %s", (period,)).fetchone()
        assert entry is not None
        assert entry["actual_value"] == 42

    def test_no_esg_kpi_code_skips_silently(self, db):
        _mk_asset(db, esg_kpi_code="")
        result = data_service.record_telemetry(db, asset_ref="GEN-001", run_hours=42, org_id=1)
        assert result["ok"] is True


class TestCompleteMaintenance:
    def test_resets_service_baseline(self, db):
        manager = _mk_user(db, "she_manager", "asset-mgr2@test.com")
        asset = _mk_asset(db, interval=50)
        r = data_service.record_telemetry(db, asset_ref="GEN-001", run_hours=60, org_id=1)
        task_id = r["maintenance_task"]["id"]
        result = data_service.complete_maintenance(db, task_id, 1, manager["id"])
        assert result["ok"] is True
        updated = data_service.get_asset(db, asset["id"])
        assert updated["hours_at_last_service"] == 60


class TestTelemetryHttp:
    def _login(self, client, email):
        client.post("/login", data={"email": email, "password": "Test1234!"})
        return client.cookies.get("she_csrf", "")

    def test_missing_key_rejected(self, client):
        resp = client.post("/assets/api/telemetry", json={"asset_ref": "GEN-001", "run_hours": 1})
        assert resp.status_code == 401

    def test_invalid_key_rejected(self, client):
        resp = client.post("/assets/api/telemetry", json={"asset_ref": "GEN-001", "run_hours": 1},
                           headers={"X-Asset-API-Key": "not-a-real-key"})
        assert resp.status_code == 401

    def test_key_without_telemetry_scope_rejected(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            manager = _mk_user(db, "she_manager", "http-mgr0@test.com")
            _mk_asset(db)
            key_result = data_service.create_api_key(db, name="wrong scope", org_id=1, created_by=manager["id"])
            # create_api_key always grants the default scope; downgrade it directly
            # to simulate a key provisioned for something else, same as ESG's own
            # scope check this mirrors.
            db.execute("UPDATE asset_api_keys SET scopes = '[]' WHERE id = %s", (key_result["record"]["id"],))
            db.commit()
        finally:
            db.close()
        resp = client.post("/assets/api/telemetry", json={"asset_ref": "GEN-001", "run_hours": 1},
                           headers={"X-Asset-API-Key": key_result["api_key"]})
        assert resp.status_code == 403

    def test_valid_key_creates_reading(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            manager = _mk_user(db, "she_manager", "http-mgr@test.com")
            _mk_asset(db)
            key_result = data_service.create_api_key(db, name="test gateway", org_id=1, created_by=manager["id"])
        finally:
            db.close()
        resp = client.post("/assets/api/telemetry",
                           json={"asset_ref": "GEN-001", "run_hours": 15, "fuel_level_pct": 95},
                           headers={"X-Asset-API-Key": key_result["api_key"]})
        assert resp.status_code == 201, resp.text
        assert resp.json()["ok"] is True

    def test_key_scoped_to_own_org_asset(self, client):
        """Regression: a valid key for org 1 must not be able to post
        telemetry against an asset that only exists in another org."""
        from sheplatform.database import get_db
        db = get_db()
        try:
            db.execute("INSERT INTO organisations (name, slug) VALUES ('Other Org2', 'other-assets-2')")
            db.commit()
            other_org = db.execute("SELECT id FROM organisations WHERE slug = 'other-assets-2'").fetchone()["id"]
            manager = _mk_user(db, "she_manager", "http-mgr2@test.com", org_id=1)
            _mk_asset(db, org_id=other_org, ref="OTHER-GEN")
            key_result = data_service.create_api_key(db, name="k", org_id=1, created_by=manager["id"])
        finally:
            db.close()
        resp = client.post("/assets/api/telemetry", json={"asset_ref": "OTHER-GEN", "run_hours": 1},
                           headers={"X-Asset-API-Key": key_result["api_key"]})
        assert resp.status_code == 400
        assert "unknown asset" in resp.json()["message"]

    def test_list_endpoint_org_isolation(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            db.execute("INSERT INTO organisations (name, slug) VALUES ('Other Org3', 'other-assets-3')")
            db.commit()
            other_org = db.execute("SELECT id FROM organisations WHERE slug = 'other-assets-3'").fetchone()["id"]
            _mk_user(db, "she_officer", "http3@test.com", org_id=1)
            _mk_asset(db, org_id=1, ref="MINE-2")
            _mk_asset(db, org_id=other_org, ref="THEIRS-2")
        finally:
            db.close()
        csrf = self._login(client, "http3@test.com")
        resp = client.get("/assets/api/list", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200
        assert [a["asset_ref"] for a in resp.json()["assets"]] == ["MINE-2"]
