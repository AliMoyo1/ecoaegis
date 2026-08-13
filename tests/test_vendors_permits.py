"""SHECMV + PTW tests (guide 26 BRN acceptance checklist).

Covers BRN-SHE-001 (PTW requires approved RA), BRN-SHE-013 (cert expiry suspends
PTW eligibility), vendor onboarding event, permit approval chain.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _mk_user(db, role, email):
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "T", "U", role, 1),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
    return dict(row)


def _mk_vendor(db, by_user=None):
    from sheplatform.modules.vendor_compliance import data_service
    return data_service.create_vendor(db, company_name="Acme Contractors",
                                      risk_profile="medium", created_by=by_user)


def _mk_assessment(db, vendor_id, by_user=None, status="draft"):
    from sheplatform.modules.vendor_compliance import risk_assessment_service
    return risk_assessment_service.create_assessment(
        db, vendor_id=vendor_id, scope_of_work="Site excavation", risk_rating="medium",
        created_by=by_user)


class TestVendorCreate:
    def test_vendor_ref_and_defaults(self, db):
        v = _mk_vendor(db)
        assert v["vendor_ref"].startswith("VD-")
        assert bool(v["ptw_eligible"]) is True  # SQLite stores bool as 1/0
        assert v["certification_status"] == "valid"
        assert v["status"] == "active"


class TestPermitGate:
    def test_ptw_rejected_without_approved_ra(self, db):
        # BRN-SHE-001: no PTW without an approved risk assessment
        officer = _mk_user(db, "she_officer", "of1@test.com")
        vendor = _mk_vendor(db, officer["id"])
        assessment = _mk_assessment(db, vendor["id"], officer["id"], status="draft")

        from sheplatform.modules.permit_to_work import data_service
        result = data_service.create_permit(
            db, permit_type="excavation", title="Excavate trench", description="",
            vendor_id=vendor["id"], risk_assessment_id=assessment["id"],
            created_by=officer["id"])
        assert result["ok"] is False
        assert result.get("code") == "BRN-001"

    def test_ptw_allowed_with_approved_ra(self, db):
        officer = _mk_user(db, "she_officer", "of2@test.com")
        vendor = _mk_vendor(db, officer["id"])
        assessment = _mk_assessment(db, vendor["id"], officer["id"])

        from sheplatform.modules.vendor_compliance import risk_assessment_service
        approved = risk_assessment_service.approve_assessment(db, assessment["id"], officer["id"])
        assert approved["assessment"]["status"] == "approved"

        from sheplatform.modules.permit_to_work import data_service
        result = data_service.create_permit(
            db, permit_type="excavation", title="Excavate trench", description="",
            vendor_id=vendor["id"], risk_assessment_id=assessment["id"],
            created_by=officer["id"])
        assert result["ok"] is True
        assert result["permit"]["status"] == "pending_approval"
        assert result["permit"]["permit_ref"].startswith("PTW-")


class TestPermitApprovalChain:
    def test_full_chain_to_activation(self, db):
        officer = _mk_user(db, "she_officer", "of3@test.com")
        manager = _mk_user(db, "she_manager", "mgr3@test.com")
        hod = _mk_user(db, "she_hod", "hod3@test.com")
        line_mgr = _mk_user(db, "line_manager", "lm3@test.com")
        vendor = _mk_vendor(db, officer["id"])
        assessment = _mk_assessment(db, vendor["id"], officer["id"])

        from sheplatform.modules.vendor_compliance import risk_assessment_service
        risk_assessment_service.approve_assessment(db, assessment["id"], manager["id"])

        from sheplatform.modules.permit_to_work import data_service
        created = data_service.create_permit(
            db, permit_type="hot_work", title="Welding at site", description="",
            vendor_id=vendor["id"], risk_assessment_id=assessment["id"],
            created_by=officer["id"])
        permit = created["permit"]

        # chain: line_manager -> she_officer -> she_manager -> she_hod
        from sheplatform.core.workflow import advance_approval
        steps = db.execute(
            "SELECT * FROM approval_chain_steps WHERE chain_id = "
            "(SELECT id FROM approval_chains WHERE entity_type = 'permit' AND entity_id = %s "
            "AND status = 'active' ORDER BY id DESC LIMIT 1) ORDER BY step_order",
            (permit["id"],),
        ).fetchall()

        assert [s["role_required"] for s in steps] == ["line_manager", "she_officer", "she_manager", "she_hod"]
        assert steps[0]["status"] == "pending"

        approvers = [line_mgr, officer, manager, hod]
        for i, step in enumerate(steps):
            res = advance_approval(db, "permit", permit["id"], step["id"],
                                   approvers[i], "approved")
            assert res["ok"] is True, f"step {i} failed: {res}"
            if i < len(steps) - 1:
                assert res["complete"] is False
            else:
                assert res["complete"] is True


class TestCertificationExpiry:
    def test_expired_cert_suspends_ptw(self, db):
        # BRN-SHE-013: cert expiry -> PTW eligibility auto-suspended
        officer = _mk_user(db, "she_officer", "of4@test.com")
        vendor = _mk_vendor(db, officer["id"])

        from sheplatform.modules.vendor_compliance import data_service
        past = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        data_service.add_certification(db, vendor_id=vendor["id"],
                                       cert_name="ISO 45001", expiry_date=past)

        alerts = data_service.check_certification_expiry(db)
        assert len(alerts) >= 1

        refreshed = data_service.get_vendor(db, vendor["id"])
        assert refreshed["certification_status"] == "suspended"
        assert bool(refreshed["ptw_eligible"]) is False

    def test_cert_expiring_soon_alerts_but_keeps_eligibility(self, db):
        officer = _mk_user(db, "she_officer", "of5@test.com")
        vendor = _mk_vendor(db, officer["id"])

        from sheplatform.modules.vendor_compliance import data_service
        soon = (datetime.now(timezone.utc) + timedelta(days=15)).isoformat()
        data_service.add_certification(db, vendor_id=vendor["id"],
                                       cert_name="Fire Safety", expiry_date=soon)

        alerts = data_service.check_certification_expiry(db)
        assert len(alerts) >= 1

        refreshed = data_service.get_vendor(db, vendor["id"])
        assert refreshed["certification_status"] == "expiring"
        assert bool(refreshed["ptw_eligible"]) is True

    def test_valid_cert_keeps_eligibility(self, db):
        officer = _mk_user(db, "she_officer", "of6@test.com")
        vendor = _mk_vendor(db, officer["id"])

        from sheplatform.modules.vendor_compliance import data_service
        far = (datetime.now(timezone.utc) + timedelta(days=200)).isoformat()
        data_service.add_certification(db, vendor_id=vendor["id"],
                                       cert_name="ISO 9001", expiry_date=far)
        data_service.check_certification_expiry(db)

        refreshed = data_service.get_vendor(db, vendor["id"])
        assert refreshed["certification_status"] == "valid"
        assert bool(refreshed["ptw_eligible"]) is True
