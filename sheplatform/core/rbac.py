"""RBAC: SHE-specific roles and capabilities. Map to BRS Section 2 (guide 5.4)."""
from __future__ import annotations

ROLES = {
    "super_admin": "Super Administrator",
    "she_manager": "SHE Manager / Head of SHE",
    "she_hod": "SHE Head of Department",
    "she_officer": "SHE Officer",
    "she_champion": "SHE Champion / Auditor",
    "line_manager": "Line Manager / Project Manager",
    "cro": "Chief Risk Officer",
    "coo": "COO",
    "ceo": "CEO",
    "board_chair": "Board Committee Chairperson",
    "employee": "General Employee",
    "external": "External Portal User",
}

CAPABILITIES = {
    # Module access
    "module.dashboard.access": {"super_admin", "she_manager", "she_hod", "she_officer", "she_champion", "cro", "coo", "ceo", "board_chair", "line_manager"},
    "module.incidents.access": {"super_admin", "she_manager", "she_officer", "she_champion", "cro", "line_manager", "employee"},
    "module.risk_register.access": {"super_admin", "she_manager", "she_officer", "cro", "coo", "ceo", "board_chair"},
    "module.risk_register.write": {"super_admin", "she_manager", "she_officer"},
    "module.permits.access": {"super_admin", "she_manager", "she_officer", "line_manager", "she_hod"},
    "module.permits.approve": {"super_admin", "she_manager"},
    "module.vendors.access": {"super_admin", "she_manager", "she_officer"},
    "module.grievances.access": {"super_admin", "she_manager", "she_officer", "she_hod"},
    "module.eia.access": {"super_admin", "she_manager", "she_officer"},
    "module.emergency.access": {"super_admin", "she_manager", "she_officer", "she_hod", "cro", "coo", "ceo"},
    "module.training.access": {"super_admin", "she_manager", "she_officer", "line_manager"},
    "module.reports.access": {"super_admin", "she_manager", "she_officer", "cro", "coo", "ceo", "board_chair"},
    "module.reports.approve": {"super_admin", "cro", "coo", "ceo", "board_chair"},
    "module.statutory.access": {"super_admin", "she_manager", "she_officer", "cro", "coo", "ceo", "board_chair"},
    "module.statutory.manage": {"super_admin", "she_manager", "she_officer"},
    "module.integrations.access": {"super_admin", "she_manager", "she_officer", "cro"},
    "module.integrations.manage": {"super_admin", "she_manager"},
    "module.esg.access": {"super_admin", "she_manager", "she_officer", "cro"},
    "module.esg.manage": {"super_admin", "she_manager", "she_officer"},
    "module.esg.admin": {"super_admin", "she_manager"},
    "module.stakeholder.access": {"super_admin", "she_manager", "she_officer"},
    "module.workplan.access": {"super_admin", "she_manager", "she_officer", "cro", "coo"},
    "module.capa.access": {"super_admin", "she_manager", "she_officer", "she_hod", "line_manager", "cro"},
    "capa.create": {"super_admin", "she_manager", "she_officer", "she_hod", "line_manager"},
    "capa.verify": {"super_admin", "she_manager", "she_officer", "she_hod", "cro"},
    "capa.manage_all": {"super_admin", "she_manager", "she_officer", "she_hod"},
    "module.inspections.access": {"super_admin", "she_manager", "she_officer", "she_hod", "line_manager"},
    "inspections.schedule": {"super_admin", "she_manager", "she_officer", "she_hod"},
    "inspections.complete": {"super_admin", "she_manager", "she_officer", "she_hod", "line_manager"},
    "observations.triage": {"super_admin", "she_manager", "she_officer", "she_hod", "line_manager"},
    "documents.manage": {"super_admin", "she_manager", "she_officer", "she_hod"},
    "documents.approve": {"super_admin", "she_manager", "she_hod"},
    "module.compliance.access": {"super_admin", "she_manager", "she_officer", "cro", "coo"},
    "compliance.manage": {"super_admin", "she_manager", "she_officer"},
    "module.contractors.access": {"super_admin", "she_manager", "she_officer", "line_manager"},
    "contractors.manage": {"super_admin", "she_manager", "she_officer"},
    "module.chemicals.access": {"super_admin", "she_manager", "she_officer"},
    "chemicals.manage": {"super_admin", "she_manager", "she_officer"},
    "module.benchmark.access": {"super_admin", "she_manager", "she_officer", "cro", "coo"},
    "module.comms.access": {"super_admin", "she_manager", "she_officer", "she_hod"},
    "module.settings.access": {"super_admin", "she_manager"},

    # Incident operations
    "incident.create": {"super_admin", "she_manager", "she_officer", "she_champion", "employee"},
    "incident.investigate": {"super_admin", "she_manager", "she_officer"},
    "incident.approve_report": {"super_admin", "cro", "coo", "ceo", "board_chair"},
    "incident.close": {"super_admin", "she_manager"},

    # PTW operations
    "ptw.create": {"super_admin", "she_officer"},
    "ptw.approve": {"super_admin", "she_manager", "she_hod"},
    "ptw.close": {"super_admin", "she_officer"},

    # External comms
    "comms.draft": {"super_admin", "she_officer"},
    "comms.approve": {"super_admin", "she_hod"},

    # Admin
    "admin.users.manage": {"super_admin", "she_manager"},
    "admin.settings.manage": {"super_admin"},
}


def get_capa_default_assignee(db, org_id: int | None = None) -> int | None:
    """Default CAPA assignee: first active SHE officer IN THE GIVEN ORG.

    Fail closed on tenancy: when an org is supplied, only that org's officers
    are eligible, so a corrective action can never be auto-assigned to an
    officer in a different organisation. Returns None if the org has no active
    officer (callers fall back to the reporting user).
    """
    if org_id is not None:
        row = db.execute(
            "SELECT id FROM users WHERE role_key = 'she_officer' AND is_active = TRUE "
            "AND org_id = %s ORDER BY id LIMIT 1", (org_id,)).fetchone()
    else:
        row = db.execute(
            "SELECT id FROM users WHERE role_key = 'she_officer' AND is_active = TRUE "
            "ORDER BY id LIMIT 1").fetchone()
    return row["id"] if row else None


def has_capability(user: dict, capability: str) -> bool:
    if not user or not user.get("is_active", True):
        return False
    if user.get("role_key") == "super_admin":
        return True
    allowed_roles = CAPABILITIES.get(capability, set())
    return user.get("role_key") in allowed_roles


def require_capability(user: dict, capability: str) -> bool:
    """Return True if allowed; used by route guards (guide 5.5)."""
    return has_capability(user, capability)
