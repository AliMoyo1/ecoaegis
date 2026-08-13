"""Dashboard org-scoping tests (guide 3.4)."""
from __future__ import annotations

from sheplatform.modules.incidents.data_service import create_incident
from sheplatform.modules.launcher.dashboard_service import dashboard_stats


def _mk_user(db, role, email, org_id=1):
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "T", "U", role, org_id),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _mk_incident(db, by_user, severity="high", incident_type="environmental", org_id=1):
    return create_incident(
        db, title="Spill", description="Chemical spill", severity=severity,
        incident_type=incident_type, occurred_at="2026-08-01T10:00:00+00:00",
        reported_by=by_user, org_id=org_id)


class TestDashboardOrgScope:
    def test_org_a_excludes_org_b(self, db):
        # Ensure org 2 exists
        db.execute("INSERT OR IGNORE INTO organisations (id, name, slug) VALUES (2, 'Org 2', 'org-2')")
        db.commit()

        user_a = _mk_user(db, "she_officer", "ds_org_a@test.com", org_id=1)
        user_b = _mk_user(db, "she_officer", "ds_org_b@test.com", org_id=2)

        _mk_incident(db, user_a["id"], severity="critical", org_id=1)
        _mk_incident(db, user_b["id"], severity="critical", org_id=2)
        _mk_incident(db, user_b["id"], severity="critical", org_id=2)

        stats_a = dashboard_stats(db, user_a["id"], 1)
        stats_b = dashboard_stats(db, user_b["id"], 2)

        assert stats_a["active_incidents"] == 1
        assert stats_b["active_incidents"] == 2

    def test_no_org_id_returns_zeros(self, db):
        officer = _mk_user(db, "she_officer", "ds_no_org@test.com")
        _mk_incident(db, officer["id"], severity="critical")
        stats = dashboard_stats(db, officer["id"], None)
        assert stats["active_incidents"] == 0
        assert stats["incident_trend"]["values"] == []
