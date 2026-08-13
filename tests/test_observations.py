"""Observations module tests: quick capture, triage, CAPA escalation."""
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


class TestObservations:
    def test_any_employee_can_report(self, db):
        emp = _mk_user(db, "employee", "obs1@test.com")
        from sheplatform.modules.observations.data_service import create_observation
        obs = create_observation(
            db, obs_type="hazard", title="Wet floor", description="Spill near exit",
            location="Warehouse", severity="medium", reported_by=emp["id"])
        assert obs["status"] == "open"
        assert obs["obs_ref"].startswith("OBS-")
        assert obs["reported_by"] == emp["id"]

    def test_triage_escalates_to_capa(self, db):
        officer = _mk_user(db, "she_officer", "obs2@test.com")
        emp = _mk_user(db, "employee", "obs3@test.com")
        from sheplatform.modules.observations.data_service import (
            create_observation, raise_corrective_action)
        obs = create_observation(
            db, obs_type="near_miss", title="Forklift near miss", description="",
            location="Yard", severity="high", reported_by=emp["id"])

        out = raise_corrective_action(db, obs["id"], officer["id"])
        assert out["capa_ref"].startswith("CA-")
        assert out["observation"]["status"] == "corrective_action"

        # CAPA linked back exists
        from sheplatform.modules.capa.data_service import list_actions
        actions = list_actions(db)
        assert any("Forklift near miss" in a["title"] for a in actions)

    def test_cannot_escalate_twice(self, db):
        officer = _mk_user(db, "she_officer", "obs4@test.com")
        emp = _mk_user(db, "employee", "obs5@test.com")
        from sheplatform.modules.observations.data_service import (
            create_observation, raise_corrective_action)
        obs = create_observation(
            db, obs_type="hazard", title="X", description="", location="",
            severity="low", reported_by=emp["id"])
        raise_corrective_action(db, obs["id"], officer["id"])
        with pytest.raises(ValueError, match="already raised"):
            raise_corrective_action(db, obs["id"], officer["id"])

    def test_close_with_resolution(self, db):
        officer = _mk_user(db, "she_officer", "obs6@test.com")
        emp = _mk_user(db, "employee", "obs7@test.com")
        from sheplatform.modules.observations.data_service import (
            create_observation, close_observation)
        obs = create_observation(
            db, obs_type="good_practice", title="Clean workstation", description="",
            location="Office", severity="low", reported_by=emp["id"])
        closed = close_observation(db, obs["id"], officer["id"], "Logged for recognition")
        assert closed["status"] == "closed"
        assert "recognition" in closed["description"].lower()

    def test_invalid_severity_rejected(self, db):
        emp = _mk_user(db, "employee", "obs8@test.com")
        from sheplatform.modules.observations.data_service import create_observation
        with pytest.raises(ValueError):
            create_observation(db, obs_type="hazard", title="X", description="",
                               location="", severity="extreme", reported_by=emp["id"])
