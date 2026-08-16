"""Phase 2B org-scoped site-picker and HTTP submission coverage."""
from __future__ import annotations

import json

from sheplatform.core.auth import hash_password
from sheplatform.database import get_db
from sheplatform.modules.map.site_relationship_service import (
    SITE_UNAVAILABLE_MESSAGE,
    list_active_sites,
)


def _seed_picker_context(email: str = "picker@test.com") -> dict:
    db = get_db()
    try:
        org_id = db.execute(
            "SELECT id FROM organisations WHERE slug = 'test-org'"
        ).fetchone()["id"]
        db.execute(
            "INSERT INTO organisations (name, slug) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            ("Other Organisation", "picker-other"),
        )
        other_org_id = db.execute(
            "SELECT id FROM organisations WHERE slug = 'picker-other'"
        ).fetchone()["id"]
        db.execute(
            "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (email, hash_password("Test1234!"), "Site", "Picker", "she_officer", org_id),
        )
        user = db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
        for code, name, status, site_org in (
            ("OWN-ACTIVE", "Own Active Site", "active", org_id),
            ("OWN-INACTIVE", "Own Inactive Site", "inactive", org_id),
            ("OTHER-ACTIVE", "Other Active Site", "active", other_org_id),
        ):
            db.execute(
                "INSERT INTO sites (site_code, site_name, city, status, org_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (code, name, "Harare", status, site_org),
            )
        db.commit()
        sites = {
            row["site_code"]: dict(row)
            for row in db.execute(
                "SELECT * FROM sites WHERE site_code IN (%s, %s, %s)",
                ("OWN-ACTIVE", "OWN-INACTIVE", "OTHER-ACTIVE"),
            ).fetchall()
        }
        return {"org_id": org_id, "user": dict(user), "sites": sites}
    finally:
        db.close()


def _login(client, email: str = "picker@test.com") -> str:
    response = client.post(
        "/login", data={"email": email, "password": "Test1234!"}
    )
    assert response.status_code == 200
    return client.cookies.get("she_csrf", "")


def _approved_assessment(user_id: int) -> tuple[int, int]:
    from sheplatform.modules.vendor_compliance import data_service as vendor_service
    from sheplatform.modules.vendor_compliance import risk_assessment_service

    db = get_db()
    try:
        vendor = vendor_service.create_vendor(
            db, company_name="Picker Test Contractor", created_by=user_id
        )
        assessment = risk_assessment_service.create_assessment(
            db,
            vendor_id=vendor["id"],
            scope_of_work="Picker HTTP test",
            risk_rating="low",
            created_by=user_id,
        )
        risk_assessment_service.approve_assessment(db, assessment["id"], user_id)
        return vendor["id"], assessment["id"]
    finally:
        db.close()


def test_site_picker_fails_closed_without_organisation(db):
    assert list_active_sites(db, None) == []


def test_all_create_pages_show_only_the_tenants_active_sites(client):
    seeded = _seed_picker_context()
    _login(client)

    for path in ("/permits", "/inspections", "/eia", "/emergency"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert 'name="site_id"' in response.text
        assert "OWN-ACTIVE" in response.text
        assert "Own Active Site" in response.text
        assert "OWN-INACTIVE" not in response.text
        assert "OTHER-ACTIVE" not in response.text
        assert "Location details" in response.text

    other_site = seeded["sites"]["OTHER-ACTIVE"]
    response = client.post(
        "/emergency/api/events",
        data={
            "title": "Tampered site",
            "severity": "high",
            "site_id": other_site["id"],
        },
        headers={"X-CSRF-Token": client.cookies.get("she_csrf", "")},
    )
    assert response.status_code == 400
    assert response.json()["message"] == SITE_UNAVAILABLE_MESSAGE


def test_site_picker_submissions_persist_links_lists_and_audits(client):
    seeded = _seed_picker_context()
    user = seeded["user"]
    site = seeded["sites"]["OWN-ACTIVE"]
    vendor_id, assessment_id = _approved_assessment(user["id"])
    csrf = _login(client)
    headers = {"X-CSRF-Token": csrf}

    requests = (
        (
            "/permits/api/create",
            {
                "title": "Linked permit",
                "description": "",
                "permit_type": "general",
                "vendor_id": vendor_id,
                "risk_assessment_id": assessment_id,
                "site_id": site["id"],
                "site_location": "North loading bay",
            },
            201,
        ),
        (
            "/inspections/api/create",
            {
                "title": "Linked inspection",
                "inspection_type": "safety",
                "inspector_id": user["id"],
                "scheduled_date": "2099-01-01",
                "site_id": site["id"],
                "site_location": "Warehouse aisle 4",
            },
            200,
        ),
        (
            "/eia/api/projects",
            {
                "project_name": "Linked EIA",
                "site_id": site["id"],
                "location": "Proposed substation footprint",
            },
            201,
        ),
        (
            "/emergency/api/events",
            {
                "title": "Linked emergency",
                "severity": "medium",
                "site_id": site["id"],
                "site_location": "Generator room",
            },
            201,
        ),
    )
    for path, data, expected_status in requests:
        response = client.post(path, data=data, headers=headers)
        assert response.status_code == expected_status, response.text
        assert response.json()["ok"] is True

    list_expectations = (
        ("/permits/api/list", "permits", "North loading bay"),
        ("/inspections/api/list", "inspections", "Warehouse aisle 4"),
        ("/eia/api/projects", "projects", "Proposed substation footprint"),
        ("/emergency/api/events", "emergencies", "Generator room"),
    )
    for path, key, location in list_expectations:
        response = client.get(path)
        assert response.status_code == 200
        record = response.json()[key][0]
        assert record["site_id"] == site["id"]
        assert record["site_code"] == "OWN-ACTIVE"
        assert record["site_name"] == "Own Active Site"
        assert (record.get("location") or record.get("site_location")) == location
        assert "latitude" not in record
        assert "longitude" not in record

    db = get_db()
    try:
        audit_rows = db.execute(
            "SELECT action, new_value FROM audit_log WHERE action IN (%s, %s, %s, %s)",
            ("permit.create", "inspection.scheduled", "eia.project.create",
             "emergency.create"),
        ).fetchall()
        audit_values = {row["action"]: json.loads(row["new_value"]) for row in audit_rows}
        assert set(audit_values) == {
            "permit.create", "inspection.scheduled", "eia.project.create",
            "emergency.create",
        }
        assert all(value["site_id"] == site["id"] for value in audit_values.values())
    finally:
        db.close()
