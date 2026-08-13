"""SHEAWPM tests (guide 26 BRN acceptance checklist).

Covers BRN-SHE-006 (>= 40% preventive), BRN-SHE-011 (drill block on closure).
"""
from __future__ import annotations


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


class TestBrn006:
    def test_submit_rejected_below_40pct_preventive(self, db):
        # BRN-SHE-006: preventive share must be >= 40%
        manager = _mk_user(db, "she_manager", "wp1@test.com")
        from sheplatform.modules.workplan import data_service
        plan = data_service.create_workplan(db, fiscal_year="2027", created_by=manager["id"])

        # 1 preventive, 3 detective = 25%
        data_service.add_task(db, workplan_id=plan["id"], title="Audit", control_type="detective")
        data_service.add_task(db, workplan_id=plan["id"], title="Inspection", control_type="detective")
        data_service.add_task(db, workplan_id=plan["id"], title="Monitor", control_type="detective")
        data_service.add_task(db, workplan_id=plan["id"], title="Training", control_type="preventive")

        result = data_service.submit_for_review(db, plan["id"])
        assert result["ok"] is False
        assert result.get("code") == "BRN-006"

    def test_submit_allowed_at_40pct(self, db):
        manager = _mk_user(db, "she_manager", "wp2@test.com")
        from sheplatform.modules.workplan import data_service
        plan = data_service.create_workplan(db, fiscal_year="2027", created_by=manager["id"])

        # 2 preventive, 3 detective = 40%
        data_service.add_task(db, workplan_id=plan["id"], title="Drill", control_type="preventive")
        data_service.add_task(db, workplan_id=plan["id"], title="Training", control_type="preventive")
        data_service.add_task(db, workplan_id=plan["id"], title="Audit", control_type="detective")
        data_service.add_task(db, workplan_id=plan["id"], title="Inspection", control_type="detective")
        data_service.add_task(db, workplan_id=plan["id"], title="Monitor", control_type="detective")

        result = data_service.submit_for_review(db, plan["id"])
        assert result["ok"] is True
        assert result["workplan"]["status"] == "committee_review"
        assert float(result["workplan"]["preventive_pct"]) == 40.0


class TestBrn011:
    def test_close_blocked_without_drill(self, db):
        # BRN-SHE-011: cannot close workplan year without a completed mock drill
        manager = _mk_user(db, "she_manager", "wp3@test.com")
        cro = _mk_user(db, "cro", "wp3-cro@test.com")
        from sheplatform.modules.workplan import data_service
        plan = data_service.create_workplan(db, fiscal_year="2027", created_by=manager["id"])
        data_service.add_task(db, workplan_id=plan["id"], title="Training", control_type="preventive")
        data_service.submit_for_review(db, plan["id"])
        data_service.approve_workplan(db, plan["id"], cro["id"])

        result = data_service.close_workplan(db, plan["id"])
        assert result["ok"] is False
        assert result.get("code") == "BRN-011"

    def test_close_allowed_after_drill(self, db):
        manager = _mk_user(db, "she_manager", "wp4@test.com")
        coo = _mk_user(db, "coo", "wp4-coo@test.com")
        from sheplatform.modules.workplan import data_service
        plan = data_service.create_workplan(db, fiscal_year="2027", created_by=manager["id"])
        data_service.add_task(db, workplan_id=plan["id"], title="Training", control_type="preventive")
        data_service.submit_for_review(db, plan["id"])
        data_service.approve_workplan(db, plan["id"], coo["id"])

        # complete a drill this year
        from sheplatform.modules.emergency import data_service as emg
        drill = emg.schedule_drill(db, plan_id=None, drill_type="evacuation",
                                   scheduled_date="2026-09-01T10:00:00+00:00")
        emg.complete_drill(db, drill["id"], observations="ok")

        result = data_service.close_workplan(db, plan["id"])
        assert result["ok"] is True
        assert result["workplan"]["status"] == "closed"


class TestFnr029ExecutiveApproval:
    """Audit P0-7 fix: approve_workplan had no role check, so the drafting
    she_manager/she_officer (also holders of module.workplan.access) could
    approve their own plan.
    """

    def test_drafting_manager_cannot_self_approve(self, db):
        manager = _mk_user(db, "she_manager", "wp5@test.com")
        from sheplatform.modules.workplan import data_service
        plan = data_service.create_workplan(db, fiscal_year="2027", created_by=manager["id"])
        data_service.add_task(db, workplan_id=plan["id"], title="Training", control_type="preventive")
        data_service.submit_for_review(db, plan["id"])

        result = data_service.approve_workplan(db, plan["id"], manager["id"])
        assert result["ok"] is False
        assert result.get("code") == "FNR-029"
        assert data_service.get_workplan(db, plan["id"])["status"] == "committee_review"

    def test_she_officer_cannot_approve(self, db):
        manager = _mk_user(db, "she_manager", "wp6@test.com")
        officer = _mk_user(db, "she_officer", "wp6-officer@test.com")
        from sheplatform.modules.workplan import data_service
        plan = data_service.create_workplan(db, fiscal_year="2027", created_by=manager["id"])
        data_service.add_task(db, workplan_id=plan["id"], title="Training", control_type="preventive")
        data_service.submit_for_review(db, plan["id"])

        result = data_service.approve_workplan(db, plan["id"], officer["id"])
        assert result["ok"] is False
        assert result.get("code") == "FNR-029"

    def test_cro_can_approve(self, db):
        manager = _mk_user(db, "she_manager", "wp7@test.com")
        cro = _mk_user(db, "cro", "wp7-cro@test.com")
        from sheplatform.modules.workplan import data_service
        plan = data_service.create_workplan(db, fiscal_year="2027", created_by=manager["id"])
        data_service.add_task(db, workplan_id=plan["id"], title="Training", control_type="preventive")
        result = data_service.submit_for_review(db, plan["id"])
        assert result["ok"] is True
        assert result["workplan"]["status"] == "committee_review"

        result = data_service.approve_workplan(db, plan["id"], cro["id"])
        assert result["ok"] is True
        assert result["workplan"]["status"] == "active"

    def test_coo_can_approve(self, db):
        manager = _mk_user(db, "she_manager", "wp8@test.com")
        coo = _mk_user(db, "coo", "wp8-coo@test.com")
        from sheplatform.modules.workplan import data_service
        plan = data_service.create_workplan(db, fiscal_year="2027", created_by=manager["id"])
        data_service.add_task(db, workplan_id=plan["id"], title="Training", control_type="preventive")
        result = data_service.submit_for_review(db, plan["id"])
        assert result["ok"] is True
        assert result["workplan"]["status"] == "committee_review"

        result = data_service.approve_workplan(db, plan["id"], coo["id"])
        assert result["ok"] is True
        assert result["workplan"]["status"] == "active"

    def test_http_route_rejects_self_approval(self, db):
        """Route-level: module.workplan.access alone must not be enough."""
        from fastapi.testclient import TestClient
        from sheplatform.core.auth import hash_password
        from sheplatform.main import app

        manager = _mk_user(db, "she_manager", "wp9@test.com")
        db.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                  (hash_password("Test1234!"), manager["id"]))
        db.commit()
        from sheplatform.modules.workplan import data_service
        plan = data_service.create_workplan(db, fiscal_year="2027", created_by=manager["id"])
        data_service.add_task(db, workplan_id=plan["id"], title="Training", control_type="preventive")
        result = data_service.submit_for_review(db, plan["id"])
        assert result["ok"] is True
        assert result["workplan"]["status"] == "committee_review"

        client = TestClient(app)
        login = client.post("/login", data={"email": "wp9@test.com", "password": "Test1234!"})
        assert login.status_code in (200, 303)
        token = client.cookies.get("she_csrf", "")

        resp = client.post(f"/workplan/api/{plan['id']}/approve",
                           headers={"X-CSRF-Token": token})
        assert resp.status_code == 400
        assert data_service.get_workplan(db, plan["id"])["status"] == "committee_review"
