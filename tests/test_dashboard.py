"""Dashboard service tests: every query runs against the real schema."""
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


def _mk_incident(db, by_user, severity="high", incident_type="environmental"):
    from sheplatform.modules.incidents.data_service import create_incident
    return create_incident(
        db, title="Spill", description="Chemical spill", severity=severity,
        incident_type=incident_type, occurred_at="2026-08-01T10:00:00+00:00",
        reported_by=by_user)


class TestDashboardService:
    def test_stats_shape(self, db):
        officer = _mk_user(db, "she_officer", "ds1@test.com")
        _mk_incident(db, officer["id"], severity="critical")

        from sheplatform.modules.launcher.dashboard_service import dashboard_stats
        stats = dashboard_stats(db, officer["id"])

        assert stats["active_incidents"] >= 1
        assert "incident_trend" in stats
        assert len(stats["incident_trend"]["labels"]) == 12
        assert len(stats["incident_trend"]["values"]) == 12
        assert "severity_distribution" in stats
        assert "risk_heatmap" in stats
        assert len(stats["risk_heatmap"]) == 5  # 5x5
        assert all(len(row) == 5 for row in stats["risk_heatmap"])
        assert "upcoming_deadlines" in stats
        assert "expiring_certs" in stats
        assert "near_miss_ratio" in stats
        assert "ca_closure_rate" in stats
        assert "key_issues" in stats

    def test_heatmap_counts_risks(self, db):
        officer = _mk_user(db, "she_officer", "ds2@test.com")
        from sheplatform.modules.risk_register import data_service as risk_svc
        risk_svc.create_risk(
            db, hazard_description="Test risk", risk_category="operational",
            likelihood=4, impact=5, control_effectiveness=2,
            created_by=officer["id"])

        from sheplatform.modules.launcher.dashboard_service import dashboard_stats
        stats = dashboard_stats(db, officer["id"])
        # likelihood 4 (index 3), impact 5 (index 4)
        assert stats["risk_heatmap"][3][4] >= 1
