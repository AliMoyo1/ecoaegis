"""SHER tests (guide 26).

Covers report approval chains, BRN-SHE-012 (overdue escalation),
report.approved -> training need (BRS row 7).
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


class TestReportApproval:
    def test_monthly_report_chain(self, db):
        # monthly_management: she_manager -> cro
        manager = _mk_user(db, "she_manager", "rp1@test.com")
        cro = _mk_user(db, "cro", "rp2@test.com")

        from sheplatform.modules.reporting import data_service
        created = data_service.create_report(
            db, report_type="monthly_management", title="Monthly SHE report",
            created_by=manager["id"])
        assert created["ok"] is True
        report = created["report"]

        result = data_service.submit_for_approval(db, report["id"])
        assert result["report"]["status"] == "review"

        steps = db.execute(
            "SELECT * FROM approval_chain_steps WHERE chain_id = "
            "(SELECT id FROM approval_chains WHERE entity_type = 'report' AND entity_id = %s "
            "AND status = 'active' ORDER BY id DESC LIMIT 1) ORDER BY step_order",
            (report["id"],)).fetchall()
        assert [s["role_required"] for s in steps] == ["she_manager", "cro"]

        # wrong role rejected
        res = data_service.approve_step(db, report["id"], steps[0]["id"], cro, "approved")
        assert res["ok"] is False

        # correct sequence
        res = data_service.approve_step(db, report["id"], steps[0]["id"], manager, "approved")
        assert res["ok"] is True
        assert res["complete"] is False
        res = data_service.approve_step(db, report["id"], steps[1]["id"], cro, "approved")
        assert res["ok"] is True
        assert res["complete"] is True

        report = data_service.get_report(db, report["id"])
        assert report["status"] == "approved"

    def test_approved_report_creates_training_need(self, db):
        # BRS row 7: report.approved -> training need
        manager = _mk_user(db, "she_manager", "rp3@test.com")
        cro = _mk_user(db, "cro", "rp4@test.com")

        from sheplatform.modules.reporting import data_service
        created = data_service.create_report(
            db, report_type="monthly_management", title="Monthly report",
            created_by=manager["id"])
        data_service.submit_for_approval(db, created["report"]["id"])
        steps = db.execute(
            "SELECT * FROM approval_chain_steps WHERE chain_id = "
            "(SELECT id FROM approval_chains WHERE entity_type = 'report' AND entity_id = %s "
            "AND status = 'active' ORDER BY id DESC LIMIT 1) ORDER BY step_order",
            (created["report"]["id"],)).fetchall()
        data_service.approve_step(db, created["report"]["id"], steps[0]["id"], manager, "approved")
        data_service.approve_step(db, created["report"]["id"], steps[1]["id"], cro, "approved")

        needs = db.execute("SELECT * FROM training_needs").fetchall()
        assert len(needs) == 1
        assert needs[0]["source_trigger"] == "audit"
        assert needs[0]["source_id"] == created["report"]["id"]


class TestOverdue:
    def test_overdue_flags_and_escalates(self, db):
        manager = _mk_user(db, "she_manager", "rp5@test.com")
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(hours=60)).isoformat()

        from sheplatform.modules.reporting import data_service
        created = data_service.create_report(
            db, report_type="nssa", title="NSSA return",
            submission_deadline=past, created_by=manager["id"])
        assert created["ok"] is True

        alerts = data_service.check_overdue_reports(db)
        assert len(alerts) >= 1
        # very late (>48h) -> status overdue
        report = data_service.get_report(db, created["report"]["id"])
        assert report["status"] == "overdue"


class TestReportRejection:
    def test_rejection_completes_without_crash(self, db):
        """Re-audit fix: she_reports.status CHECK constraint didn't permit
        'rejected', so approve_step() raised on any rejection. Now it does.
        """
        manager = _mk_user(db, "she_manager", "rp6@test.com")
        cro = _mk_user(db, "cro", "rp7@test.com")

        from sheplatform.modules.reporting import data_service
        created = data_service.create_report(
            db, report_type="monthly_management", title="Monthly SHE report",
            created_by=manager["id"])
        data_service.submit_for_approval(db, created["report"]["id"])
        steps = db.execute(
            "SELECT * FROM approval_chain_steps WHERE chain_id = "
            "(SELECT id FROM approval_chains WHERE entity_type = 'report' AND entity_id = %s "
            "AND status = 'active' ORDER BY id DESC LIMIT 1) ORDER BY step_order",
            (created["report"]["id"],)).fetchall()

        result = data_service.approve_step(
            db, created["report"]["id"], steps[0]["id"], manager, "rejected", "needs revision")
        assert result["ok"] is True
        assert result["complete"] is True

        report = data_service.get_report(db, created["report"]["id"])
        assert report["status"] == "rejected"


class TestAnnualSustainabilityCompile:
    """SS14 / AR-FR-005/010: automated ESG KPI insertion into the annual
    sustainability report draft."""

    def test_compile_inserts_kpi_summary(self, db):
        import json
        from sheplatform.modules.esg_kpi import data_service as esg
        from sheplatform.modules.reporting import data_service as rep
        mgr = _mk_user(db, "she_manager", "as1@test.com")
        esg.seed_kpis(db, org_id=1)
        kpis = esg.list_kpis(db)
        for kpi in kpis[:2]:
            esg.record_kpi_entry(db, kpi_id=kpi["id"], period="2026-06",
                                 actual_value=0.0, target_value=100.0,
                                 created_by=mgr["id"], org_id=1)
        created = rep.create_report(
            db, report_type="annual_sustainability", title="FY26 Sustainability",
            period_start="2026-01-01T00:00:00", created_by=mgr["id"], org_id=1)
        res = rep.compile_annual_sustainability(db, created["report"]["id"], org_id=1)
        assert res["ok"], res
        assert res["summary"]["reporting_year"] == "2026"
        assert res["summary"]["kpi_count"] == 2

        report = rep.get_report(db, created["report"]["id"])
        content = report["content"]
        if isinstance(content, str):
            content = json.loads(content)
        assert "esg_kpi_summary" in content
        assert content["esg_kpi_summary"]["kpi_count"] == 2

    def test_compile_rejects_non_annual_report(self, db):
        from sheplatform.modules.reporting import data_service as rep
        mgr = _mk_user(db, "she_manager", "as2@test.com")
        created = rep.create_report(db, report_type="monthly_management",
                                    title="Monthly", created_by=mgr["id"], org_id=1)
        res = rep.compile_annual_sustainability(db, created["report"]["id"], org_id=1)
        assert res["ok"] is False

    def test_compile_is_org_scoped(self, db):
        from sheplatform.modules.reporting import data_service as rep
        mgr = _mk_user(db, "she_manager", "as3@test.com")
        created = rep.create_report(db, report_type="annual_sustainability",
                                    title="FY26", created_by=mgr["id"], org_id=1)
        # a different org may not compile this report
        res = rep.compile_annual_sustainability(db, created["report"]["id"], org_id=999)
        assert res["ok"] is False


class TestCompileHttp:
    """The compile-esg route end to end through real auth + CSRF middleware -
    the same POST path the reporting UI button uses."""

    def test_compile_route_end_to_end(self, client, db):
        from sheplatform.core.auth import hash_password
        db.execute(
            "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
            "VALUES ('rphttp@test.com', %s, 'T', 'U', 'she_manager', 1)",
            (hash_password("Test1234!"),))
        db.commit()
        client.post("/login", data={"email": "rphttp@test.com", "password": "Test1234!"})
        csrf = client.cookies.get("she_csrf", "")
        created = client.post("/reports/api/create", data={
            "report_type": "annual_sustainability", "title": "FY26",
            "period_start": "2026-01-01T00:00:00"}, headers={"X-CSRF-Token": csrf})
        assert created.status_code == 201, created.text
        report_id = created.json()["report"]["id"]
        compiled = client.post(f"/reports/api/{report_id}/compile-esg",
                               headers={"X-CSRF-Token": csrf})
        assert compiled.status_code == 200, compiled.text
        assert compiled.json()["ok"] is True


class TestReportMilestones:
    """SS14 / AR-FR-001: pre-deadline reminders at 7/3/1 days."""

    def test_alert_at_7_days_not_5(self, db):
        from datetime import datetime, timedelta, timezone
        from sheplatform.modules.reporting import data_service as rep
        mgr = _mk_user(db, "she_manager", "ms1@test.com")
        in_7 = (datetime.now(timezone.utc) + timedelta(days=7, hours=1)).isoformat()
        in_5 = (datetime.now(timezone.utc) + timedelta(days=5, hours=1)).isoformat()
        rep.create_report(db, report_type="board", title="Due in 7",
                          submission_deadline=in_7, created_by=mgr["id"], org_id=1)
        rep.create_report(db, report_type="board", title="Due in 5",
                          submission_deadline=in_5, created_by=mgr["id"], org_id=1)
        titles = {a["title"] for a in rep.check_report_milestones(db)}
        assert "Due in 7" in titles
        assert "Due in 5" not in titles


class TestKeyIssuesFiltering:
    def test_status_filter_is_parameterized(self, db):
        """Re-audit fix: list_key_issues built its WHERE clause with an
        f-string. A status value containing a quote must not break the
        query, and filtering must still work correctly.
        """
        manager = _mk_user(db, "she_manager", "rp8@test.com")
        from sheplatform.modules.reporting import data_service
        created = data_service.create_report(
            db, report_type="monthly_management", title="Report for issues",
            created_by=manager["id"])
        data_service.add_key_issue(db, created["report"]["id"], "Open issue",
                                   severity="high", created_by=manager["id"])

        # a value shaped like an injection attempt must not error or leak rows
        result = data_service.list_key_issues(db, status="open' OR '1'='1")
        assert result == []

        # normal filtering still works
        result = data_service.list_key_issues(db, status="open")
        assert len(result) == 1
        assert result[0]["title"] == "Open issue"
