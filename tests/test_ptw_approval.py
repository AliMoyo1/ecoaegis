"""PTW approval chain tests: the fix for the audit finding (dead-end approval)."""
from __future__ import annotations

import pytest


def _mk_user(db, role, email):
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key) "
        "VALUES (%s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "F", "L", role),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _mk_approved_ra(db, officer):
    db.execute(
        "INSERT INTO vendors (vendor_ref, company_name, status) VALUES (%s, %s, 'active')",
        ("VD-TEST", "Test Vendor"))
    db.commit()
    vendor = dict(db.execute("SELECT * FROM vendors ORDER BY id DESC LIMIT 1").fetchone())
    db.execute(
        "INSERT INTO risk_assessments (assessment_ref, scope_of_work, risk_rating, status, "
        "vendor_id, created_by) "
        "VALUES (%s, %s, 'medium', 'approved', %s, %s)",
        ("RA-TEST", "Approved RA for welding", vendor["id"], officer["id"]))
    db.commit()
    return dict(db.execute("SELECT * FROM risk_assessments ORDER BY id DESC LIMIT 1").fetchone())


def _mk_permit(db, officer):
    ra = _mk_approved_ra(db, officer)
    vendor = dict(db.execute("SELECT * FROM vendors ORDER BY id DESC LIMIT 1").fetchone())
    from sheplatform.modules.permit_to_work.data_service import create_permit
    result = create_permit(
        db, permit_type="hot_work", title="Welding works", description="",
        vendor_id=vendor["id"], risk_assessment_id=ra["id"],
        site_location="Workshop", created_by=officer["id"])
    assert result["ok"] is True
    return result["permit"]


class TestPTWApproval:
    def test_full_chain_activates_permit(self, db):
        """The audit finding: permits were never activatable. Now 4-step -> active."""
        officer = _mk_user(db, "she_officer", "ptw1@test.com")
        manager = _mk_user(db, "she_manager", "ptw2@test.com")
        line_mgr = _mk_user(db, "line_manager", "ptw3@test.com")
        hod = _mk_user(db, "she_hod", "ptw4@test.com")

        permit = _mk_permit(db, officer)
        assert permit["status"] == "pending_approval"

        from sheplatform.modules.permit_to_work.data_service import (
            get_pending_approval_step, approve_permit_step)

        # step 1: line manager
        s = get_pending_approval_step(db, permit["id"])
        assert s["role_required"] == "line_manager"
        r = approve_permit_step(db, permit["id"], s["id"], line_mgr, "approved")
        assert r["ok"] and not r.get("complete")

        # step 2: SHE officer
        s = get_pending_approval_step(db, permit["id"])
        assert s["role_required"] == "she_officer"
        r = approve_permit_step(db, permit["id"], s["id"], officer, "approved")
        assert r["ok"] and not r.get("complete")

        # step 3: SHE manager
        s = get_pending_approval_step(db, permit["id"])
        assert s["role_required"] == "she_manager"
        r = approve_permit_step(db, permit["id"], s["id"], manager, "approved")
        assert r["ok"] and not r.get("complete")

        # step 4: SHE HOD -> chain complete -> permit ACTIVE
        s = get_pending_approval_step(db, permit["id"])
        assert s["role_required"] == "she_hod"
        r = approve_permit_step(db, permit["id"], s["id"], hod, "approved")
        assert r["ok"] and r.get("complete")

        from sheplatform.modules.permit_to_work.data_service import get_permit
        permit = get_permit(db, permit["id"])
        assert permit["status"] == "active"  # THE FIX: was stuck at pending_approval

    def test_wrong_role_cannot_approve(self, db):
        officer = _mk_user(db, "she_officer", "ptw5@test.com")
        employee = _mk_user(db, "employee", "ptw6@test.com")

        permit = _mk_permit(db, officer)
        from sheplatform.modules.permit_to_work.data_service import (
            get_pending_approval_step, approve_permit_step)
        s = get_pending_approval_step(db, permit["id"])
        r = approve_permit_step(db, permit["id"], s["id"], employee, "approved")
        assert r["ok"] is False
        assert "requires role" in r["message"]

    def test_rejection_rejects_permit(self, db):
        officer = _mk_user(db, "she_officer", "ptw7@test.com")
        line_mgr = _mk_user(db, "line_manager", "ptw8@test.com")

        permit = _mk_permit(db, officer)
        from sheplatform.modules.permit_to_work.data_service import (
            get_pending_approval_step, approve_permit_step, get_permit)
        s = get_pending_approval_step(db, permit["id"])
        r = approve_permit_step(db, permit["id"], s["id"], line_mgr, "rejected", "unsafe method")
        assert r["ok"] and r.get("complete")
        assert get_permit(db, permit["id"])["status"] == "revoked"

    def test_permit_approved_event_emitted(self, db):
        """Events must fire so downstream (vendor, risk) handlers see the activation."""
        officer = _mk_user(db, "she_officer", "ptw9@test.com")
        manager = _mk_user(db, "she_manager", "ptw10@test.com")
        line_mgr = _mk_user(db, "line_manager", "ptw11@test.com")
        hod = _mk_user(db, "she_hod", "ptw12@test.com")

        permit = _mk_permit(db, officer)
        from sheplatform.modules.permit_to_work.data_service import (
            get_pending_approval_step, approve_permit_step)
        for approver in (line_mgr, officer, manager, hod):
            s = get_pending_approval_step(db, permit["id"])
            approve_permit_step(db, permit["id"], s["id"], approver, "approved")

        rows = db.execute("SELECT event_type FROM events WHERE event_type = 'permit.approved'").fetchall()
        assert len(rows) == 1
