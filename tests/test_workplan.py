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
        from sheplatform.modules.workplan import data_service
        plan = data_service.create_workplan(db, fiscal_year="2027", created_by=manager["id"])
        data_service.add_task(db, workplan_id=plan["id"], title="Training", control_type="preventive")
        data_service.submit_for_review(db, plan["id"])
        data_service.approve_workplan(db, plan["id"], manager["id"])

        result = data_service.close_workplan(db, plan["id"])
        assert result["ok"] is False
        assert result.get("code") == "BRN-011"

    def test_close_allowed_after_drill(self, db):
        manager = _mk_user(db, "she_manager", "wp4@test.com")
        from sheplatform.modules.workplan import data_service
        plan = data_service.create_workplan(db, fiscal_year="2027", created_by=manager["id"])
        data_service.add_task(db, workplan_id=plan["id"], title="Training", control_type="preventive")
        data_service.submit_for_review(db, plan["id"])
        data_service.approve_workplan(db, plan["id"], manager["id"])

        # complete a drill this year
        from sheplatform.modules.emergency import data_service as emg
        drill = emg.schedule_drill(db, plan_id=None, drill_type="evacuation",
                                   scheduled_date="2026-09-01T10:00:00+00:00")
        emg.complete_drill(db, drill["id"], observations="ok")

        result = data_service.close_workplan(db, plan["id"])
        assert result["ok"] is True
        assert result["workplan"]["status"] == "closed"
