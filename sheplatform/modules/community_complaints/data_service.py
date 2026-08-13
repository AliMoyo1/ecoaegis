"""SHECCM - Community Complaints data service (guide 12, Module 6).

Business rules:
- BRN-SHE-010: complainant must be notified BEFORE case closure (enforced)
- BRN-SHE-003: residual-risk closure -> flag Risk Register + mandate mock drill
- Multi-channel intake (FNR-SHE-007)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sheplatform.core import events
from sheplatform.database import resolve_org


def next_case_ref(db) -> str:
    year = datetime.now(timezone.utc).strftime("%Y")
    prefix = f"GRV-{year}-"
    row = db.execute(
        "SELECT case_ref FROM grievances WHERE case_ref LIKE %s ORDER BY id DESC LIMIT 1",
        (f"{prefix}%",),
    ).fetchone()
    if row is None:
        return f"{prefix}001"
    m = re.search(r"(\d+)$", row["case_ref"])
    return f"{prefix}{(int(m.group(1)) if m else 0) + 1:03d}"


def create_grievance(db, *, description: str, source_channel: str,
                     complainant_name: str = "", complainant_contact: str = "",
                     classification: str = "", severity: str = "medium",
                     created_by: int | None = None, org_id: int | None = None) -> dict:
    org_id = resolve_org(db, org_id, created_by)
    ref = next_case_ref(db)
    db.execute(
        "INSERT INTO grievances (case_ref, complainant_name, complainant_contact, "
        "source_channel, description, classification, severity, status, created_by, org_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ref, complainant_name, complainant_contact, source_channel, description,
         classification, severity, "open", created_by, org_id),
    )
    db.commit()
    grievance = get_grievance_by_ref(db, ref)
    events.emit("grievance.created", {
        "grievance_id": grievance["id"], "case_ref": ref, "severity": severity,
        "org_id": org_id, "entity_type": "grievance", "entity_id": grievance["id"],
    }, db, user_id=created_by, source_module="community_complaints")
    return grievance


def get_grievance(db, grievance_id: int) -> dict | None:
    row = db.execute("SELECT * FROM grievances WHERE id = %s", (grievance_id,)).fetchone()
    return dict(row) if row else None


def get_grievance_by_ref(db, ref: str) -> dict | None:
    row = db.execute("SELECT * FROM grievances WHERE case_ref = %s", (ref,)).fetchone()
    return dict(row) if row else None


def list_grievances(db, status: str | None = None, org_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM grievances"
    conds, params = [], []
    if status:
        conds.append("status = %s")
        params.append(status)
    if not org_id:
        return []  # fail closed: no tenant scope -> no data (audit S5)
    conds.append("org_id = %s")
    params.append(org_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def assign_investigator(db, grievance_id: int, investigator_id: int) -> dict:
    db.execute(
        "UPDATE grievances SET investigating_officer = %s, status = 'investigating' "
        "WHERE id = %s", (investigator_id, grievance_id))
    db.commit()
    return {"ok": True, "grievance": get_grievance(db, grievance_id)}


def add_action(db, grievance_id: int, action_text: str, assigned_to: int | None = None) -> dict:
    db.execute(
        "INSERT INTO grievance_actions (grievance_id, action_text, assigned_to) "
        "VALUES (%s,%s,%s)", (grievance_id, action_text, assigned_to))
    db.commit()
    row = db.execute(
        "SELECT * FROM grievance_actions WHERE grievance_id = %s ORDER BY id DESC LIMIT 1",
        (grievance_id,)).fetchone()
    return dict(row)


def resolve_grievance(db, grievance_id: int, *, resolution_outcome: str,
                      is_residual_risk: bool, asset_id: str = "") -> dict:
    """Record resolution. Status becomes 'resolved' or 'residual_risk' (awaiting close)."""
    grievance = get_grievance(db, grievance_id)
    if grievance is None:
        return {"ok": False, "message": "grievance not found"}
    status = "residual_risk" if is_residual_risk else "resolved"
    db.execute(
        "UPDATE grievances SET resolution_outcome = %s, is_residual_risk = %s, "
        "asset_id = %s, status = %s WHERE id = %s",
        (resolution_outcome, is_residual_risk, asset_id, status, grievance_id))
    db.commit()
    return {"ok": True, "grievance": get_grievance(db, grievance_id)}


def record_notification(db, grievance_id: int, method: str = "phone",
                        notified_by: int | None = None, notes: str = "") -> dict:
    """BRN-010: record that the complainant was notified.

    Audit P0-5 fix: notification is no longer anonymous self-attestation.
    The acting user is captured and an audit trail entry is written, so
    'complainant_notified' is attributable to a named SHE officer.
    """
    if not notified_by:
        return {"ok": False, "message": "notified_by is required (attestation must be attributable)"}
    from sheplatform.core.audit import log_audit
    db.execute(
        "UPDATE grievances SET complainant_notified = 1, complainant_notified_at = %s, "
        "notification_method = %s, notified_by = %s WHERE id = %s",
        (datetime.now(timezone.utc).isoformat(), method, notified_by, grievance_id))
    db.commit()
    log_audit(db, notified_by, None, "grievance.notified", "grievances", grievance_id,
              new_value={"method": method, "notes": notes})
    return {"ok": True, "grievance": get_grievance(db, grievance_id)}


def close_grievance(db, grievance_id: int, closed_by: int | None = None) -> dict:
    """Close a grievance. BRN-010: REJECT closure if complainant not notified."""
    grievance = get_grievance(db, grievance_id)
    if grievance is None:
        return {"ok": False, "message": "grievance not found"}
    if not grievance["complainant_notified"]:
        return {"ok": False, "message": "BRN-010: complainant must be notified before closure",
                "code": "BRN-010"}
    db.execute(
        "UPDATE grievances SET status = 'closed' WHERE id = %s", (grievance_id,))
    db.commit()
    events.emit("grievance.closed", {
        "grievance_id": grievance_id,
        "case_ref": grievance["case_ref"],
        "is_residual_risk": bool(grievance["is_residual_risk"]),
        "asset_id": grievance.get("asset_id"),
        "severity": grievance["severity"],
        "org_id": grievance.get("org_id"),
        "entity_type": "grievance", "entity_id": grievance_id,
    }, db, user_id=closed_by, source_module="community_complaints")
    return {"ok": True, "grievance": get_grievance(db, grievance_id)}
