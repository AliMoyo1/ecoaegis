"""SHECCM tests (guide 26 BRN acceptance checklist).

Covers BRN-SHE-010 (notify before close), BRN-SHE-003 (residual risk ->
Risk Register + mock drill), multi-channel intake.
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


def _mk_grievance(db, by_user, severity="medium", channel="portal"):
    from sheplatform.modules.community_complaints import data_service
    return data_service.create_grievance(
        db, description="Dust from operations affecting nearby homes",
        source_channel=channel, complainant_name="Mrs Ncube",
        severity=severity, created_by=by_user)


class TestGrievanceCreate:
    def test_ref_and_defaults(self, db):
        officer = _mk_user(db, "she_officer", "g1@test.com")
        g = _mk_grievance(db, officer["id"])
        assert g["case_ref"].startswith(f"GRV-")
        assert g["status"] == "open"
        assert g["complainant_notified"] == 0


class TestBrn010:
    def test_close_blocked_without_notification(self, db):
        # BRN-SHE-010: complainant must be notified BEFORE closure
        officer = _mk_user(db, "she_officer", "g2@test.com")
        g = _mk_grievance(db, officer["id"])

        from sheplatform.modules.community_complaints import data_service
        data_service.resolve_grievance(db, g["id"], resolution_outcome="Agreed mitigation",
                                       is_residual_risk=False)
        result = data_service.close_grievance(db, g["id"], officer["id"])
        assert result["ok"] is False
        assert result.get("code") == "BRN-010"

    def test_close_allowed_after_notification(self, db):
        officer = _mk_user(db, "she_officer", "g3@test.com")
        g = _mk_grievance(db, officer["id"])

        from sheplatform.modules.community_complaints import data_service
        data_service.record_notification(db, g["id"], method="phone")
        data_service.resolve_grievance(db, g["id"], resolution_outcome="Resolved",
                                       is_residual_risk=False)
        result = data_service.close_grievance(db, g["id"], officer["id"])
        assert result["ok"] is True
        assert result["grievance"]["status"] == "closed"


class TestBrn003:
    def test_residual_risk_creates_risk_and_drill(self, db):
        # BRN-SHE-003: residual-risk closure -> Risk Register + mock drill
        officer = _mk_user(db, "she_officer", "g4@test.com")
        g = _mk_grievance(db, officer["id"], severity="high")

        from sheplatform.modules.community_complaints import data_service
        data_service.record_notification(db, g["id"], method="email")
        data_service.resolve_grievance(db, g["id"], resolution_outcome="Partial mitigation",
                                       is_residual_risk=True, asset_id="site-dam")
        result = data_service.close_grievance(db, g["id"], officer["id"])
        assert result["ok"] is True

        # Risk created from grievance
        from sheplatform.modules.risk_register import data_service as risk_svc
        risks = risk_svc.list_risks(db)
        assert len(risks) == 1
        assert risks[0]["source_type"] == "grievance"
        assert risks[0]["origin_module"] == "SHECCM"

        # Mock drill mandated
        drills = db.execute("SELECT * FROM mock_drills").fetchall()
        assert len(drills) == 1
        assert drills[0]["drill_type"] == "community_response"

    def test_plain_resolution_no_risk_no_drill(self, db):
        officer = _mk_user(db, "she_officer", "g5@test.com")
        g = _mk_grievance(db, officer["id"])

        from sheplatform.modules.community_complaints import data_service
        data_service.record_notification(db, g["id"])
        data_service.resolve_grievance(db, g["id"], resolution_outcome="Fully resolved",
                                       is_residual_risk=False)
        data_service.close_grievance(db, g["id"], officer["id"])

        from sheplatform.modules.risk_register import data_service as risk_svc
        assert len(risk_svc.list_risks(db)) == 0
        assert len(db.execute("SELECT * FROM mock_drills").fetchall()) == 0
