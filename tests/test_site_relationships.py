"""Phase 2 canonical-site relationship and tenant-boundary coverage."""
from __future__ import annotations

import sqlite3

import pytest

from sheplatform.modules.map.site_relationship_service import SITE_UNAVAILABLE_MESSAGE


TABLE_BY_KIND = {
    "permit": "permits",
    "inspection": "inspections",
    "eia": "eia_projects",
    "emergency": "emergency_events",
}


def _create_org(db, name: str, slug: str) -> int:
    db.execute("INSERT INTO organisations (name, slug) VALUES (%s, %s)", (name, slug))
    db.commit()
    return db.execute(
        "SELECT id FROM organisations WHERE slug = %s", (slug,)
    ).fetchone()["id"]


def _create_user(db, org_id: int, email: str = "site-user@test.com") -> dict:
    from sheplatform.core.auth import hash_password

    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, 'Site', 'User', 'she_officer', %s)",
        (email, hash_password("Test1234!"), org_id),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _create_site(db, org_id: int, code: str, status: str = "active") -> dict:
    db.execute(
        "INSERT INTO sites (site_code, site_name, status, org_id) VALUES (%s, %s, %s, %s)",
        (code, f"Site {code}", status, org_id),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM sites WHERE site_code = %s", (code,)).fetchone())


def _approved_assessment(db, user_id: int) -> tuple[int, int]:
    from sheplatform.modules.vendor_compliance import data_service as vendor_service
    from sheplatform.modules.vendor_compliance import risk_assessment_service

    vendor = vendor_service.create_vendor(
        db, company_name=f"Site Contractor {user_id}", created_by=user_id
    )
    assessment = risk_assessment_service.create_assessment(
        db,
        vendor_id=vendor["id"],
        scope_of_work="Site relationship test",
        risk_rating="low",
        created_by=user_id,
    )
    risk_assessment_service.approve_assessment(db, assessment["id"], user_id)
    return vendor["id"], assessment["id"]


def _create_record(db, kind: str, user: dict, site_id: int | None, org_id=None) -> dict:
    org_id = user["org_id"] if org_id is None else org_id
    if kind == "permit":
        from sheplatform.modules.permit_to_work import data_service

        vendor_id, assessment_id = _approved_assessment(db, user["id"])
        result = data_service.create_permit(
            db,
            permit_type="general",
            title="Site-linked permit",
            description="",
            vendor_id=vendor_id,
            risk_assessment_id=assessment_id,
            site_location="North loading bay",
            site_id=site_id,
            created_by=user["id"],
            org_id=org_id,
        )
        return result["permit"]
    if kind == "inspection":
        from sheplatform.modules.inspections import data_service

        return data_service.schedule_inspection(
            db,
            title="Site-linked inspection",
            inspection_type="safety",
            site_location="North loading bay",
            site_id=site_id,
            scheduled_date="2099-01-01T00:00:00+00:00",
            inspector_id=user["id"],
            created_by=user["id"],
            org_id=org_id,
        )
    if kind == "eia":
        from sheplatform.modules.eia import data_service

        return data_service.create_project(
            db,
            project_name="Site-linked EIA",
            location="North loading bay",
            site_id=site_id,
            created_by=user["id"],
            org_id=org_id,
        )
    if kind == "emergency":
        from sheplatform.modules.emergency import data_service

        return data_service.create_emergency(
            db,
            title="Site-linked emergency",
            description="",
            severity="low",
            site_location="North loading bay",
            site_id=site_id,
            created_by=user["id"],
            org_id=org_id,
        )
    raise AssertionError(f"unknown test record kind: {kind}")


@pytest.mark.parametrize("kind", TABLE_BY_KIND)
def test_new_records_store_site_id_without_replacing_location_text(db, kind):
    user = _create_user(db, 1)
    site = _create_site(db, 1, "OWN-ACTIVE")

    record = _create_record(db, kind, user, site["id"])

    location_field = "location" if kind == "eia" else "site_location"
    assert record["site_id"] == site["id"]
    assert record["org_id"] == user["org_id"]
    assert record[location_field] == "North loading bay"
    assert "latitude" not in record
    assert "longitude" not in record


@pytest.mark.parametrize("kind", TABLE_BY_KIND)
@pytest.mark.parametrize(
    ("site_scope", "site_status"),
    (("other_org", "active"), ("same_org", "inactive")),
    ids=("cross-organisation", "inactive"),
)
def test_invalid_site_assignments_are_rejected_without_creating_records(
    db, kind, site_scope, site_status
):
    user = _create_user(db, 1)
    site_org = (
        _create_org(db, "Other Organisation", "other-org")
        if site_scope == "other_org"
        else user["org_id"]
    )
    site = _create_site(db, site_org, f"{kind}-{site_scope}", site_status)

    with pytest.raises(ValueError, match=SITE_UNAVAILABLE_MESSAGE):
        _create_record(db, kind, user, site["id"])

    count = db.execute(f"SELECT COUNT(*) AS n FROM {TABLE_BY_KIND[kind]}").fetchone()["n"]
    assert count == 0


def test_actor_cannot_spoof_another_tenant_assignment(db):
    user = _create_user(db, 1)
    other_org = _create_org(db, "Spoof Target", "spoof-target")
    other_site = _create_site(db, other_org, "SPOOF-TARGET")

    with pytest.raises(ValueError, match=SITE_UNAVAILABLE_MESSAGE):
        _create_record(db, "inspection", user, other_site["id"], org_id=other_org)

    count = db.execute("SELECT COUNT(*) AS n FROM inspections").fetchone()["n"]
    assert count == 0


def test_site_lock_blocks_status_change_until_creation_transaction_finishes(db):
    from sheplatform.config import settings
    from sheplatform.modules.map.site_relationship_service import prepare_site_assignment

    user = _create_user(db, 1)
    site = _create_site(db, 1, "LOCKED-SITE")
    org_id, site_id = prepare_site_assignment(
        db, site_id=site["id"], org_id=1, user_id=user["id"]
    )

    competitor = sqlite3.connect(settings.DB_PATH, timeout=0)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            competitor.execute(
                "UPDATE sites SET status = 'inactive' WHERE id = ?", (site["id"],)
            )
    finally:
        competitor.close()
        db.rollback()

    assert org_id == 1
    assert site_id == site["id"]
