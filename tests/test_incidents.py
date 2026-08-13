"""SHEIMI + Risk Register tests (guide 26 BRN acceptance checklist).

Covers BRN-SHE-002 (48h deadline), BRN-SHE-005 (approval chain),
cross-module integration #3 (incident.closed -> risk register).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


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


def _mk_incident(db, user_id, severity="high", title="Test incident"):
    from sheplatform.modules.incidents import data_service
    now = datetime.now(timezone.utc).isoformat()
    return data_service.create_incident(
        db, title=title, description="Worker fell from height", severity=severity,
        incident_type="accident", occurred_at=now, reported_by=user_id)


class TestIncidentCreate:
    def test_ref_format(self, db):
        emp = _mk_user(db, "employee", "emp1@test.com")
        inc = _mk_incident(db, emp["id"])
        assert inc["incident_ref"].startswith(f"INC-{datetime.now(timezone.utc).year}-")
        import re
        assert re.match(r"^INC-\d{4}-\d{3}$", inc["incident_ref"])

    def test_ref_sequence(self, db):
        emp = _mk_user(db, "employee", "emp2@test.com")
        i1 = _mk_incident(db, emp["id"])
        i2 = _mk_incident(db, emp["id"])
        seq1 = int(i1["incident_ref"].split("-")[-1])
        seq2 = int(i2["incident_ref"].split("-")[-1])
        assert seq2 == seq1 + 1

    def test_ref_rollover_past_999(self, db):
        # Regression: refs sort lexicographically, so ordering by ref breaks
        # once the sequence hits 4 digits (INC-2026-1000 < INC-2026-999).
        emp = _mk_user(db, "employee", "emp2b@test.com")
        db.execute(
            "INSERT INTO incidents (incident_ref, title, description, severity, incident_type, "
            "occurred_at, reported_by) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("INC-2026-0999", "Seed", "d", "low", "accident",
             datetime.now(timezone.utc).isoformat(), emp["id"]),
        )
        db.commit()
        inc = _mk_incident(db, emp["id"])
        assert inc["incident_ref"].endswith("1000")
        inc2 = _mk_incident(db, emp["id"])
        assert inc2["incident_ref"].endswith("1001")

    def test_critical_sets_48h_deadline(self, db):
        # BRN-SHE-002: critical incident -> statutory_deadline = reported_at + 48h
        emp = _mk_user(db, "employee", "emp3@test.com")
        inc = _mk_incident(db, emp["id"], severity="critical")
        assert inc["statutory_deadline"] is not None
        reported = datetime.fromisoformat(inc["reported_at"])
        deadline = datetime.fromisoformat(inc["statutory_deadline"])
        assert (deadline - reported).total_seconds() == 48 * 3600

    def test_non_critical_no_deadline(self, db):
        emp = _mk_user(db, "employee", "emp4@test.com")
        inc = _mk_incident(db, emp["id"], severity="low")
        assert inc["statutory_deadline"] is None

    def test_timeline_entry_created(self, db):
        emp = _mk_user(db, "employee", "emp5@test.com")
        inc = _mk_incident(db, emp["id"])
        from sheplatform.modules.incidents import data_service
        tl = data_service.get_timeline(db, inc["id"])
        assert len(tl) == 1
        assert "reported" in tl[0]["event_text"]


class TestIncidentLifecycle:
    def test_submit_report_and_close(self, db):
        emp = _mk_user(db, "employee", "emp6@test.com")
        mgr = _mk_user(db, "she_manager", "mgr6@test.com")
        inc = _mk_incident(db, emp["id"])

        from sheplatform.modules.incidents import data_service
        res = data_service.submit_root_cause_report(
            db, inc["id"], "Root: no guardrail", "Immediate: wet floor", "No PPE culture",
            mgr["id"])
        assert res["ok"] is True
        assert res["incident"]["status"] == "under_review"

        res = data_service.close_incident(db, inc["id"], mgr["id"])
        assert res["ok"] is True
        assert res["incident"]["status"] == "closed"


class TestIncidentToRiskIntegration:
    def test_close_creates_risk(self, db):
        # BRS cross-module integration row 3: incident.closed -> Risk Register
        emp = _mk_user(db, "employee", "emp7@test.com")
        mgr = _mk_user(db, "she_manager", "mgr7@test.com")
        inc = _mk_incident(db, emp["id"], severity="high")

        from sheplatform.modules.incidents import data_service
        data_service.submit_root_cause_report(db, inc["id"], "root", "", "", mgr["id"])
        data_service.close_incident(db, inc["id"], mgr["id"])

        from sheplatform.modules.risk_register import data_service as risk_svc
        risks = risk_svc.list_risks(db)
        assert len(risks) == 1
        assert risks[0]["source_type"] == "incident"
        assert risks[0]["source_id"] == inc["id"]
        assert risks[0]["origin_module"] == "SHEIMI"

    def test_close_twice_no_duplicate_risk(self, db):
        emp = _mk_user(db, "employee", "emp8@test.com")
        mgr = _mk_user(db, "she_manager", "mgr8@test.com")
        inc = _mk_incident(db, emp["id"], severity="medium")

        from sheplatform.modules.incidents import data_service
        data_service.submit_root_cause_report(db, inc["id"], "root", "", "", mgr["id"])
        data_service.close_incident(db, inc["id"], mgr["id"])
        data_service.close_incident(db, inc["id"], mgr["id"])

        from sheplatform.modules.risk_register import data_service as risk_svc
        risks = risk_svc.list_risks(db)
        assert len(risks) == 1  # still one risk, no duplicate


class TestRiskScoring:
    def test_residual_computed(self, db):
        from sheplatform.modules.risk_register import data_service
        risk = data_service.create_risk(
            db, hazard_description="Fire hazard", risk_category="operational",
            likelihood=5, impact=5, control_effectiveness=4,
            created_by=None)
        # inherent 25 / CE 4 = 6.25 residual
        assert float(risk["residual_score"]) == 6.25
        assert risk["inherent_score"] == 25

    def test_high_priority_flagged(self, db):
        from sheplatform.modules.risk_register import data_service
        risk = data_service.create_risk(
            db, hazard_description="Uncontrolled hazardous material", risk_category="regulatory",
            likelihood=5, impact=5, control_effectiveness=2,
            created_by=None)
        # 25 / 2 = 12.5 >= 12 -> High (CRO dashboard per BRS 11.2)
        assert risk["priority"] == "High"

    def test_risk_ref_sequence(self, db):
        from sheplatform.modules.risk_register import data_service
        r1 = data_service.create_risk(db, hazard_description="A", risk_category="operational",
                                      likelihood=2, impact=2, created_by=None)
        r2 = data_service.create_risk(db, hazard_description="B", risk_category="operational",
                                      likelihood=2, impact=2, created_by=None)
        assert r1["risk_ref"].startswith("RK-")
        s1 = int(r1["risk_ref"].split("-")[-1])
        s2 = int(r2["risk_ref"].split("-")[-1])
        assert s2 == s1 + 1
