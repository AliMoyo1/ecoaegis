"""Workflow engine tests (guide 5.6, BRN-SHE-005)."""
from __future__ import annotations

from sheplatform.core import workflow


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


class TestApprovalChains:
    def test_create_chain_and_advance(self, db):
        cro = _mk_user(db, "cro", "cro@test.com")
        ceo = _mk_user(db, "coo", "coo@test.com")

        workflow.create_approval_chain(db, "incident_investigation", 1, [
            {"step_order": 1, "role_required": "cro", "sla_hours": 24},
            {"step_order": 2, "role_required": "coo", "sla_hours": 48},
        ])

        # first step is pending
        step1 = db.execute(
            "SELECT * FROM approval_chain_steps WHERE step_order = 1"
        ).fetchone()
        assert step1["status"] == "pending"

        # wrong role cannot advance
        res = workflow.advance_approval(db, "incident_investigation", 1, step1["id"],
                                        cro, "approved")
        assert res["ok"] is True
        assert res["complete"] is False
        assert res["next_step"] is not None

        # second step now pending, complete on approval
        step2 = db.execute(
            "SELECT * FROM approval_chain_steps WHERE step_order = 2"
        ).fetchone()
        assert step2["status"] == "pending"
        res = workflow.advance_approval(db, "incident_investigation", 1, step2["id"],
                                        ceo, "approved")
        assert res["ok"] is True
        assert res["complete"] is True
        chain = db.execute("SELECT * FROM approval_chains").fetchone()
        assert chain["status"] == "completed"

    def test_wrong_role_rejected(self, db):
        cro = _mk_user(db, "cro", "cro2@test.com")
        champion = _mk_user(db, "she_champion", "champ@test.com")
        workflow.create_approval_chain(db, "root_cause", 7, [
            {"step_order": 1, "role_required": "cro", "sla_hours": 24},
        ])
        step = db.execute("SELECT * FROM approval_chain_steps").fetchone()

        res = workflow.advance_approval(db, "root_cause", 7, step["id"],
                                        champion, "approved")
        assert res["ok"] is False
        assert "requires role" in res["message"]

        # cro can do it
        res = workflow.advance_approval(db, "root_cause", 7, step["id"], cro, "approved")
        assert res["ok"] is True

    def test_double_approval_race(self, db):
        cro = _mk_user(db, "cro", "cro3@test.com")
        workflow.create_approval_chain(db, "ptw", 9, [
            {"step_order": 1, "role_required": "cro", "sla_hours": 24},
        ])
        step = db.execute("SELECT * FROM approval_chain_steps").fetchone()

        first = workflow.advance_approval(db, "ptw", 9, step["id"], cro, "approved")
        second = workflow.advance_approval(db, "ptw", 9, step["id"], cro, "approved")
        assert first["ok"] is True
        assert second["ok"] is False  # already handled

    def test_rejection_ends_chain(self, db):
        cro = _mk_user(db, "cro", "cro4@test.com")
        workflow.create_approval_chain(db, "comms", 3, [
            {"step_order": 1, "role_required": "cro", "sla_hours": 24},
            {"step_order": 2, "role_required": "coo", "sla_hours": 48},
        ])
        step = db.execute("SELECT * FROM approval_chain_steps WHERE step_order = 1").fetchone()
        res = workflow.advance_approval(db, "comms", 3, step["id"], cro, "rejected", "missing detail")
        assert res["ok"] is True
        assert res["complete"] is True
        chain = db.execute("SELECT * FROM approval_chains").fetchone()
        assert chain["status"] == "rejected"
