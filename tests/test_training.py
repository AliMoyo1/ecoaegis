"""SHET&A tests (guide 26).

Covers FNR-SHE-044 (attendance -> competency matrix), FNR-SHE-045 (refresher),
BRN-014 (outsourced -> procurement ref).
"""
from __future__ import annotations


def _mk_user(db, role, email):
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key) "
        "VALUES (%s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "T", "U", role),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
    return dict(row)


class TestTrainingNeeds:
    def test_create_need(self, db):
        officer = _mk_user(db, "she_officer", "tr1@test.com")
        from sheplatform.modules.training import data_service
        need = data_service.create_need(
            db, title="Confined space entry", source_trigger="incident", source_id=5,
            created_by=officer["id"])
        assert need["need_ref"].startswith("TN-")
        assert need["status"] == "identified"


class TestSessions:
    def test_outsourced_generates_procurement_ref(self, db):
        # BRN-014: outsourced training -> procurement request
        officer = _mk_user(db, "she_officer", "tr2@test.com")
        from sheplatform.modules.training import data_service
        session = data_service.schedule_session(
            db, need_id=None, title="Advanced first aid", scheduled_date="2026-10-01T09:00:00+00:00",
            delivery_method="outsourced", created_by=officer["id"])
        assert session["procurement_ref"] is not None
        assert session["procurement_ref"].startswith("PRC-TRN-")

    def test_attendance_creates_competency(self, db):
        # FNR-SHE-044/045: attendance -> competency matrix with refresher due
        officer = _mk_user(db, "she_officer", "tr3@test.com")
        employee = _mk_user(db, "employee", "tr4@test.com")
        from sheplatform.modules.training import data_service
        session = data_service.schedule_session(
            db, need_id=None, title="Working at height", scheduled_date="2026-09-15T09:00:00+00:00",
            created_by=officer["id"])
        data_service.record_attendance(
            db, session_id=session["id"], user_id=employee["id"],
            attended=True, competency_score=88)

        competency = data_service.get_competency(db, employee["id"])
        assert len(competency) == 1
        assert competency[0]["competency_name"] == "Working at height"
        assert competency[0]["level"] == "expert"  # score 88 >= 85
        assert competency[0]["certified"] == 1
        assert competency[0]["expiry_date"] is not None  # refresher scheduled
