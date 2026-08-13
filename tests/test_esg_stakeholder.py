"""ESG + Stakeholder tests (guide 26).

Covers FNR-SHE-063 (non-zero critical KPI -> auto incident),
RAG status computation, stakeholder quarterly feedback.
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


class TestEsgKpi:
    def test_seed_kpis(self, db):
        from sheplatform.modules.esg_kpi import data_service
        n = data_service.seed_kpis(db)
        assert n == 12
        assert data_service.seed_kpis(db) == 0  # idempotent
        kpis = data_service.list_kpis(db)
        assert len(kpis) == 12

    def test_nonzero_critical_kpi_creates_incident(self, db):
        # FNR-SHE-063: non-zero spillage -> auto-created incident
        officer = _mk_user(db, "she_officer", "es1@test.com")
        from sheplatform.modules.esg_kpi import data_service
        data_service.seed_kpis(db)
        spill_kpi = data_service.get_kpi(db, 3)  # ESG-ENV-03 diesel spillage
        assert spill_kpi["kpi_code"] == "ESG-ENV-03"

        result = data_service.record_kpi_entry(
            db, kpi_id=spill_kpi["id"], period="2026-08", actual_value=50,
            created_by=officer["id"])
        assert result["ok"] is True
        assert result["entry"]["linked_incident_id"] is not None
        assert result["entry"]["rag_status"] == "red"

        # incident created
        from sheplatform.modules.incidents import data_service as inc_svc
        incidents = inc_svc.list_incidents(db, org_id=1)
        assert len(incidents) == 1
        assert "spillage" in incidents[0]["title"].lower()

    def test_zero_critical_kpi_no_incident(self, db):
        officer = _mk_user(db, "she_officer", "es2@test.com")
        from sheplatform.modules.esg_kpi import data_service
        data_service.seed_kpis(db)
        spill_kpi = data_service.get_kpi(db, 3)
        result = data_service.record_kpi_entry(
            db, kpi_id=spill_kpi["id"], period="2026-08", actual_value=0,
            created_by=officer["id"])
        assert result["ok"] is True
        assert result["entry"]["linked_incident_id"] is None
        assert result["entry"]["rag_status"] == "green"

    def test_rag_thresholds(self, db):
        officer = _mk_user(db, "she_officer", "es3@test.com")
        from sheplatform.modules.esg_kpi import data_service
        data_service.seed_kpis(db)
        # energy KPI (max type), target 100
        energy = [k for k in data_service.list_kpis(db) if k["kpi_code"] == "ESG-ENV-05"][0]
        # over 10% above target -> red
        r = data_service.record_kpi_entry(db, kpi_id=energy["id"], period="2026-08",
                                          actual_value=120, target_value=100,
                                          created_by=officer["id"])
        assert r["entry"]["rag_status"] == "red"
        # slightly above -> amber
        r = data_service.record_kpi_entry(db, kpi_id=energy["id"], period="2026-08",
                                          actual_value=105, target_value=100,
                                          created_by=officer["id"])
        assert r["entry"]["rag_status"] == "amber"
        # at/below -> green
        r = data_service.record_kpi_entry(db, kpi_id=energy["id"], period="2026-08",
                                          actual_value=95, target_value=100,
                                          created_by=officer["id"])
        assert r["entry"]["rag_status"] == "green"


class TestStakeholder:
    def test_quarterly_feedback(self, db):
        officer = _mk_user(db, "she_officer", "sh1@test.com")
        from sheplatform.modules.stakeholder import data_service
        stakeholder = data_service.create_stakeholder(
            db, name="EMA", category="regulator", contact_person="Ms Mapfumo",
            org_id=None)
        engagement = data_service.create_engagement(
            db, stakeholder_id=stakeholder["id"],
            engagement_issue="EIA submission coordination",
            created_by=officer["id"])

        result = data_service.record_quarterly_feedback(
            db, engagement["id"], 1, "Positive engagement, docs on track")
        assert result["ok"] is True
        assert "Positive" in result["engagement"]["q1_feedback"]

        # invalid quarter rejected
        result = data_service.record_quarterly_feedback(db, engagement["id"], 5, "x")
        assert result["ok"] is False

    def test_overdue_detection(self, db):
        from datetime import datetime, timedelta, timezone
        officer = _mk_user(db, "she_officer", "sh2@test.com")
        from sheplatform.modules.stakeholder import data_service
        stakeholder = data_service.create_stakeholder(
            db, name="Community Trust", category="community", org_id=None)
        past = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        data_service.create_engagement(
            db, stakeholder_id=stakeholder["id"], engagement_issue="Quarterly consult",
            target_date=past, created_by=officer["id"])

        overdue = data_service.check_overdue_engagements(db)
        assert len(overdue) == 1
        assert overdue[0]["status"] == "overdue"
