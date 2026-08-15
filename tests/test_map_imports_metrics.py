"""Private map measurement and controlled site-coordinate import coverage."""
from __future__ import annotations

import json

import pytest

from sheplatform.core.auth import hash_password
from sheplatform.modules.map import coordinate_import_service, data_service


def _mk_user(db, email="map-import@test.com", role="she_manager", org_id=1):
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s,%s,'Map','Manager',%s,%s)",
        (email, hash_password("Test1234!"), role, org_id),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _mk_site(db, code, org_id=1, latitude=None, longitude=None):
    db.execute(
        "INSERT INTO sites (site_code, site_name, status, latitude, longitude, org_id) "
        "VALUES (%s,%s,'active',%s,%s,%s)",
        (code, f"Site {code}", latitude, longitude, org_id),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM sites WHERE site_code = %s", (code,)).fetchone())


def _csv(*rows, header="site_code,latitude,longitude,accuracy_m"):
    return (header + "\n" + "\n".join(rows) + "\n").encode()


class TestPrivateMapMetrics:
    def test_metric_schema_excludes_users_coordinates_and_text(self, db):
        columns = {row["name"] for row in db.execute("PRAGMA table_info(map_usage_metrics)")}
        assert "org_id" in columns
        assert not {"user_id", "latitude", "longitude", "narrative", "query"} & columns

    def test_metrics_fail_closed_and_summarise_by_org(self, db):
        assert data_service.record_map_metric(
            db, event_type="map_session", org_id=None) is False
        data_service.record_map_metric(db, event_type="map_session", org_id=1)
        data_service.record_map_metric(
            db, event_type="layer_request", layer_name="sites", feature_count=4,
            duration_ms=12.25, truncated=True, org_id=1)
        summary = data_service.map_metrics_summary(db, 1)
        assert summary["sessions"] == 1
        assert summary["layers"]["sites"] == {
            "requests": 1, "features": 4, "average_duration_ms": 12.25,
            "truncations": 1,
        }
        assert data_service.map_metrics_summary(db, None)["sessions"] == 0

    def test_http_records_private_session_layers_and_provider_failure(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "metrics@test.com")
            _mk_site(db, "METRIC-SITE")
        finally:
            db.close()
        client.post("/login", data={"email": "metrics@test.com", "password": "Test1234!"})
        csrf = client.cookies.get("she_csrf", "")
        assert client.get("/map").status_code == 200
        points = client.get("/map/api/points")
        assert points.status_code == 200
        failure = client.post(
            "/map/api/metrics/provider-failure", headers={"X-CSRF-Token": csrf})
        assert failure.status_code == 200
        summary = client.get("/map/api/metrics/summary")
        assert summary.status_code == 200
        assert summary.headers["cache-control"] == "private, no-store"
        data = summary.json()
        assert data["sessions"] == 1
        assert data["provider_failures"] == 1
        assert data["layers"]["incidents"]["requests"] == 1
        assert data["layers"]["sites"]["requests"] == 1
        assert data["unlocated_sites"] == 1


class TestCoordinateImportService:
    def test_preview_is_inert_and_records_validation_audit(self, db):
        manager = _mk_user(db)
        site = _mk_site(db, "IMPORT-1")
        result = coordinate_import_service.preview_coordinate_import(
            db, file_bytes=_csv("IMPORT-1,-17.8252,31.0335,15"),
            org_id=1, created_by=manager["id"])
        assert result["batch"]["valid_rows"] == 1
        assert result["rows"][0]["status"] == "valid"
        unchanged = db.execute("SELECT * FROM sites WHERE id = %s", (site["id"],)).fetchone()
        assert unchanged["latitude"] is None
        audit = db.execute(
            "SELECT * FROM audit_log WHERE action = 'site_coords.import_preview'"
        ).fetchone()
        assert json.loads(audit["new_value"])["valid_rows"] == 1

    @pytest.mark.parametrize("contents,message", [
        (_csv("UNKNOWN,-17.8,31.0,"), "not found"),
        (_csv("IMPORT-2,999,31.0,"), "latitude"),
        (_csv("IMPORT-2,-17.8,31.0,", "IMPORT-2,-17.9,31.1,"), "duplicate"),
    ])
    def test_preview_reports_invalid_rows(self, db, contents, message):
        manager = _mk_user(db)
        _mk_site(db, "IMPORT-2")
        result = coordinate_import_service.preview_coordinate_import(
            db, file_bytes=contents, org_id=1, created_by=manager["id"])
        assert result["batch"]["invalid_rows"] >= 1
        assert any(message in (row["error"] or "") for row in result["rows"])

    def test_missing_columns_rejected_without_creating_batch(self, db):
        manager = _mk_user(db)
        with pytest.raises(ValueError, match="longitude"):
            coordinate_import_service.preview_coordinate_import(
                db, file_bytes=_csv("X,-17.8", header="site_code,latitude"),
                org_id=1, created_by=manager["id"])
        assert db.execute("SELECT COUNT(*) AS c FROM site_coordinate_imports").fetchone()["c"] == 0

    def test_invalid_batch_cannot_commit(self, db):
        manager = _mk_user(db)
        _mk_site(db, "IMPORT-3")
        preview = coordinate_import_service.preview_coordinate_import(
            db, file_bytes=_csv("IMPORT-3,999,31.0,"), org_id=1,
            created_by=manager["id"])
        result = coordinate_import_service.commit_coordinate_import(
            db, import_id=preview["batch"]["id"], org_id=1,
            updated_by=manager["id"], overwrite_existing=False)
        assert result["ok"] is False
        assert result["invalid_rows"] == 1

    def test_conflict_requires_explicit_overwrite_then_commits_and_audits(self, db):
        manager = _mk_user(db)
        site = _mk_site(db, "IMPORT-4", latitude=-17.0, longitude=30.0)
        preview = coordinate_import_service.preview_coordinate_import(
            db, file_bytes=_csv("IMPORT-4,-17.8252,31.0335,22"),
            org_id=1, created_by=manager["id"])
        denied = coordinate_import_service.commit_coordinate_import(
            db, import_id=preview["batch"]["id"], org_id=1,
            updated_by=manager["id"], overwrite_existing=False)
        assert denied["requires_overwrite"] is True
        committed = coordinate_import_service.commit_coordinate_import(
            db, import_id=preview["batch"]["id"], org_id=1,
            updated_by=manager["id"], overwrite_existing=True)
        assert committed == {"ok": True, "import_id": preview["batch"]["id"],
                             "updated_sites": 1, "overwrite_approved": True}
        updated = db.execute("SELECT * FROM sites WHERE id = %s", (site["id"],)).fetchone()
        assert updated["latitude"] == -17.8252
        assert updated["longitude"] == 31.0335
        assert updated["coordinate_source"] == "imported"
        actions = {row["action"] for row in db.execute(
            "SELECT action FROM audit_log WHERE entity_id IN (%s,%s)",
            (site["id"], preview["batch"]["id"]),
        ).fetchall()}
        assert "site.set_coords" in actions
        assert "site_coords.import_commit" in actions

    def test_preview_and_commit_are_tenant_scoped(self, db):
        db.execute("INSERT INTO organisations (name, slug) VALUES ('Import Other', 'import-other')")
        db.commit()
        other_org = db.execute(
            "SELECT id FROM organisations WHERE slug = 'import-other'").fetchone()["id"]
        manager = _mk_user(db)
        _mk_site(db, "OTHER-SITE", org_id=other_org)
        preview = coordinate_import_service.preview_coordinate_import(
            db, file_bytes=_csv("OTHER-SITE,-17.8,31.0,"), org_id=1,
            created_by=manager["id"])
        assert preview["rows"][0]["status"] == "invalid"
        assert coordinate_import_service.get_coordinate_import(
            db, import_id=preview["batch"]["id"], org_id=other_org)["ok"] is False

    def test_commit_rolls_back_all_rows_if_preview_becomes_stale(self, db):
        db.execute("INSERT INTO organisations (name, slug) VALUES ('Moved Org', 'moved-org')")
        db.commit()
        other_org = db.execute(
            "SELECT id FROM organisations WHERE slug = 'moved-org'").fetchone()["id"]
        manager = _mk_user(db)
        first = _mk_site(db, "ATOMIC-1")
        second = _mk_site(db, "ATOMIC-2")
        preview = coordinate_import_service.preview_coordinate_import(
            db, file_bytes=_csv("ATOMIC-1,-17.8,31.0,", "ATOMIC-2,-18.0,30.9,"),
            org_id=1, created_by=manager["id"])
        db.execute("UPDATE sites SET org_id = %s WHERE id = %s", (other_org, second["id"]))
        db.commit()
        with pytest.raises(ValueError, match="no longer available"):
            coordinate_import_service.commit_coordinate_import(
                db, import_id=preview["batch"]["id"], org_id=1,
                updated_by=manager["id"], overwrite_existing=False)
        unchanged = db.execute("SELECT latitude FROM sites WHERE id = %s", (first["id"],)).fetchone()
        assert unchanged["latitude"] is None
        batch = db.execute(
            "SELECT status FROM site_coordinate_imports WHERE id = %s",
            (preview["batch"]["id"],),
        ).fetchone()
        assert batch["status"] == "previewed"

    def test_commit_rejects_when_a_previewed_site_gains_coordinates_before_commit(self, db):
        manager = _mk_user(db)
        first = _mk_site(db, "RACE-1")
        second = _mk_site(db, "RACE-2")
        preview = coordinate_import_service.preview_coordinate_import(
            db, file_bytes=_csv("RACE-1,-17.8,31.0,", "RACE-2,-18.0,30.9,"),
            org_id=1, created_by=manager["id"])
        # A different admin locates RACE-2 through the single-site editor
        # while this preview batch sits uncommitted. RACE-2 was previewed as
        # "valid" (no existing coordinates), so nobody was ever asked to
        # approve overwriting it.
        data_service.set_site_coords(
            db, site_id=second["id"], latitude=-1.0, longitude=1.0,
            source="manual", updated_by=manager["id"], org_id=1)
        with pytest.raises(ValueError, match="gained coordinates"):
            coordinate_import_service.commit_coordinate_import(
                db, import_id=preview["batch"]["id"], org_id=1,
                updated_by=manager["id"], overwrite_existing=False)
        # RACE-1 was processed first and updated within the transaction
        # before the race was detected on RACE-2; the whole commit must
        # still roll back atomically, not leave RACE-1 half-applied.
        first_now = db.execute(
            "SELECT latitude FROM sites WHERE id = %s", (first["id"],)).fetchone()
        assert first_now["latitude"] is None
        batch = db.execute(
            "SELECT status FROM site_coordinate_imports WHERE id = %s",
            (preview["batch"]["id"],)).fetchone()
        assert batch["status"] == "previewed"
        # RACE-2 keeps the other admin's value, not the stale CSV value.
        second_now = db.execute(
            "SELECT latitude, longitude FROM sites WHERE id = %s",
            (second["id"],)).fetchone()
        assert second_now["latitude"] == -1.0
        assert second_now["longitude"] == 1.0

    def test_metric_failure_never_blocks_preview_or_commit(self, db, monkeypatch):
        manager = _mk_user(db)
        site = _mk_site(db, "METRIC-INDEPENDENT")

        def fail_metric(*args, **kwargs):
            raise RuntimeError("measurement unavailable")

        monkeypatch.setattr(data_service, "record_map_metric", fail_metric)
        preview = coordinate_import_service.preview_coordinate_import(
            db, file_bytes=_csv("METRIC-INDEPENDENT,-17.8,31.0,"),
            org_id=1, created_by=manager["id"])
        committed = coordinate_import_service.commit_coordinate_import(
            db, import_id=preview["batch"]["id"], org_id=1,
            updated_by=manager["id"], overwrite_existing=False)
        assert committed["ok"] is True
        updated = db.execute("SELECT latitude FROM sites WHERE id = %s", (site["id"],)).fetchone()
        assert updated["latitude"] == -17.8


class TestCoordinateImportHttp:
    def _login(self, client, email):
        client.post("/login", data={"email": email, "password": "Test1234!"})
        return client.cookies.get("she_csrf", "")

    def test_manager_previews_and_commits_without_geocoder(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "import-http@test.com")
            site = _mk_site(db, "HTTP-IMPORT")
        finally:
            db.close()
        csrf = self._login(client, "import-http@test.com")
        preview = client.post(
            "/map/api/coordinate-imports/preview",
            files={"file": ("sites.csv", _csv("HTTP-IMPORT,-17.8252,31.0335,"), "text/csv")},
            headers={"X-CSRF-Token": csrf})
        assert preview.status_code == 201, preview.text
        assert preview.headers["cache-control"] == "private, no-store"
        import_id = preview.json()["batch"]["id"]
        commit = client.post(
            f"/map/api/coordinate-imports/{import_id}/commit",
            data={"overwrite_existing": "false"}, headers={"X-CSRF-Token": csrf})
        assert commit.status_code == 200, commit.text
        db = get_db()
        try:
            updated = db.execute("SELECT * FROM sites WHERE id = %s", (site["id"],)).fetchone()
            assert updated["coordinate_source"] == "imported"
        finally:
            db.close()

    def test_non_manager_cannot_preview_import(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "import-officer@test.com", role="she_officer")
        finally:
            db.close()
        csrf = self._login(client, "import-officer@test.com")
        response = client.post(
            "/map/api/coordinate-imports/preview",
            files={"file": ("sites.csv", _csv("ANY,-17.8,31.0,"), "text/csv")},
            headers={"X-CSRF-Token": csrf})
        assert response.status_code == 403
