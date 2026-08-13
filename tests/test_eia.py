"""SHEIA tests (guide 26 BRN acceptance checklist).

Covers BRN-SHE-004 (EIA clearance gate), FNR-SHE-048 (EMA-accredited consultant),
EMA decision flow, eia.rejected -> risk handler.
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


def _mk_project(db, by_user):
    from sheplatform.modules.eia import data_service
    return data_service.create_project(
        db, project_name="New substation", department="Engineering",
        project_type="Infrastructure", location="Graniteside", created_by=by_user)


class TestScreeningGate:
    def test_eia_required_blocks_project(self, db):
        # BRN-SHE-004: EIA clearance gate
        officer = _mk_user(db, "she_officer", "e1@test.com")
        project = _mk_project(db, officer["id"])

        from sheplatform.modules.eia import data_service
        result = data_service.complete_screening(db, project["id"], eia_required=True)
        assert result["project"]["screening_result"] == "required"
        assert bool(result["project"]["blocked"]) is True
        assert result["project"]["status"] == "prospectus"

    def test_eia_not_required_unblocks(self, db):
        officer = _mk_user(db, "she_officer", "e2@test.com")
        project = _mk_project(db, officer["id"])

        from sheplatform.modules.eia import data_service
        result = data_service.complete_screening(db, project["id"], eia_required=False)
        assert result["project"]["screening_result"] == "not_required"
        assert bool(result["project"]["blocked"]) is False


class TestConsultantGate:
    def test_unverified_consultant_rejected(self, db):
        # FNR-SHE-048: consultant must be EMA-accredited
        officer = _mk_user(db, "she_officer", "e3@test.com")
        project = _mk_project(db, officer["id"])

        from sheplatform.modules.eia import data_service
        consultant = data_service.register_consultant(
            db, name="Green Consulting", company="GreenCo", ema_accreditation_number="")
        assert consultant["ema_accreditation_verified"] == 0

        result = data_service.assign_consultant(db, project["id"], consultant["id"])
        assert result["ok"] is False
        assert result.get("code") == "FNR-048"

    def test_accreditation_requires_manager_verification(self, db):
        """Audit P0-5: accreditation is NOT self-attested by the number alone."""
        officer = _mk_user(db, "she_officer", "e8@test.com")
        manager = _mk_user(db, "she_manager", "e9@test.com")
        project = _mk_project(db, officer["id"])

        from sheplatform.modules.eia import data_service
        # number supplied, but consultant still UNVERIFIED (no auto-verify)
        consultant = data_service.register_consultant(
            db, name="Green Consulting", company="GreenCo",
            ema_accreditation_number="EMA-2026-0042")
        assert consultant["ema_accreditation_verified"] == 0
        assert consultant["status"] == "pending"

        # assign blocked even with a number (the fix)
        result = data_service.assign_consultant(db, project["id"], consultant["id"])
        assert result["ok"] is False
        assert result.get("code") == "FNR-048"

        # officer cannot verify (role check)
        r = data_service.verify_consultant_accreditation(db, consultant["id"], officer["id"])
        assert r["ok"] is False
        assert "SHE Manager" in r["message"]

        # manager verifies -> now assignable
        r = data_service.verify_consultant_accreditation(db, consultant["id"], manager["id"])
        assert r["ok"] is True
        assert r["consultant"]["ema_accreditation_verified"] == 1
        assert r["consultant"]["status"] == "verified"
        result = data_service.assign_consultant(db, project["id"], consultant["id"])
        assert result["ok"] is True

    def test_accredited_consultant_assigns(self, db):
        officer = _mk_user(db, "she_officer", "e4@test.com")
        manager = _mk_user(db, "she_manager", "e10@test.com")
        project = _mk_project(db, officer["id"])

        from sheplatform.modules.eia import data_service
        consultant = data_service.register_consultant(
            db, name="EMA Certified Ltd", company="ECL",
            ema_accreditation_number="EMA-2024-011")
        data_service.verify_consultant_accreditation(db, consultant["id"], manager["id"])
        result = data_service.assign_consultant(db, project["id"], consultant["id"])
        assert result["ok"] is True
        assert result["project"]["status"] == "assessment"


class TestEmaDecision:
    def test_approved_unblocks_project(self, db):
        officer = _mk_user(db, "she_officer", "e5@test.com")
        project = _mk_project(db, officer["id"])

        from sheplatform.modules.eia import data_service
        data_service.complete_screening(db, project["id"], eia_required=True)
        data_service.submit_to_ema(db, project["id"], submission_ref="EMA-REF-1")
        result = data_service.record_ema_decision(db, project["id"], "approved")
        assert result["project"]["ema_decision"] == "approved"
        assert bool(result["project"]["blocked"]) is False

    def test_rejected_creates_risk_and_stays_blocked(self, db):
        officer = _mk_user(db, "she_officer", "e6@test.com")
        project = _mk_project(db, officer["id"])

        from sheplatform.modules.eia import data_service
        data_service.complete_screening(db, project["id"], eia_required=True)
        data_service.submit_to_ema(db, project["id"])
        result = data_service.record_ema_decision(db, project["id"], "rejected")
        assert bool(result["project"]["blocked"]) is True

        # eia.rejected handler created a risk
        from sheplatform.modules.risk_register import data_service as risk_svc
        risks = risk_svc.list_risks(db, org_id=1)
        assert len(risks) == 1
        assert risks[0]["source_type"] == "eia"
        assert risks[0]["origin_module"] == "SHEIA"
        assert risks[0]["risk_category"] == "regulatory"
