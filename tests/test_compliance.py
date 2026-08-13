"""Compliance obligations tests."""
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


class TestCompliance:
    def test_create_and_list(self, db):
        officer = _mk_user(db, "she_officer", "obl1@test.com")
        from sheplatform.modules.compliance.data_service import create_obligation, list_obligations
        ob = create_obligation(
            db, regulation="EMA Regulations 2007", obligation="Annual effluent discharge report",
            regulator="EMA", owner_id=officer["id"], frequency="annual",
            next_due_date="2099-03-31T00:00:00+00:00", created_by=officer["id"])
        assert ob["obligation_ref"].startswith("OBL-")
        assert ob["status"] == "active"

        items = list_obligations(db, regulator="EMA")
        assert any(o["obligation_ref"] == ob["obligation_ref"] for o in items)

    def test_mark_compliant(self, db):
        officer = _mk_user(db, "she_officer", "obl2@test.com")
        from sheplatform.modules.compliance.data_service import (
            create_obligation, mark_compliant)
        ob = create_obligation(
            db, regulation="NSSA Act", obligation="Workman's compensation return",
            regulator="NSSA", owner_id=officer["id"], frequency="annual",
            created_by=officer["id"])
        ob = mark_compliant(db, ob["id"], officer["id"], "receipt NSSA-2026-001")
        assert ob["status"] == "compliant"
        assert ob["evidence_path"] == "receipt NSSA-2026-001"

    def test_overdue_aging(self, db):
        officer = _mk_user(db, "she_officer", "obl3@test.com")
        from sheplatform.modules.compliance.data_service import (
            create_obligation, age_obligations, list_obligations)
        create_obligation(
            db, regulation="ZRP", obligation="Incident notification",
            regulator="ZRP", owner_id=officer["id"], frequency="event_based",
            next_due_date="2000-01-01T00:00:00+00:00", created_by=officer["id"])

        aged = age_obligations(db)
        assert aged >= 1
        overdue = list_obligations(db, status="overdue")
        assert any(o["regulator"] == "ZRP" for o in overdue)

    def test_invalid_frequency(self, db):
        officer = _mk_user(db, "she_officer", "obl4@test.com")
        from sheplatform.modules.compliance.data_service import create_obligation
        with pytest.raises(ValueError):
            create_obligation(
                db, regulation="X", obligation="Y", regulator="Z",
                owner_id=officer["id"], frequency="weekly", created_by=officer["id"])
