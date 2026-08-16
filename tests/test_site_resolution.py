"""Phase 2C exact historical site resolution and reviewed queue coverage."""
from __future__ import annotations

import json

from sheplatform.core.auth import hash_password
from sheplatform.database import get_db
from sheplatform.modules.map import site_resolution_service as resolution


def _organisation(db, slug: str = "resolution-other") -> int:
    db.execute(
        "INSERT INTO organisations (name, slug) VALUES (%s, %s)",
        (slug.replace("-", " ").title(), slug),
    )
    db.commit()
    return db.execute(
        "SELECT id FROM organisations WHERE slug = %s", (slug,)
    ).fetchone()["id"]


def _user(db, org_id: int, email: str, role: str = "she_manager") -> dict:
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (email, hash_password("Test1234!"), "Site", "Reviewer", role, org_id),
    )
    db.commit()
    return dict(db.execute(
        "SELECT * FROM users WHERE email = %s", (email,)
    ).fetchone())


def _site(
    db,
    org_id: int,
    code: str,
    name: str,
    *,
    status: str = "active",
) -> dict:
    db.execute(
        "INSERT INTO sites (site_code, site_name, city, region, status, org_id) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (code, name, "Harare", "Harare", status, org_id),
    )
    db.commit()
    return dict(db.execute(
        "SELECT * FROM sites WHERE site_code = %s", (code,)
    ).fetchone())


def _record(db, record_type: str, org_id: int, suffix: str, location: str = "") -> dict:
    if record_type == "permit":
        db.execute(
            "INSERT INTO permits (permit_ref, permit_type, title, site_location, org_id) "
            "VALUES (%s,'general',%s,%s,%s)",
            (f"PTW-RES-{suffix}", f"Permit {suffix}", location, org_id),
        )
        table, ref_col, ref = "permits", "permit_ref", f"PTW-RES-{suffix}"
    elif record_type == "inspection":
        db.execute(
            "INSERT INTO inspections (inspection_ref, title, inspection_type, site_location, org_id) "
            "VALUES (%s,%s,'safety',%s,%s)",
            (f"INSP-RES-{suffix}", f"Inspection {suffix}", location, org_id),
        )
        table, ref_col, ref = "inspections", "inspection_ref", f"INSP-RES-{suffix}"
    elif record_type == "eia":
        db.execute(
            "INSERT INTO eia_projects (project_ref, project_name, location, org_id) "
            "VALUES (%s,%s,%s,%s)",
            (f"EIA-RES-{suffix}", f"EIA {suffix}", location, org_id),
        )
        table, ref_col, ref = "eia_projects", "project_ref", f"EIA-RES-{suffix}"
    elif record_type == "emergency":
        db.execute(
            "INSERT INTO emergency_events (event_ref, title, severity, site_location, org_id) "
            "VALUES (%s,%s,'medium',%s,%s)",
            (f"EMG-RES-{suffix}", f"Emergency {suffix}", location, org_id),
        )
        table, ref_col, ref = "emergency_events", "event_ref", f"EMG-RES-{suffix}"
    else:
        raise AssertionError(record_type)
    db.commit()
    return dict(db.execute(
        f"SELECT * FROM {table} WHERE {ref_col} = %s", (ref,)
    ).fetchone())


def _login(client, email: str) -> str:
    response = client.post(
        "/login", data={"email": email, "password": "Test1234!"}
    )
    assert response.status_code == 200
    return client.cookies.get("she_csrf", "")


def test_exact_resolver_is_normalized_conservative_and_tenant_scoped(db, org_id):
    _site(db, org_id, "CENTRAL-01", "Central Depot")
    _site(db, org_id, "SHARED-01", "Shared Depot")
    _site(db, org_id, "SHARED-02", "Shared Depot")
    _site(db, org_id, "INACTIVE-01", "Inactive Depot", status="inactive")
    other_org = _organisation(db)
    _site(db, other_org, "OTHER-01", "Other Organisation Depot")

    by_code = resolution.resolve_exact_site(
        db, org_id=org_id, original_text="  central-01  "
    )
    assert by_code["status"] == "matched"
    assert [site["site_code"] for site in by_code["candidates"]] == ["CENTRAL-01"]

    by_name = resolution.resolve_exact_site(
        db, org_id=org_id, original_text="CENTRAL   DEPOT"
    )
    assert by_name["status"] == "matched"
    assert by_name["normalized"] == "central depot"

    ambiguous = resolution.resolve_exact_site(
        db, org_id=org_id, original_text="shared depot"
    )
    assert ambiguous["status"] == "ambiguous"
    assert {site["site_code"] for site in ambiguous["candidates"]} == {
        "SHARED-01", "SHARED-02",
    }

    for original_text in (
        "Central Depot north loading bay",
        "Inactive Depot",
        "Other Organisation Depot",
        "",
    ):
        result = resolution.resolve_exact_site(
            db, org_id=org_id, original_text=original_text
        )
        assert result["status"] == "unresolved"
        assert result["candidates"] == []

    assert resolution.resolve_exact_site(
        db, org_id=None, original_text="Central Depot"
    )["status"] == "unresolved"


def test_review_queue_reports_all_record_types_outcomes_and_unlocated_counts(db, org_id):
    _site(db, org_id, "EXACT-01", "Exact Depot")
    _site(db, org_id, "DUP-01", "Duplicate Depot")
    _site(db, org_id, "DUP-02", "Duplicate Depot")
    _record(db, "permit", org_id, "PENDING", "exact-01")
    _record(db, "inspection", org_id, "AMBIG", "Duplicate Depot")
    _record(db, "eia", org_id, "UNKNOWN", "Proposed greenfield")
    _record(db, "emergency", org_id, "BLANK", "")

    other_org = _organisation(db, "resolution-queue-other")
    _site(db, other_org, "QUEUE-OTHER", "Other Queue Site")
    _record(db, "permit", other_org, "OTHER", "Other Queue Site")

    queue = resolution.list_resolution_queue(
        db, org_id=org_id, review_status="pending", limit=100
    )
    assert queue["counts"] == {
        "total": 4, "linked": 0, "unlinked": 4, "pending": 4, "skipped": 0,
    }
    assert queue["truncated"] is False
    assert {item["record_type"] for item in queue["records"]} == {
        "permit", "inspection", "eia", "emergency",
    }
    outcomes = {
        item["record_type"]: item["resolver"]["status"]
        for item in queue["records"]
    }
    assert outcomes == {
        "permit": "matched",
        "inspection": "ambiguous",
        "eia": "unresolved",
        "emergency": "unresolved",
    }
    assert {site["site_code"] for site in queue["available_sites"]} == {
        "EXACT-01", "DUP-01", "DUP-02",
    }
    assert all("latitude" not in site and "longitude" not in site
               for site in queue["available_sites"])
    allowed_record_fields = {
        "id", "record_ref", "original_text", "decision", "decision_note",
        "reviewed_at", "record_type", "record_type_label", "resolver",
        "current_site",
    }
    assert all(set(item) <= allowed_record_fields for item in queue["records"])
    assert all("normalized" not in item["resolver"] for item in queue["records"])
    assert resolution.list_resolution_queue(
        db, org_id=None
    )["records"] == []


def test_reviewed_resolution_is_atomic_preserves_text_and_audits(db, org_id):
    reviewer = _user(db, org_id, "resolution-manager@test.com")
    site = _site(db, org_id, "RESOLVE-01", "Resolved Depot")
    permit = _record(db, "permit", org_id, "APPLY", "Resolved Depot")

    result = resolution.resolve_record(
        db, record_type="permit", record_id=permit["id"], site_id=site["id"],
        org_id=org_id, reviewed_by=reviewer["id"], decision_note="Verified register",
    )
    assert result["ok"] is True
    updated = db.execute(
        "SELECT site_id, site_location FROM permits WHERE id = %s", (permit["id"],)
    ).fetchone()
    assert updated["site_id"] == site["id"]
    assert updated["site_location"] == "Resolved Depot"

    decision = db.execute(
        "SELECT * FROM site_resolution_decisions WHERE record_type = 'permit' "
        "AND record_id = %s AND org_id = %s",
        (permit["id"], org_id),
    ).fetchone()
    assert decision["decision"] == "resolved"
    assert decision["original_text"] == "Resolved Depot"
    assert decision["resolved_site_id"] == site["id"]

    audit = db.execute(
        "SELECT * FROM audit_log WHERE action = 'site_resolution.resolve' "
        "AND entity_type = 'permits' AND entity_id = %s",
        (permit["id"],),
    ).fetchone()
    assert audit["org_id"] == org_id
    assert audit["user_id"] == reviewer["id"]
    assert json.loads(audit["old_value"])["original_text"] == "Resolved Depot"
    assert json.loads(audit["new_value"])["site_id"] == site["id"]
    resolved_queue = resolution.list_resolution_queue(
        db, org_id=org_id, review_status="resolved"
    )
    resolved_item = next(
        item for item in resolved_queue["records"]
        if item["record_ref"] == "PTW-RES-APPLY"
    )
    assert resolved_item["current_site"] == {
        "id": site["id"], "site_code": "RESOLVE-01", "site_name": "Resolved Depot",
    }
    assert resolved_item["original_text"] == "Resolved Depot"


def test_resolution_revalidates_record_site_tenant_and_active_state(db, org_id):
    reviewer = _user(db, org_id, "resolution-guard@test.com")
    own_record = _record(db, "inspection", org_id, "GUARD", "Guard Site")
    inactive = _site(db, org_id, "GUARD-INACTIVE", "Guard Site", status="inactive")
    other_org = _organisation(db, "resolution-guard-other")
    other_site = _site(db, other_org, "GUARD-OTHER", "Guard Site")
    other_record = _record(db, "inspection", other_org, "OTHER", "Guard Site")

    inactive_result = resolution.resolve_record(
        db, record_type="inspection", record_id=own_record["id"],
        site_id=inactive["id"], org_id=org_id, reviewed_by=reviewer["id"],
    )
    assert inactive_result == {"ok": False, "message": resolution.SITE_NOT_FOUND_MESSAGE}
    cross_site = resolution.resolve_record(
        db, record_type="inspection", record_id=own_record["id"],
        site_id=other_site["id"], org_id=org_id, reviewed_by=reviewer["id"],
    )
    assert cross_site == {"ok": False, "message": resolution.SITE_NOT_FOUND_MESSAGE}
    cross_record = resolution.resolve_record(
        db, record_type="inspection", record_id=other_record["id"],
        site_id=other_site["id"], org_id=org_id, reviewed_by=reviewer["id"],
    )
    assert cross_record == {"ok": False, "message": resolution.RECORD_NOT_FOUND_MESSAGE}
    assert db.execute(
        "SELECT site_id FROM inspections WHERE id = %s", (own_record["id"],)
    ).fetchone()["site_id"] is None


def test_skip_is_persistent_audited_and_leaves_record_unlinked(db, org_id):
    reviewer = _user(db, org_id, "resolution-skip@test.com")
    event = _record(db, "emergency", org_id, "SKIP", "Temporary response point")

    result = resolution.skip_record(
        db, record_type="emergency", record_id=event["id"], org_id=org_id,
        reviewed_by=reviewer["id"], decision_note="Needs field confirmation",
    )
    assert result["ok"] is True
    assert db.execute(
        "SELECT site_id FROM emergency_events WHERE id = %s", (event["id"],)
    ).fetchone()["site_id"] is None

    pending = resolution.list_resolution_queue(
        db, org_id=org_id, review_status="pending"
    )
    skipped = resolution.list_resolution_queue(
        db, org_id=org_id, review_status="skipped"
    )
    assert pending["records"] == []
    assert pending["counts"]["unlinked"] == 1
    assert pending["counts"]["skipped"] == 1
    assert skipped["records"][0]["record_ref"] == "EMG-RES-SKIP"
    assert skipped["records"][0]["decision_note"] == "Needs field confirmation"
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'site_resolution.skip' "
        "AND entity_id = %s", (event["id"],)
    ).fetchone()[0] == 1


def test_create_site_action_is_atomic_unlocated_and_audited(db, org_id):
    reviewer = _user(db, org_id, "resolution-create@test.com")
    project = _record(db, "eia", org_id, "CREATE", "New solar compound")

    result = resolution.create_site_and_resolve(
        db, record_type="eia", record_id=project["id"],
        site_code="SOLAR-NEW", site_name="New Solar Compound", city="Harare",
        region="Harare", site_type="facility", org_id=org_id,
        reviewed_by=reviewer["id"],
    )
    assert result["ok"] is True
    site = db.execute(
        "SELECT * FROM sites WHERE site_code = 'SOLAR-NEW'"
    ).fetchone()
    assert site["org_id"] == org_id
    assert site["latitude"] is None and site["longitude"] is None
    project_after = db.execute(
        "SELECT site_id, location FROM eia_projects WHERE id = %s", (project["id"],)
    ).fetchone()
    assert project_after["site_id"] == site["id"]
    assert project_after["location"] == "New solar compound"
    decision = db.execute(
        "SELECT * FROM site_resolution_decisions WHERE record_type = 'eia' "
        "AND record_id = %s", (project["id"],)
    ).fetchone()
    assert decision["decision"] == "site_created"
    actions = {
        row["action"] for row in db.execute(
            "SELECT action FROM audit_log WHERE user_id = %s", (reviewer["id"],)
        ).fetchall()
    }
    assert {"site_resolution.create_site", "site_resolution.resolve"} <= actions


def test_create_site_collision_rolls_back_without_link_or_decision(db, org_id):
    reviewer = _user(db, org_id, "resolution-collision@test.com")
    project = _record(db, "eia", org_id, "COLLISION", "Collision compound")
    other_org = _organisation(db, "resolution-collision-other")
    _site(db, other_org, "COLLISION-CODE", "Other tenant site")

    try:
        resolution.create_site_and_resolve(
            db, record_type="eia", record_id=project["id"],
            site_code="COLLISION-CODE", site_name="Collision compound",
            org_id=org_id, reviewed_by=reviewer["id"],
        )
    except ValueError as exc:
        assert "choose another site code" in str(exc)
    else:
        raise AssertionError("global site-code collision should be rejected")

    assert db.execute(
        "SELECT site_id FROM eia_projects WHERE id = %s", (project["id"],)
    ).fetchone()["site_id"] is None
    assert db.execute(
        "SELECT COUNT(*) FROM site_resolution_decisions WHERE record_type = 'eia' "
        "AND record_id = %s AND org_id = %s", (project["id"], org_id),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE user_id = %s", (reviewer["id"],)
    ).fetchone()[0] == 0


def test_resolution_http_routes_are_private_role_gated_and_tenant_safe(client):
    db = get_db()
    try:
        org_id = db.execute(
            "SELECT id FROM organisations WHERE slug = 'test-org'"
        ).fetchone()["id"]
        manager = _user(db, org_id, "resolution-http-manager@test.com")
        _user(db, org_id, "resolution-http-officer@test.com", role="she_officer")
        own_site = _site(db, org_id, "HTTP-OWN", "HTTP Own Site")
        own_record = _record(db, "permit", org_id, "HTTP", "HTTP Own Site")
        create_record = _record(db, "eia", org_id, "HTTP-CREATE", "HTTP New Site")
        other_org = _organisation(db, "resolution-http-other")
        other_site = _site(db, other_org, "HTTP-OTHER", "HTTP Other Site")
        other_record = _record(db, "permit", other_org, "HTTP-OTHER", "HTTP Other Site")
    finally:
        db.close()

    _login(client, "resolution-http-officer@test.com")
    assert client.get("/map/api/site-resolution").status_code == 403
    assert 'id="site-resolution-review"' not in client.get("/map").text
    client.cookies.clear()

    csrf = _login(client, "resolution-http-manager@test.com")
    headers = {"X-CSRF-Token": csrf}
    assert 'id="site-resolution-review"' in client.get("/map").text
    queue_response = client.get("/map/api/site-resolution")
    assert queue_response.status_code == 200
    assert queue_response.headers["cache-control"] == "private, no-store"
    refs = {item["record_ref"] for item in queue_response.json()["records"]}
    assert "PTW-RES-HTTP" in refs
    assert "PTW-RES-HTTP-OTHER" not in refs

    tampered_record = client.post(
        f"/map/api/site-resolution/permit/{other_record['id']}/resolve",
        data={"site_id": other_site["id"]}, headers=headers,
    )
    assert tampered_record.status_code == 404
    assert tampered_record.json()["message"] == resolution.RECORD_NOT_FOUND_MESSAGE
    tampered_site = client.post(
        f"/map/api/site-resolution/permit/{own_record['id']}/resolve",
        data={"site_id": other_site["id"]}, headers=headers,
    )
    assert tampered_site.status_code == 400
    assert tampered_site.json()["message"] == resolution.SITE_NOT_FOUND_MESSAGE

    applied = client.post(
        f"/map/api/site-resolution/permit/{own_record['id']}/resolve",
        data={"site_id": own_site["id"], "decision_note": "HTTP reviewed"},
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    assert applied.headers["cache-control"] == "private, no-store"
    assert applied.json()["site"]["site_code"] == "HTTP-OWN"

    created = client.post(
        f"/map/api/site-resolution/eia/{create_record['id']}/create-site",
        data={"site_code": "HTTP-NEW", "site_name": "HTTP New Site",
              "city": "Harare", "region": "Harare", "site_type": "facility"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    assert created.json()["site"]["site_code"] == "HTTP-NEW"

    db = get_db()
    try:
        assert db.execute(
            "SELECT site_id FROM permits WHERE id = %s", (own_record["id"],)
        ).fetchone()["site_id"] == own_site["id"]
        assert db.execute(
            "SELECT site_id FROM eia_projects WHERE id = %s", (create_record["id"],)
        ).fetchone()["site_id"] == created.json()["site"]["id"]
        assert db.execute(
            "SELECT site_id FROM permits WHERE id = %s", (other_record["id"],)
        ).fetchone()["site_id"] is None
        assert db.execute(
            "SELECT COUNT(*) FROM audit_log WHERE user_id = %s "
            "AND action = 'site_resolution.resolve'", (manager["id"],)
        ).fetchone()[0] == 2
    finally:
        db.close()
