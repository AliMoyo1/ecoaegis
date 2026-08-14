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
        result = data_service.set_site_coords(db, site["id"], -17.82, 31.05, org_id=1)
        assert result["ok"] is True
        assert result["site"]["latitude"] == -17.82

    def test_rejects_unknown_or_other_org_site(self, db):
        site = _mk_site(db, org_id=1, code="S6")
        result = data_service.set_site_coords(db, site["id"], -17.82, 31.05, org_id=999)
        assert result["ok"] is False


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
            data={"latitude": "-17.8", "longitude": "31.05"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text

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
