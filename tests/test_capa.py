"""CAPA module tests: 2-person verification, workflow, ageing, refs."""
from __future__ import annotations

import pytest


def _mk_user(db, role, email):
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "F", "L", role, 1),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _mk_action(db, officer, manager, priority="high"):
    from sheplatform.modules.capa.data_service import create_action
    return create_action(
        db, title="Fix spill kit", description="Restock spill kit",
        source_type="incident", source_id=1, priority=priority,
        assigned_to=officer["id"], due_date="2099-01-01T00:00:00+00:00",
        created_by=manager["id"], org_id=1)


class TestCAPAWorkflow:
    def test_full_lifecycle_with_2person_verification(self, db):
        officer = _mk_user(db, "she_officer", "capa1@test.com")
        manager = _mk_user(db, "she_manager", "capa2@test.com")

        action = _mk_action(db, officer, manager)
        assert action["status"] == "open"
        assert action["action_ref"].startswith("CA-")

        # assignee starts
        from sheplatform.modules.capa.data_service import start_action
        action = start_action(db, action["id"], officer["id"])
        assert action["status"] == "in_progress"

        # assignee completes
        from sheplatform.modules.capa.data_service import complete_action
        action = complete_action(db, action["id"], officer["id"], "Kit restocked")
        assert action["status"] == "completed"
        assert "Kit restocked" in action["description"]

        # 2-person rule: assignee CANNOT verify own work
        from sheplatform.modules.capa.data_service import verify_action
        with pytest.raises(ValueError, match="2-person"):
            verify_action(db, action["id"], officer["id"])

        # manager (different user) verifies
        action = verify_action(db, action["id"], manager["id"], "Verified on site")
        assert action["status"] == "verified"
        assert action["verified_by"] == manager["id"]
        assert "Verified on site" in action["description"]

    def test_verify_requires_completed(self, db):
        officer = _mk_user(db, "she_officer", "capa3@test.com")
        manager = _mk_user(db, "she_manager", "capa4@test.com")
        action = _mk_action(db, officer, manager)

        from sheplatform.modules.capa.data_service import verify_action
        with pytest.raises(ValueError, match="only completed"):
            verify_action(db, action["id"], manager["id"])

    def test_not_assignee_cannot_complete(self, db):
        officer = _mk_user(db, "she_officer", "capa5@test.com")
        manager = _mk_user(db, "she_manager", "capa6@test.com")
        other = _mk_user(db, "employee", "capa7@test.com")
        action = _mk_action(db, officer, manager)

        from sheplatform.modules.capa.data_service import complete_action
        with pytest.raises(ValueError, match="not assigned"):
            complete_action(db, action["id"], other["id"])


class TestCAPAAgeing:
    def test_overdue_detection(self, db):
        officer = _mk_user(db, "she_officer", "capa8@test.com")
        manager = _mk_user(db, "she_manager", "capa9@test.com")
        from sheplatform.modules.capa.data_service import create_action
        create_action(
            db, title="Old action", description="", source_type="audit", source_id=1,
            priority="low", assigned_to=officer["id"],
            due_date="2000-01-01T00:00:00+00:00", created_by=manager["id"], org_id=1)

        from sheplatform.modules.capa.data_service import age_actions
        aged = age_actions(db)
        assert aged >= 1

        from sheplatform.modules.capa.data_service import list_actions
        items = list_actions(db, status="overdue", org_id=1)
        assert any(a["title"] == "Old action" for a in items)


class TestCAPARefs:
    def test_ref_sequence(self, db):
        officer = _mk_user(db, "she_officer", "capa10@test.com")
        manager = _mk_user(db, "she_manager", "capa11@test.com")
        a1 = _mk_action(db, officer, manager)
        a2 = _mk_action(db, officer, manager)
        assert a1["action_ref"] != a2["action_ref"]
        assert int(a2["action_ref"].rsplit("-", 1)[1]) == int(a1["action_ref"].rsplit("-", 1)[1]) + 1
