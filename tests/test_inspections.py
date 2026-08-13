"""Inspections module tests: schedule -> run checklist -> complete, fails -> CAPA."""
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


def _mk_inspection(db, officer, inspection_type="safety"):
    from sheplatform.modules.inspections.data_service import schedule_inspection
    return schedule_inspection(
        db, title="Monthly safety walk", inspection_type=inspection_type,
        site_location="Harare HQ", scheduled_date="2099-01-01T00:00:00+00:00",
        inspector_id=officer["id"], created_by=officer["id"])


class TestInspectionWorkflow:
    def test_schedule_and_checklist(self, db):
        officer = _mk_user(db, "she_officer", "insp1@test.com")
        insp = _mk_inspection(db, officer)
        assert insp["status"] == "scheduled"
        assert insp["inspection_ref"].startswith("INSP-")

        from sheplatform.modules.inspections.data_service import get_checklist
        items = get_checklist("safety")
        assert len(items) == 5
        assert any("PPE" in i for i in items)

    def test_start_and_complete_with_fails_creates_capa(self, db):
        officer = _mk_user(db, "she_officer", "insp2@test.com")
        insp = _mk_inspection(db, officer)

        from sheplatform.modules.inspections.data_service import start_inspection, complete_inspection
        insp = start_inspection(db, insp["id"], officer["id"])
        assert insp["status"] == "in_progress"

        items = ["PPE worn by all personnel", "Emergency exits clear and lit"]
        out = complete_inspection(db, insp["id"], officer["id"],
                                  findings="2 fails found",
                                  results=[
                                      {"item": items[0], "result": "fail", "comment": "one worker no gloves"},
                                      {"item": items[1], "result": "pass", "comment": ""},
                                  ])
        assert out["inspection"]["status"] == "completed"
        assert len(out["capa_created"]) == 1  # one fail -> one CAPA

        # the CAPA exists with source inspection
        from sheplatform.modules.capa.data_service import list_actions
        actions = list_actions(db)
        assert any(a["source_type"] == "inspection" and a["source_id"] == insp["id"]
                   for a in actions)
        assert "PPE worn by all personnel" in actions[0]["title"]

    def test_cannot_complete_twice(self, db):
        officer = _mk_user(db, "she_officer", "insp3@test.com")
        insp = _mk_inspection(db, officer)

        from sheplatform.modules.inspections.data_service import complete_inspection
        complete_inspection(db, insp["id"], officer["id"], "first", [])
        with pytest.raises(ValueError, match="only scheduled/in-progress"):
            complete_inspection(db, insp["id"], officer["id"], "second", [])

    def test_results_persisted(self, db):
        officer = _mk_user(db, "she_officer", "insp4@test.com")
        insp = _mk_inspection(db, officer)

        from sheplatform.modules.inspections.data_service import complete_inspection
        out = complete_inspection(db, insp["id"], officer["id"], "ok",
                                  results=[{"item": "PPE worn", "result": "pass", "comment": "all good"}])
        rows = db.execute("SELECT * FROM inspection_results WHERE inspection_id = %s",
                          (insp["id"],)).fetchall()
        assert len(rows) == 1
        assert rows[0]["result"] == "pass"


class TestInspectionAgeing:
    def test_overdue(self, db):
        officer = _mk_user(db, "she_officer", "insp5@test.com")
        from sheplatform.modules.inspections.data_service import schedule_inspection
        schedule_inspection(
            db, title="Old inspection", inspection_type="fire",
            site_location="X", scheduled_date="2000-01-01T00:00:00+00:00",
            inspector_id=officer["id"], created_by=officer["id"])

        from sheplatform.modules.inspections.data_service import age_inspections
        aged = age_inspections(db)
        assert aged >= 1

        from sheplatform.modules.inspections.data_service import list_inspections
        assert any(i["status"] == "overdue" for i in list_inspections(db, status="overdue"))
