"""SHEEPRP + SHEER tests (guide 26 BRN acceptance checklist).

Covers BRN-SHE-007 (dual sign-off), BRN-SHE-011 (drill mandate),
emergency.post_crisis -> EPRP improvement queue (BRS row 6).
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


class TestSiteSafeCertificate:
    def test_dual_signoff_required(self, db):
        # BRN-SHE-007: Site Safe for Occupation requires SHE Manager AND HOD Security
        officer = _mk_user(db, "she_officer", "em1@test.com")
        manager = _mk_user(db, "she_manager", "em2@test.com")
        hod = _mk_user(db, "she_hod", "em3@test.com")

        from sheplatform.modules.emergency import data_service
        event = data_service.create_emergency(
            db, title="Fire at warehouse", description="", severity="critical",
            created_by=officer["id"])

        # officer cannot sign
        result = data_service.issue_site_safe_certificate(
            db, event["id"], she_manager_id=officer["id"], hod_security_id=hod["id"])
        assert result["ok"] is False
        assert result.get("code") == "BRN-007"

        # manager + hod works
        result = data_service.issue_site_safe_certificate(
            db, event["id"], she_manager_id=manager["id"], hod_security_id=hod["id"])
        assert result["ok"] is True
        assert bool(result["emergency"]["site_safe_certificate"]) is True
        assert result["emergency"]["status"] == "contained"


class TestDrills:
    def test_drill_mandate_check(self, db):
        # BRN-011: workplan closure blocked if no completed drill this year
        from sheplatform.modules.emergency import data_service
        assert data_service.drills_completed_this_year(db) is False

        drill = data_service.schedule_drill(
            db, plan_id=None, drill_type="evacuation",
            scheduled_date="2026-09-01T10:00:00+00:00")
        data_service.complete_drill(db, drill["id"], observations="All clear in 4 min")
        assert data_service.drills_completed_this_year(db) is True


class TestPostCrisis:
    def test_post_crisis_blocked_without_site_certificate(self, db):
        """Audit P0-2: the re-entry gate must block post_crisis until BRN-007 cert."""
        officer = _mk_user(db, "she_officer", "em4@test.com")
        from sheplatform.modules.emergency import data_service
        event = data_service.create_emergency(
            db, title="Chemical leak", description="", severity="high",
            created_by=officer["id"])

        result = data_service.transition_post_crisis(
            db, event["id"], root_cause="Valve failure")
        assert result["ok"] is False
        assert "site safe certificate required" in result["message"]

    def test_post_crisis_creates_eprp_improvement(self, db):
        # BRS row 6: emergency.post_crisis -> EPRP improvement queue
        officer = _mk_user(db, "she_officer", "em5@test.com")
        manager = _mk_user(db, "she_manager", "em6@test.com")
        hod = _mk_user(db, "she_hod", "em7@test.com")
        from sheplatform.modules.emergency import data_service
        event = data_service.create_emergency(
            db, title="Chemical leak", description="", severity="high",
            created_by=officer["id"])

        # dual sign-off first (BRN-007)
        cert = data_service.issue_site_safe_certificate(
            db, event["id"], she_manager_id=manager["id"], hod_security_id=hod["id"])
        assert cert["ok"] is True

        result = data_service.transition_post_crisis(
            db, event["id"], root_cause="Valve failure")
        assert result["ok"] is True
        assert result["emergency"]["status"] == "post_crisis"

        improvements = db.execute("SELECT * FROM drill_improvements").fetchall()
        assert len(improvements) == 1
        assert "Valve failure" in improvements[0]["description"]
