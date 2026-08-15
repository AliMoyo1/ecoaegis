"""C1: geographic map (incidents + sites with coordinates).

Data-service level tests use the plain `db` fixture (org 1, the seeded
"Test Org"), matching test_incidents.py / test_incident_injuries.py's
convention. Org-isolation tests go through the real HTTP route with the
`client` fixture, since that is where the actual tenant boundary lives
(lesson from PR #6/#7: a data-service test alone would miss a route-level
capability/org-guard regression).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from sheplatform.core.auth import hash_password
from sheplatform.modules.incidents import data_service as incident_service
from sheplatform.modules.map import data_service


def _mk_user(db, role, email, org_id=1):
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "T", "U", role, org_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
    return dict(row)


def _mk_incident(db, user_id, org_id=None, severity="high", incident_type="accident",
                 latitude=None, longitude=None, occurred_at=None):
    return incident_service.create_incident(
        db, title="Test incident", description="d", severity=severity,
        incident_type=incident_type, occurred_at=occurred_at or datetime.now(timezone.utc).isoformat(),
        reported_by=user_id, org_id=org_id, latitude=latitude, longitude=longitude)


def _mk_site(db, org_id=1, code="SITE1", latitude=None, longitude=None, status="active"):
    db.execute(
        "INSERT INTO sites (site_code, site_name, city, site_type, status, latitude, longitude, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (code, f"Site {code}", "Harare", "tower", status, latitude, longitude, org_id))
    db.commit()
    row = db.execute("SELECT * FROM sites WHERE site_code = %s", (code,)).fetchone()
    return dict(row)


class TestIncidentPoints:
    def test_fails_closed_without_org(self, db):
        officer = _mk_user(db, "she_officer", "ip1@test.com")
        _mk_incident(db, officer["id"], org_id=1, latitude=-17.8, longitude=31.05)
        assert data_service.list_incident_points(db, None) == []

    def test_only_returns_incidents_with_coords(self, db):
        officer = _mk_user(db, "she_officer", "ip2@test.com")
        _mk_incident(db, officer["id"], org_id=1, latitude=-17.8, longitude=31.05)
        _mk_incident(db, officer["id"], org_id=1)  # no coords
        points = data_service.list_incident_points(db, 1)
        assert len(points) == 1
        assert points[0]["latitude"] == -17.8

    def test_severity_filter(self, db):
        officer = _mk_user(db, "she_officer", "ip3@test.com")
        _mk_incident(db, officer["id"], org_id=1, severity="critical", latitude=-17.8, longitude=31.05)
        _mk_incident(db, officer["id"], org_id=1, severity="low", latitude=-18.0, longitude=31.0)
        points = data_service.list_incident_points(db, 1, severity="critical")
        assert len(points) == 1
        assert points[0]["severity"] == "critical"

    def test_type_filter(self, db):
        officer = _mk_user(db, "she_officer", "ip4@test.com")
        _mk_incident(db, officer["id"], org_id=1, incident_type="vehicle", latitude=-17.8, longitude=31.05)
        _mk_incident(db, officer["id"], org_id=1, incident_type="environmental", latitude=-18.0, longitude=31.0)
        points = data_service.list_incident_points(db, 1, incident_type="vehicle")
        assert len(points) == 1
        assert points[0]["incident_type"] == "vehicle"

    def test_since_filter(self, db):
        officer = _mk_user(db, "she_officer", "ip5@test.com")
        old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        recent = datetime.now(timezone.utc).isoformat()
        _mk_incident(db, officer["id"], org_id=1, latitude=-17.8, longitude=31.05, occurred_at=old)
        _mk_incident(db, officer["id"], org_id=1, latitude=-18.0, longitude=31.0, occurred_at=recent)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        points = data_service.list_incident_points(db, 1, since=cutoff)
        assert len(points) == 1


class TestSitePoints:
    def test_fails_closed_without_org(self, db):
        _mk_site(db, org_id=1, code="S1", latitude=-17.8, longitude=31.05)
        assert data_service.list_site_points(db, None) == []

    def test_excludes_inactive_and_uncoordinated_sites(self, db):
        _mk_site(db, org_id=1, code="S2", latitude=-17.8, longitude=31.05, status="active")
        _mk_site(db, org_id=1, code="S3", latitude=-18.0, longitude=31.0, status="inactive")
        _mk_site(db, org_id=1, code="S4")  # no coords
        points = data_service.list_site_points(db, 1)
        assert [p["site_code"] for p in points] == ["S2"]


class TestSetSiteCoords:
    def test_sets_coords(self, db):
        site = _mk_site(db, org_id=1, code="S5")
        manager = _mk_user(db, "she_manager", "coords1@test.com")
        result = data_service.set_site_coords(
            db, site_id=site["id"], latitude=-17.82, longitude=31.05,
            source="manual", updated_by=manager["id"], org_id=1)
        assert result["ok"] is True
        assert result["site"]["latitude"] == -17.82
        assert result["site"]["coordinate_source"] == "manual"
        assert result["site"]["coordinates_updated_by"] == manager["id"]

    def test_rejects_unknown_or_other_org_site(self, db):
        site = _mk_site(db, org_id=1, code="S6")
        manager = _mk_user(db, "she_manager", "coords2@test.com")
        result = data_service.set_site_coords(
            db, site_id=site["id"], latitude=-17.82, longitude=31.05,
            source="manual", updated_by=manager["id"], org_id=999)
        assert result["ok"] is False

    def test_fails_closed_without_org(self, db):
        site = _mk_site(db, org_id=1, code="S6A")
        manager = _mk_user(db, "she_manager", "coords3@test.com")
        result = data_service.set_site_coords(
            db, site_id=site["id"], latitude=-17.82, longitude=31.05,
            source="manual", updated_by=manager["id"], org_id=None)
        assert result["ok"] is False

    def test_records_accuracy_and_previous_new_audit_values(self, db):
        site = _mk_site(db, org_id=1, code="S6B", latitude=-17.8, longitude=31.0)
        manager = _mk_user(db, "she_manager", "coords4@test.com")
        result = data_service.set_site_coords(
            db, site_id=site["id"], latitude=-17.82, longitude=31.05,
            source="device_gps", accuracy_m=24.5,
            updated_by=manager["id"], org_id=1)
        assert result["site"]["coordinate_accuracy_m"] == 24.5
        audit = db.execute(
            "SELECT * FROM audit_log WHERE action = %s AND entity_id = %s ORDER BY id DESC LIMIT 1",
            ("site.set_coords", site["id"]),
        ).fetchone()
        old_value = json.loads(audit["old_value"])
        new_value = json.loads(audit["new_value"])
        assert old_value["latitude"] == -17.8
        assert old_value["longitude"] == 31.0
        assert new_value["coordinate_source"] == "device_gps"
        assert new_value["coordinate_accuracy_m"] == 24.5
        assert audit["user_id"] == manager["id"]
        assert audit["org_id"] == 1

    @pytest.mark.parametrize("latitude,longitude", [
        (91, 31), (-91, 31), (-17, 181), (-17, -181), (float("nan"), 31),
    ])
    def test_rejects_invalid_coordinates(self, db, latitude, longitude):
        site = _mk_site(db, org_id=1, code=f"BAD{abs(hash((latitude, longitude)))}")
        manager = _mk_user(db, "she_manager", f"bad{abs(hash((latitude, longitude)))}@test.com")
        with pytest.raises(ValueError):
            data_service.set_site_coords(
                db, site_id=site["id"], latitude=latitude, longitude=longitude,
                source="manual", updated_by=manager["id"], org_id=1)

    def test_rejects_invalid_source_and_accuracy(self, db):
        site = _mk_site(db, org_id=1, code="S6C")
        manager = _mk_user(db, "she_manager", "coords5@test.com")
        with pytest.raises(ValueError, match="source"):
            data_service.set_site_coords(
                db, site_id=site["id"], latitude=-17.8, longitude=31.0,
                source="automatic_guess", updated_by=manager["id"], org_id=1)
        with pytest.raises(ValueError, match="accuracy"):
            data_service.set_site_coords(
                db, site_id=site["id"], latitude=-17.8, longitude=31.0,
                source="device_gps", accuracy_m=-1, updated_by=manager["id"], org_id=1)

    def test_clear_coords_is_audited(self, db):
        site = _mk_site(db, org_id=1, code="S6D", latitude=-17.8, longitude=31.0)
        manager = _mk_user(db, "she_manager", "coords6@test.com")
        result = data_service.clear_site_coords(
            db, site_id=site["id"], updated_by=manager["id"], org_id=1)
        assert result["ok"] is True
        assert result["site"]["latitude"] is None
        assert result["site"]["coordinates_updated_by"] == manager["id"]
        assert result["site"]["coordinates_updated_at"] is not None
        audit = db.execute(
            "SELECT * FROM audit_log WHERE action = 'site.clear_coords' AND entity_id = %s",
            (site["id"],),
        ).fetchone()
        assert json.loads(audit["old_value"])["latitude"] == -17.8
        assert json.loads(audit["new_value"])["latitude"] is None


class TestMapHttp:
    def _login(self, client, email):
        client.post("/login", data={"email": email, "password": "Test1234!"})
        return client.cookies.get("she_csrf", "")

    def test_points_endpoint_org_isolation(self, client):
        """Regression: /map/api/points must never leak another org's incidents
        or sites, even though both live in a single shared table.
        """
        from sheplatform.database import get_db
        db = get_db()
        try:
            db.execute("INSERT INTO organisations (name, slug) VALUES ('Other Org', 'other-org')")
            db.commit()
            other_org = db.execute("SELECT id FROM organisations WHERE slug = 'other-org'").fetchone()["id"]

            me = _mk_user(db, "she_officer", "http1@test.com", org_id=1)
            _mk_incident(db, me["id"], org_id=1, latitude=-17.8, longitude=31.05)
            _mk_site(db, org_id=1, code="MINE", latitude=-17.8, longitude=31.05)

            other = _mk_user(db, "she_officer", "http1-other@test.com", org_id=other_org)
            _mk_incident(db, other["id"], org_id=other_org, latitude=-20.0, longitude=28.5)
            _mk_site(db, org_id=other_org, code="THEIRS", latitude=-20.0, longitude=28.5)
        finally:
            db.close()

        csrf = self._login(client, "http1@test.com")
        resp = client.get("/map/api/points", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        assert resp.headers["cache-control"] == "private, no-store"
        data = resp.json()
        assert len(data["incidents"]) == 1
        assert len(data["sites"]) == 1
        assert data["sites"][0]["site_code"] == "MINE"

    def test_set_site_coords_requires_settings_access(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "she_officer", "http2@test.com", org_id=1)
            site = _mk_site(db, org_id=1, code="S7")
        finally:
            db.close()
        csrf = self._login(client, "http2@test.com")
        resp = client.post(
            f"/map/api/sites/{site['id']}/coords",
            data={"latitude": "-17.8", "longitude": "31.05"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 403

    def test_manager_can_set_site_coords(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "she_manager", "http3@test.com", org_id=1)
            site = _mk_site(db, org_id=1, code="S8")
        finally:
            db.close()
        csrf = self._login(client, "http3@test.com")
        resp = client.post(
            f"/map/api/sites/{site['id']}/coords",
            data={"latitude": "-17.8", "longitude": "31.05",
                  "source": "device_gps", "accuracy_m": "18.4"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        assert resp.headers["cache-control"] == "private, no-store"
        assert resp.json()["site"]["coordinate_source"] == "device_gps"
        assert resp.json()["site"]["coordinate_accuracy_m"] == 18.4

    def test_cannot_set_coords_on_another_orgs_site(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            db.execute("INSERT INTO organisations (name, slug) VALUES ('Other Org2', 'other-org-2')")
            db.commit()
            other_org = db.execute("SELECT id FROM organisations WHERE slug = 'other-org-2'").fetchone()["id"]
            _mk_user(db, "she_manager", "http4@test.com", org_id=1)
            other_site = _mk_site(db, org_id=other_org, code="S9")
        finally:
            db.close()
        csrf = self._login(client, "http4@test.com")
        resp = client.post(
            f"/map/api/sites/{other_site['id']}/coords",
            data={"latitude": "-17.8", "longitude": "31.05"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 404

    def test_invalid_coordinates_rejected(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "she_manager", "http5@test.com", org_id=1)
            site = _mk_site(db, org_id=1, code="S10")
        finally:
            db.close()
        csrf = self._login(client, "http5@test.com")
        resp = client.post(
            f"/map/api/sites/{site['id']}/coords",
            data={"latitude": "999", "longitude": "31.05"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400

    def test_invalid_source_rejected(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "she_manager", "http-source@test.com", org_id=1)
            site = _mk_site(db, org_id=1, code="S11")
        finally:
            db.close()
        csrf = self._login(client, "http-source@test.com")
        resp = client.post(
            f"/map/api/sites/{site['id']}/coords",
            data={"latitude": "-17.8", "longitude": "31.05", "source": "guess"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400

    def test_coordinate_site_list_includes_unlocated_sites_and_is_org_scoped(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            db.execute("INSERT INTO organisations (name, slug) VALUES ('Map Other', 'map-other')")
            db.commit()
            other_org = db.execute("SELECT id FROM organisations WHERE slug = 'map-other'").fetchone()["id"]
            _mk_user(db, "she_manager", "http-sites@test.com", org_id=1)
            _mk_site(db, org_id=1, code="UNLOCATED")
            _mk_site(db, org_id=other_org, code="OTHER-UNLOCATED")
        finally:
            db.close()
        csrf = self._login(client, "http-sites@test.com")
        resp = client.get("/map/api/sites", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200
        codes = {site["site_code"] for site in resp.json()["sites"]}
        assert "UNLOCATED" in codes
        assert "OTHER-UNLOCATED" not in codes
        assert resp.headers["cache-control"] == "private, no-store"

    def test_manager_can_clear_site_coords(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "she_manager", "http-clear@test.com", org_id=1)
            site = _mk_site(db, org_id=1, code="CLEAR", latitude=-17.8, longitude=31.05)
        finally:
            db.close()
        csrf = self._login(client, "http-clear@test.com")
        resp = client.delete(
            f"/map/api/sites/{site['id']}/coords",
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        assert resp.json()["site"]["latitude"] is None

    def test_map_operates_without_basemap_and_editor_is_role_gated(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "she_manager", "http-shell-manager@test.com", org_id=1)
            _mk_user(db, "she_officer", "http-shell-officer@test.com", org_id=1)
        finally:
            db.close()
        self._login(client, "http-shell-manager@test.com")
        manager_page = client.get("/map")
        assert manager_page.status_code == 200
        assert 'id="map"' in manager_page.text
        assert 'id="coordinate-editor"' in manager_page.text
        client.cookies.clear()
        self._login(client, "http-shell-officer@test.com")
        officer_page = client.get("/map")
        assert officer_page.status_code == 200
        assert 'id="map"' in officer_page.text
        assert 'id="coordinate-editor"' not in officer_page.text


class TestIncidentCreateWithCoords:
    def _login(self, client, email):
        client.post("/login", data={"email": email, "password": "Test1234!"})
        return client.cookies.get("she_csrf", "")

    def test_geolocation_persists_via_http(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "she_officer", "http6@test.com", org_id=1)
        finally:
            db.close()
        csrf = self._login(client, "http6@test.com")
        resp = client.post(
            "/incidents/api/create",
            data={"title": "Ladder fall", "description": "d", "severity": "high",
                 "incident_type": "accident", "latitude": "-17.82", "longitude": "31.05"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 201, resp.text
        assert resp.json()["incident"]["latitude"] == -17.82
