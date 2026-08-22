"""Phase 3 secure BBOX GeoJSON API regression coverage."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sheplatform.core.auth import hash_password
from sheplatform.modules.map import layer_service


HARARE = layer_service.BBox(west=30.0, south=-19.0, east=32.0, north=-16.0)


def _user(db, role: str, email: str, org_id: int = 1) -> dict:
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, 'Map', 'User', %s, %s)",
        (email, hash_password("Test1234!"), role, org_id),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _site(db, code: str, *, org_id: int = 1, latitude=-17.82, longitude=31.05,
          status: str = "active") -> dict:
    db.execute(
        "INSERT INTO sites (site_code, site_name, site_type, status, latitude, longitude, org_id) "
        "VALUES (%s, %s, 'facility', %s, %s, %s, %s)",
        (code, f"Site {code}", status, latitude, longitude, org_id),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM sites WHERE site_code = %s", (code,)).fetchone())


def _incident(db, ref: str, *, org_id: int = 1, site_id=None, latitude=None,
              longitude=None, title="Safe short label", description="sensitive narrative") -> dict:
    db.execute(
        "INSERT INTO incidents (incident_ref, title, description, severity, status, incident_type, "
        "site_id, latitude, longitude, occurred_at, org_id) "
        "VALUES (%s, %s, %s, 'high', 'open', 'accident', %s, %s, %s, %s, %s)",
        (ref, title, description, site_id, latitude, longitude,
         datetime.now(timezone.utc).isoformat(), org_id),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM incidents WHERE incident_ref = %s", (ref,)).fetchone())


def _other_org(db, slug: str = "map-other") -> int:
    db.execute("INSERT INTO organisations (name, slug) VALUES (%s, %s)", ("Map Other", slug))
    db.commit()
    return db.execute("SELECT id FROM organisations WHERE slug = %s", (slug,)).fetchone()["id"]


def _login(client, email: str) -> None:
    response = client.post("/login", data={"email": email, "password": "Test1234!"})
    assert response.status_code in (200, 303)


class TestBBoxValidation:
    @pytest.mark.parametrize("raw", [
        "", "30,-19,32", "west,-19,32,-16", "nan,-19,32,-16",
        "-181,-19,32,-16", "30,-91,32,-16", "30,-16,32,-19",
        "32,-19,30,-16",
    ])
    def test_invalid_bbox_is_rejected(self, raw):
        with pytest.raises(ValueError):
            layer_service.parse_bbox(raw)

    def test_bbox_preserves_geojson_axis_order(self):
        bbox = layer_service.parse_bbox("30,-19,32,-16")
        assert bbox.as_list() == [30.0, -19.0, 32.0, -16.0]

    @pytest.mark.parametrize("requested,expected", [(-10, 1), (0, 1), (25, 25), (50000, 2000)])
    def test_limit_is_clamped(self, requested, expected):
        assert layer_service.clamp_limit(requested) == expected

    def test_four_named_bbox_values_are_supported(self):
        assert layer_service.parse_bbox_values("30", "-19", "32", "-16") == HARARE


class TestLayerQueries:
    def test_incident_bbox_tenant_fallback_and_safe_properties(self, db):
        mine = _site(db, "MINE")
        other_org = _other_org(db)
        theirs = _site(db, "THEIRS", org_id=other_org, latitude=-17.83, longitude=31.06)

        _incident(db, "INC-OWN", latitude=-17.81, longitude=31.04)
        _incident(db, "INC-SITE", site_id=mine["id"])
        _incident(db, "INC-OUTSIDE", latitude=-20.0, longitude=28.5)
        _incident(db, "INC-NO-LOCATION")
        _incident(db, "INC-FOREIGN-SITE", site_id=theirs["id"])
        _incident(db, "INC-OTHER-ORG", org_id=other_org, latitude=-17.8, longitude=31.0)

        result = layer_service.get_layer_collection(
            db, layer_key="incidents", org_id=1, bbox=HARARE)

        refs = {feature["properties"]["ref"] for feature in result["features"]}
        assert refs == {"INC-OWN", "INC-SITE"}
        assert result["meta"]["unlocated"] == 2
        feature = next(item for item in result["features"]
                       if item["properties"]["ref"] == "INC-OWN")
        assert feature["geometry"]["coordinates"] == [31.04, -17.81]
        assert set(feature["properties"]) <= {
            "id", "ref", "label", "status", "severity", "type", "site_name", "timestamp", "url"
        }
        assert "description" not in feature["properties"]
        assert "location" not in feature["properties"]

    def test_missing_org_fails_closed(self, db):
        _site(db, "NO-ORG-CLOSE")
        result = layer_service.get_layer_collection(
            db, layer_key="facilities", org_id=None, bbox=HARARE)
        assert result["features"] == []
        assert result["meta"]["unlocated"] == 0

    def test_limit_plus_one_sets_truncation(self, db):
        _incident(db, "INC-LIMIT-1", latitude=-17.8, longitude=31.0)
        _incident(db, "INC-LIMIT-2", latitude=-17.9, longitude=31.1)
        result = layer_service.get_layer_collection(
            db, layer_key="incidents", org_id=1, bbox=HARARE, limit=1)
        assert result["meta"] == {
            "layer": "incidents", "returned": 1, "limit": 1,
            "truncated": True, "unlocated": 0,
        }

    def test_user_controlled_labels_are_normalized_and_bounded(self, db):
        _incident(
            db, "INC-LONG-LABEL", latitude=-17.8, longitude=31.0,
            title="Line one\n" + ("x" * 300),
        )
        result = layer_service.get_layer_collection(
            db, layer_key="incidents", org_id=1, bbox=HARARE)
        label = result["features"][0]["properties"]["label"]
        assert "\n" not in label
        assert len(label) == 160
        assert label.endswith("...")

    def test_filters_are_parameterized_and_applied(self, db):
        _incident(db, "INC-FILTER-HIGH", latitude=-17.8, longitude=31.0)
        db.execute(
            "UPDATE incidents SET severity = 'low', incident_type = 'vehicle' "
            "WHERE incident_ref = 'INC-FILTER-HIGH'")
        _incident(db, "INC-FILTER-LOW", latitude=-17.9, longitude=31.1)
        result = layer_service.get_layer_collection(
            db, layer_key="incidents", org_id=1, bbox=HARARE,
            filters={"severity": "low", "type": "vehicle"})
        assert [f["properties"]["ref"] for f in result["features"]] == ["INC-FILTER-HIGH"]

    def test_every_registry_builder_returns_its_real_source(self, db):
        site = _site(db, "ALL-LAYERS")
        incident = _incident(db, "INC-ALL", site_id=site["id"])
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO permits (permit_ref, permit_type, title, status, site_id, org_id, created_at) "
            "VALUES ('PTW-ALL', 'general', 'Permit label', 'active', %s, 1, %s)", (site["id"], now))
        db.execute(
            "INSERT INTO inspections (inspection_ref, title, inspection_type, status, site_id, org_id, created_at) "
            "VALUES ('INSP-ALL', 'Inspection label', 'workplace', 'scheduled', %s, 1, %s)",
            (site["id"], now))
        inspection = db.execute(
            "SELECT id FROM inspections WHERE inspection_ref = 'INSP-ALL'").fetchone()
        db.execute(
            "INSERT INTO eia_projects (project_ref, project_name, project_type, status, site_id, org_id, created_at) "
            "VALUES ('EIA-ALL', 'EIA label', 'screening', 'screening', %s, 1, %s)", (site["id"], now))
        db.execute(
            "INSERT INTO emergency_events (event_ref, title, severity, status, site_id, org_id, created_at) "
            "VALUES ('EM-ALL', 'Emergency label', 'critical', 'active', %s, 1, %s)", (site["id"], now))
        db.execute(
            "INSERT INTO vendors (vendor_ref, company_name, risk_profile, status, org_id) "
            "VALUES ('VEN-ALL', 'Vendor label', 'medium', 'active', 1)")
        vendor = db.execute("SELECT id FROM vendors WHERE vendor_ref = 'VEN-ALL'").fetchone()
        db.execute(
            "INSERT INTO contractor_inductions (vendor_id, site_id, induction_type, status, created_at) "
            "VALUES (%s, %s, 'site_specific', 'valid', %s)", (vendor["id"], site["id"], now))
        db.execute(
            "INSERT INTO corrective_actions (action_ref, source_type, source_id, title, priority, status, org_id, created_at) "
            "VALUES ('CA-ALL', 'incident', %s, 'Action label', 'high', 'open', 1, %s)",
            (incident["id"], now))
        db.execute(
            "INSERT INTO assets (asset_ref, name, asset_type, status, site_id, org_id, created_at) "
            "VALUES ('AST-ALL', 'Asset label', 'other', 'active', %s, 1, %s)", (site["id"], now))
        db.execute(
            "INSERT INTO observations (obs_ref, obs_type, title, severity, status, site_id, org_id, created_at) "
            "VALUES ('OBS-ALL', 'hazard', 'Observation label', 'medium', 'open', %s, 1, %s)",
            (site["id"], now))
        db.execute(
            "INSERT INTO risks (risk_ref, hazard_description, risk_category, likelihood, impact, status, "
            "source_type, source_id, org_id, created_at) "
            "VALUES ('RSK-ALL', 'Do not expose this narrative', 'operational', 2, 3, 'open', "
            "'inspection', %s, 1, %s)", (inspection["id"], now))
        db.commit()

        for key in layer_service.LAYER_REGISTRY:
            result = layer_service.get_layer_collection(
                db, layer_key=key, org_id=1, bbox=HARARE)
            assert len(result["features"]) == 1, key
            assert result["features"][0]["geometry"]["type"] == "Point"
        risk = layer_service.get_layer_collection(
            db, layer_key="risks", org_id=1, bbox=HARARE)["features"][0]
        assert risk["properties"]["label"] == "RSK-ALL"
        assert "Do not expose" not in str(risk)
        facility = layer_service.get_facility_detail(
            db, site_id=site["id"], org_id=1,
            count_layers=[key for key in layer_service.LAYER_REGISTRY if key != "facilities"],
        )
        assert facility is not None
        assert facility["counts"] == {
            "incidents": 1, "permits": 1, "inspections": 1, "environmental": 1,
            "emergencies": 1, "contractors": 1, "corrective_actions": 1,
            "assets": 1, "observations": 1, "risks": 1,
        }

    def test_unlocated_queue_is_bounded_and_safe(self, db):
        _incident(db, "INC-UNLOC-1", title="Queue label")
        _incident(db, "INC-UNLOC-2", title="Second label")
        result = layer_service.get_unlocated_records(
            db, layer_key="incidents", org_id=1, limit=1)
        assert result["meta"]["returned"] == 1
        assert result["meta"]["truncated"] is True
        assert "geometry" not in result["records"][0]
        assert "description" not in result["records"][0]


class TestLayerHttp:
    def test_manifest_only_lists_authorized_layers_and_is_private(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _user(db, "she_hod", "map-hod@test.com")
        finally:
            db.close()
        _login(client, "map-hod@test.com")
        response = client.get("/map/api/manifest?bbox=30,-19,32,-16")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        keys = {layer["key"] for layer in response.json()["layers"]}
        assert "facilities" in keys
        assert "incidents" not in keys
        assert "inspections" in keys

    def test_unauthorized_layer_is_403_without_counts(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _user(db, "she_hod", "map-denied@test.com")
            _incident(db, "INC-DENIED", latitude=-17.8, longitude=31.0)
        finally:
            db.close()
        _login(client, "map-denied@test.com")
        response = client.get("/map/api/layer/incidents?bbox=30,-19,32,-16")
        assert response.status_code == 403
        assert response.json() == {"detail": "Forbidden"}
        assert response.headers["cache-control"] == "private, no-store"

    def test_unknown_and_invalid_requests_are_private(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _user(db, "she_officer", "map-errors@test.com")
        finally:
            db.close()
        _login(client, "map-errors@test.com")
        unknown = client.get("/map/api/layer/not-real?bbox=30,-19,32,-16")
        invalid = client.get("/map/api/layer/incidents?bbox=32,-19,30,-16")
        assert unknown.status_code == 404
        assert invalid.status_code == 400
        assert unknown.headers["cache-control"] == "private, no-store"
        assert invalid.headers["cache-control"] == "private, no-store"

    def test_layer_accepts_documented_named_bounds(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _user(db, "she_officer", "map-named-bounds@test.com")
            _incident(db, "INC-NAMED-BOUNDS", latitude=-17.8, longitude=31.0)
        finally:
            db.close()
        _login(client, "map-named-bounds@test.com")
        response = client.get(
            "/map/api/layer/incidents?min_lng=30&min_lat=-19&max_lng=32&max_lat=-16&limit=999999")
        assert response.status_code == 200
        assert response.json()["features"][0]["properties"]["ref"] == "INC-NAMED-BOUNDS"
        assert response.json()["meta"]["limit"] == 2000
        assert response.headers["cache-control"] == "private, no-store"

    def test_layer_and_facility_endpoints_enforce_org_scope(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _user(db, "she_officer", "map-org@test.com")
            other_org = _other_org(db, "http-map-other")
            other_site = _site(db, "HTTP-OTHER", org_id=other_org)
            _incident(db, "INC-HTTP-OTHER", org_id=other_org, site_id=other_site["id"])
        finally:
            db.close()
        _login(client, "map-org@test.com")
        layer = client.get("/map/api/layer/incidents?bbox=30,-19,32,-16")
        facility = client.get(f"/map/api/facility/{other_site['id']}")
        assert layer.status_code == 200
        assert layer.json()["features"] == []
        assert facility.status_code == 404

    def test_facility_omits_unauthorized_module_counts(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _user(db, "she_hod", "map-counts@test.com")
            site = _site(db, "COUNT-SITE")
            _incident(db, "INC-COUNT-HIDDEN", site_id=site["id"])
            db.execute(
                "INSERT INTO inspections (inspection_ref, title, status, site_id, org_id) "
                "VALUES ('INSP-COUNT', 'Inspection', 'scheduled', %s, 1)", (site["id"],))
            db.commit()
        finally:
            db.close()
        _login(client, "map-counts@test.com")
        response = client.get(f"/map/api/facility/{site['id']}")
        assert response.status_code == 200
        counts = response.json()["counts"]
        assert counts["inspections"] == 1
        assert "incidents" not in counts
        assert response.headers["cache-control"] == "private, no-store"

    def test_unlocated_endpoint_requires_source_capability(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _user(db, "she_hod", "map-unlocated-denied@test.com")
        finally:
            db.close()
        _login(client, "map-unlocated-denied@test.com")
        response = client.get("/map/api/unlocated/incidents")
        assert response.status_code == 403
        assert response.json() == {"detail": "Forbidden"}
