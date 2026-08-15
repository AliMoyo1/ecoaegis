"""SHEEPRP + SHEER - Emergency data service (guide 14, Module 8).

Business rules:
- FNR-SHE-017: IMT/ECMT appointment with contact details
- BRN-SHE-011: at least one mock drill per year (block workplan closure otherwise)
- BRN-SHE-007: Site Safe for Occupation = dual sign-off (SHE Manager AND HOD Security)
- emergency.post_crisis -> EPRP improvement queue (BRS row 6)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sheplatform.core import events
from sheplatform.modules.map.site_relationship_service import prepare_site_assignment


# ---- ref generators (audit fix: identifiers hardcoded, no interpolation) ----
def _next_plan_ref(db) -> str:
    row = db.execute("SELECT plan_ref FROM emergency_plans ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return "EPRP-0001"
    m = re.search(r"(\d+)$", row["plan_ref"])
    return f"EPRP-{(int(m.group(1)) if m else 0) + 1:04d}"


def _next_drill_ref(db) -> str:
    row = db.execute("SELECT drill_ref FROM mock_drills ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return "DRL-0001"
    m = re.search(r"(\d+)$", row["drill_ref"])
    return f"DRL-{(int(m.group(1)) if m else 0) + 1:04d}"


def _next_event_ref(db) -> str:
    row = db.execute("SELECT event_ref FROM emergency_events ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return "EMG-0001"
    m = re.search(r"(\d+)$", row["event_ref"])
    return f"EMG-{(int(m.group(1)) if m else 0) + 1:04d}"


# ---- Emergency plans (SHEEPRP) ----
def create_plan(db, *, title: str, created_by: int | None = None,
                org_id: int | None = None) -> dict:
    ref = _next_plan_ref(db)
    db.execute(
        "INSERT INTO emergency_plans (plan_ref, title, status, created_by, org_id) "
        "VALUES (%s, %s, %s, %s, %s)",
        (ref, title, "draft", created_by, org_id))
    db.commit()
    row = db.execute("SELECT * FROM emergency_plans WHERE plan_ref = %s", (ref,)).fetchone()
    return dict(row)


def get_plan(db, plan_id: int) -> dict | None:
    row = db.execute("SELECT * FROM emergency_plans WHERE id = %s", (plan_id,)).fetchone()
    return dict(row) if row else None


def list_plans(db, org_id: int | None = None) -> list[dict]:
    if not org_id:
        return []  # fail closed: no tenant scope -> no data (audit S5)
    return [dict(r) for r in db.execute(
        "SELECT * FROM emergency_plans WHERE org_id = %s ORDER BY id DESC",
        (org_id,)).fetchall()]


def approve_plan(db, plan_id: int, approver_id: int) -> dict:
    db.execute(
        "UPDATE emergency_plans SET status = 'active', approved_by = %s, approved_at = %s "
        "WHERE id = %s AND status IN ('draft','review')",
        (approver_id, datetime.now(timezone.utc).isoformat(), plan_id))
    db.commit()
    return {"ok": True, "plan": get_plan(db, plan_id)}


def add_team_member(db, plan_id: int, *, team_type: str, member_name: str,
                    role: str, contact_phone: str = "", contact_email: str = "") -> dict:
    """FNR-SHE-017: IMT/ECMT appointment."""
    db.execute(
        "INSERT INTO emergency_teams (plan_id, team_type, member_name, role, "
        "contact_phone, contact_email) VALUES (%s,%s,%s,%s,%s,%s)",
        (plan_id, team_type, member_name, role, contact_phone, contact_email))
    db.commit()
    row = db.execute("SELECT * FROM emergency_teams ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row)


# ---- Mock drills ----
def schedule_drill(db, *, plan_id: int | None, drill_type: str, scheduled_date: str,
                   created_by: int | None = None, org_id: int | None = None) -> dict:
    ref = _next_drill_ref(db)
    db.execute(
        "INSERT INTO mock_drills (drill_ref, plan_id, drill_type, scheduled_date, status, "
        "created_by, org_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (ref, plan_id, drill_type, scheduled_date, "scheduled", created_by, org_id))
    db.commit()
    row = db.execute("SELECT * FROM mock_drills WHERE drill_ref = %s", (ref,)).fetchone()
    return dict(row)


def complete_drill(db, drill_id: int, *, observations: str = "", feedback: str = "") -> dict:
    db.execute(
        "UPDATE mock_drills SET status = 'completed', actual_date = %s, observations = %s, "
        "feedback = %s WHERE id = %s",
        (datetime.now(timezone.utc).isoformat(), observations, feedback, drill_id))
    db.commit()
    return {"ok": True, "drill": get_drill(db, drill_id)}


def get_drill(db, drill_id: int) -> dict | None:
    row = db.execute("SELECT * FROM mock_drills WHERE id = %s", (drill_id,)).fetchone()
    return dict(row) if row else None


def list_drills(db, org_id: int | None = None) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT * FROM mock_drills ORDER BY id DESC").fetchall()]


def drills_completed_this_year(db) -> bool:
    """BRN-011: at least one completed drill in the current year."""
    year_start = f"{datetime.now(timezone.utc).strftime('%Y')}-01-01"
    row = db.execute(
        "SELECT COUNT(*) AS c FROM mock_drills WHERE status = 'completed' "
        "AND actual_date >= %s", (year_start,)).fetchone()
    return (row["c"] if row else 0) > 0


def add_drill_improvement(db, drill_id: int, description: str) -> dict:
    db.execute(
        "INSERT INTO drill_improvements (drill_id, description) VALUES (%s, %s)",
        (drill_id, description))
    db.commit()
    row = db.execute(
        "SELECT * FROM drill_improvements ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row)


# ---- Emergency events (SHEER) ----
def create_emergency(db, *, title: str, description: str, severity: str,
                     site_location: str = "", created_by: int | None = None,
                     org_id: int | None = None, site_id: int | None = None) -> dict:
    org_id, site_id = prepare_site_assignment(
        db, site_id=site_id, org_id=org_id, user_id=created_by
    )
    ref = _next_event_ref(db)
    db.execute(
        "INSERT INTO emergency_events (event_ref, title, description, severity, "
        "site_location, site_id, status, created_by, org_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ref, title, description, severity, site_location, site_id,
         "active", created_by, org_id))
    db.commit()
    row = db.execute("SELECT * FROM emergency_events WHERE event_ref = %s", (ref,)).fetchone()
    return dict(row)


def get_emergency(db, emergency_id: int) -> dict | None:
    row = db.execute("SELECT * FROM emergency_events WHERE id = %s", (emergency_id,)).fetchone()
    return dict(row) if row else None


def list_emergencies(db, org_id: int | None = None) -> list[dict]:
    if not org_id:
        return []  # fail closed: no tenant scope -> no data (audit S5)
    return [dict(r) for r in db.execute(
        "SELECT * FROM emergency_events WHERE org_id = %s ORDER BY id DESC",
        (org_id,)).fetchall()]


def issue_site_safe_certificate(db, emergency_id: int, *, she_manager_id: int,
                                hod_security_id: int) -> dict:
    """BRN-SHE-007: dual sign-off (SHE Manager AND HOD Security)."""
    from sheplatform.core.rbac import ROLES

    sm = db.execute("SELECT * FROM users WHERE id = %s", (she_manager_id,)).fetchone()
    hs = db.execute("SELECT * FROM users WHERE id = %s", (hod_security_id,)).fetchone()
    if sm is None or hs is None:
        return {"ok": False, "message": "approver not found"}
    if sm["role_key"] != "she_manager":
        return {"ok": False, "message": "BRN-007: first sign-off requires SHE Manager",
                "code": "BRN-007"}
    if hs["role_key"] != "she_hod":
        return {"ok": False, "message": "BRN-007: second sign-off requires HOD Security",
                "code": "BRN-007"}

    import json
    certified_by = json.dumps([
        {"user_id": she_manager_id, "role": "she_manager", "signed_at": datetime.now(timezone.utc).isoformat()},
        {"user_id": hod_security_id, "role": "she_hod", "signed_at": datetime.now(timezone.utc).isoformat()},
    ])
    db.execute(
        "UPDATE emergency_events SET site_safe_certificate = TRUE, site_certified_by = %s, "
        "status = 'contained' WHERE id = %s",
        (certified_by, emergency_id))
    db.commit()
    return {"ok": True, "emergency": get_emergency(db, emergency_id)}


def transition_post_crisis(db, emergency_id: int, *, root_cause: str = "") -> dict:
    """Move to post_crisis. Emits emergency.post_crisis -> EPRP improvement queue (BRS row 6).

    Gate (audit P0-2): site re-entry / post-crisis transition is BLOCKED until
    the Site Safe for Occupation certificate is issued (BRN-SHE-007 dual
    sign-off). Previously the certificate blocked nothing.
    """
    emergency = get_emergency(db, emergency_id)
    if emergency is None:
        return {"ok": False, "message": "emergency not found"}
    if not emergency.get("site_safe_certificate"):
        return {"ok": False, "message": "BRN-007: site safe certificate required before re-entry",
                "code": "BRN-007"}
    db.execute(
        "UPDATE emergency_events SET status = 'post_crisis', root_cause = %s WHERE id = %s",
        (root_cause, emergency_id))
    db.commit()
    events.emit("emergency.post_crisis", {
        "emergency_id": emergency_id,
        "event_ref": emergency["event_ref"],
        "root_cause": root_cause,
        "severity": emergency["severity"],
        "org_id": emergency.get("org_id"),
        "entity_type": "emergency_event", "entity_id": emergency_id,
    }, db, user_id=None, source_module="emergency")
    return {"ok": True, "emergency": get_emergency(db, emergency_id)}
