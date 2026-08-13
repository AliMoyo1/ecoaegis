"""RBAC tests (guide 5.4 success criteria)."""
from __future__ import annotations

from sheplatform.core.rbac import has_capability


def _user(role):
    return {"role_key": role, "is_active": True}


class TestCapabilities:
    def test_officer_can_access_incidents(self):
        assert has_capability(_user("she_officer"), "module.incidents.access")

    def test_officer_cannot_approve_reports(self):
        assert not has_capability(_user("she_officer"), "incident.approve_report")

    def test_board_chair_can_view_risk_register(self):
        assert has_capability(_user("board_chair"), "module.risk_register.access")

    def test_board_chair_cannot_write_risk_register(self):
        assert not has_capability(_user("board_chair"), "module.risk_register.write")

    def test_champion_can_create_incident(self):
        assert has_capability(_user("she_champion"), "incident.create")

    def test_champion_cannot_investigate(self):
        assert not has_capability(_user("she_champion"), "incident.investigate")

    def test_employee_can_create_incident(self):
        assert has_capability(_user("employee"), "incident.create")

    def test_employee_cannot_access_admin(self):
        assert not has_capability(_user("employee"), "admin.users.manage")

    def test_super_admin_bypasses_all(self):
        assert has_capability(_user("super_admin"), "admin.settings.manage")
        assert has_capability(_user("super_admin"), "incident.close")

    def test_unknown_capability_denied(self):
        assert not has_capability(_user("she_manager"), "does.not.exist")

    def test_inactive_user_denied(self):
        assert not has_capability({"role_key": "super_admin", "is_active": False}, "admin.settings.manage")
