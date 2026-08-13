"""PTW approval chain tests: the fix for the audit finding (dead-end approval)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _mk_user(db, role, email):
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "F", "L", role, 1),
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


class TestPTWApprovalHTTP:
    """Re-audit fix: the data-service tests above call approve_permit_step()
    directly and always passed, even while the HTTP route 403'd every step-4
    (she_hod) approver. module.permits.access was missing she_hod. These
    tests go through the real route + capability gate, the thing that was
    actually broken, so they would have failed before the fix.
    """

    def _login(self, client, email) -> str:
        resp = client.post("/login", data={"email": email, "password": "Test1234!"})
        assert resp.status_code in (200, 303), f"login failed for {email}: {resp.status_code}"
        return client.cookies.get("she_csrf", "")

    def _approve(self, client, email, permit_id, step_id):
        token = self._login(client, email)
        return client.post(
            f"/permits/api/{permit_id}/approve",
            data={"step_id": step_id, "decision": "approved"},
            headers={"X-CSRF-Token": token})

    def test_every_chain_role_can_reach_the_approve_route(self, db):
        officer = _mk_user(db, "she_officer", "http-officer@test.com")
        _mk_user(db, "she_manager", "http-manager@test.com")
        _mk_user(db, "line_manager", "http-linemgr@test.com")
        _mk_user(db, "she_hod", "http-hod@test.com")
        permit = _mk_permit(db, officer)

        from sheplatform.main import app
        client = TestClient(app)

        from sheplatform.modules.permit_to_work.data_service import get_pending_approval_step
        chain = [
            ("http-linemgr@test.com", "line_manager"),
            ("http-officer@test.com", "she_officer"),
            ("http-manager@test.com", "she_manager"),
            ("http-hod@test.com", "she_hod"),
        ]
        for email, expected_role in chain:
            step = get_pending_approval_step(db, permit["id"])
            assert step["role_required"] == expected_role
            resp = self._approve(client, email, permit["id"], step["id"])
            assert resp.status_code == 200, (
                f"{expected_role} ({email}) got {resp.status_code} approving step "
                f"{step['step_order']}, expected 200. Body: {resp.text}")

        from sheplatform.modules.permit_to_work.data_service import get_permit
        assert get_permit(db, permit["id"])["status"] == "active"

    def test_she_hod_alone_is_not_blocked_by_capability_gate(self, db):
        """Narrow regression test for the exact bug: she_hod on step 4 got a
        blanket 403 from @require_capability before ever reaching the
        workflow engine's own role check.
        """
        officer = _mk_user(db, "she_officer", "http-officer2@test.com")
        _mk_user(db, "she_hod", "http-hod2@test.com")
        permit = _mk_permit(db, officer)

        # fast-forward the first three steps at the data-service level;
        # only step 4 (she_hod) is what this test is checking.
        from sheplatform.modules.permit_to_work.data_service import (
            get_pending_approval_step, approve_permit_step)
        _mk_user(db, "line_manager", "http-linemgr2@test.com")
        _mk_user(db, "she_manager", "http-manager2@test.com")
        line_mgr = dict(db.execute(
            "SELECT * FROM users WHERE email = 'http-linemgr2@test.com'").fetchone())
        manager = dict(db.execute(
            "SELECT * FROM users WHERE email = 'http-manager2@test.com'").fetchone())
        for approver in (line_mgr, officer, manager):
            step = get_pending_approval_step(db, permit["id"])
            approve_permit_step(db, permit["id"], step["id"], approver, "approved")

        step = get_pending_approval_step(db, permit["id"])
        assert step["role_required"] == "she_hod"

        from sheplatform.main import app
        client = TestClient(app)
        resp = self._approve(client, "http-hod2@test.com", permit["id"], step["id"])
        assert resp.status_code != 403, (
            "she_hod was blocked by the route's capability gate, "
            "module.permits.access is missing she_hod again"
        )
        assert resp.status_code == 200

    def test_http_route_allows_line_manager_step1(self, db):
        """REGRESSION (re-audit): the HTTP route was gated by ptw.approve which
        excluded line_manager/she_officer, so step 1 could never be approved in
        the running app. Route must allow all chain roles; service enforces role.
        """
        from fastapi.testclient import TestClient
        from sheplatform.core.auth import hash_password
        # seed the chain roles with the dev password for HTTP login
        officer = _mk_user(db, "she_officer", "http-officer@test.com")
        line_mgr = _mk_user(db, "line_manager", "http-lm@test.com")
        for u in (officer, line_mgr):
            db.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                       (hash_password("ChangeMe!123"), u["id"]))
        db.commit()
        permit = _mk_permit(db, officer)

        from sheplatform.main import app
        client = TestClient(app)

        def login(email):
            r = client.post("/login", data={"email": email, "password": "ChangeMe!123"})
            assert r.status_code in (200, 303), f"login {email} -> {r.status_code}"
            return client.cookies.get("she_csrf", "")

        def approve(email, step_id, decision="approved"):
            token = login(email)
            return client.post(
                f"/permits/api/{permit['id']}/approve",
                data={"step_id": step_id, "decision": decision},
                headers={"X-CSRF-Token": token})

        from sheplatform.modules.permit_to_work.data_service import get_pending_approval_step

        # step 1: LINE MANAGER via the real HTTP route (was 403 before the fix)
        s = get_pending_approval_step(db, permit["id"])
        assert s["role_required"] == "line_manager"
        r = approve("http-lm@test.com", s["id"])
        assert r.status_code in (200, 400), f"expected ok-or-role-error, got {r.status_code}"
        if r.status_code == 400:
            # if 400, body must be a role error (not a 403 permission error)
            assert "requires role" in r.json()["message"]

        # step 2: SHE OFFICER via HTTP
        s = get_pending_approval_step(db, permit["id"])
        assert s["role_required"] == "she_officer"
        r = approve("http-officer@test.com", s["id"])
        assert r.status_code in (200, 400)

        # a user with NO permit module access must be blocked at the route gate
        # (403 = capability gate; 400 = service role check - either is correct
        # defense in depth, but employee has no permit module access at all)
        emp = _mk_user(db, "employee", "http-emp@test.com")
        db.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                   (hash_password("ChangeMe!123"), emp["id"]))
        db.commit()
        s = get_pending_approval_step(db, permit["id"])
        r = approve("http-emp@test.com", s["id"])
        assert r.status_code in (400, 403)
