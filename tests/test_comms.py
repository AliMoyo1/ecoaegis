"""SHE EC&SC tests (guide 26 BRN acceptance checklist).

Covers BRN-SHE-008 (no dispatch without HOD sign-off),
FNR-SHE-016 (effectiveness assessment before closure).
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


class TestBrn008:
    def test_dispatch_blocked_without_hod(self, db):
        # BRN-SHE-008: no external release without HOD sign-off
        officer = _mk_user(db, "she_officer", "cm1@test.com")
        from sheplatform.modules.external_comms import data_service
        comms = data_service.create_comms(
            db, concern_description="Dust emissions", medium="letter",
            created_by=officer["id"])

        result = data_service.dispatch(db, comms["id"], officer["id"])
        assert result["ok"] is False
        assert result.get("code") == "BRN-008"

    def test_full_flow_with_hod(self, db):
        officer = _mk_user(db, "she_officer", "cm2@test.com")
        hod = _mk_user(db, "she_hod", "cm3@test.com")

        from sheplatform.modules.external_comms import data_service
        comms = data_service.create_comms(
            db, concern_description="Noise complaint response", medium="email",
            created_by=officer["id"])

        # non-HOD cannot approve
        result = data_service.hod_approve(db, comms["id"], officer["id"])
        assert result["ok"] is False
        assert result.get("code") == "BRN-008"

        # HOD approves
        result = data_service.hod_approve(db, comms["id"], hod["id"], "Approved")
        assert result["ok"] is True
        assert result["comms"]["status"] == "approved"

        # dispatch now works
        result = data_service.dispatch(db, comms["id"], officer["id"])
        assert result["ok"] is True
        assert result["comms"]["status"] == "dispatched"


class TestFnr016:
    def test_close_requires_effectiveness_assessment(self, db):
        officer = _mk_user(db, "she_officer", "cm4@test.com")
        hod = _mk_user(db, "she_hod", "cm5@test.com")
        from sheplatform.modules.external_comms import data_service
        comms = data_service.create_comms(
            db, concern_description="Community workshop", medium="workshop",
            created_by=officer["id"])
        data_service.hod_approve(db, comms["id"], hod["id"])
        data_service.dispatch(db, comms["id"], officer["id"])

        # empty assessment rejected
        result = data_service.close_with_effectiveness(db, comms["id"], "  ")
        assert result["ok"] is False
        assert result.get("code") == "FNR-016"

        # with assessment works
        result = data_service.close_with_effectiveness(
            db, comms["id"], "Community feedback positive, concern resolved")
        assert result["ok"] is True
        assert result["comms"]["status"] == "closed"
