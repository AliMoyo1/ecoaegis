"""Permit to Work data service (guide 11, Module 5).

Critical rule BRN-SHE-001: no PTW without an APPROVED risk assessment.
Approval chain: Supervisor -> SHE Officer -> SHE Manager -> Site Manager.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from sheplatform.core import events


def next_permit_ref(db) -> str:
    row = db.execute(
        "SELECT permit_ref FROM permits ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "PTW-0001"
    m = re.search(r"(\d+)$", row["permit_ref"])
    return f"PTW-{(int(m.group(1)) if m else 0) + 1:04d}"


def create_permit(db, *, permit_type: str, title: str, description: str,
                  vendor_id: int, risk_assessment_id: int,
                  site_location: str = "", scope_boundary: str = "",
                  valid_from: str = "", valid_until: str = "",
                  created_by: int | None = None, org_id: int | None = None) -> dict:
    """Create a PTW. BRN-001: reject if the referenced risk assessment is not approved."""
    ra = db.execute("SELECT * FROM risk_assessments WHERE id = %s", (risk_assessment_id,)).fetchone()
    if ra is None:
        return {"ok": False, "message": "risk assessment not found"}
    if ra["status"] != "approved":
        return {"ok": False, "message": "BRN-001: PTW requires an APPROVED risk assessment",
                "code": "BRN-001"}

    ref = next_permit_ref(db)
    db.execute(
        "INSERT INTO permits (permit_ref, permit_type, title, description, vendor_id, "
        "risk_assessment_id, site_location, scope_boundary, valid_from, valid_until, "
        "status, created_by, org_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ref, permit_type, title, description, vendor_id, risk_assessment_id,
         site_location, scope_boundary, valid_from or None, valid_until or None,
         "pending_approval", created_by, org_id),
    )
    db.commit()
    permit = get_permit_by_ref(db, ref)

    # Approval chain per guide 11: Supervisor -> SHE Officer -> SHE Manager -> Site Manager
    from sheplatform.core.workflow import create_approval_chain
    create_approval_chain(db, "permit", permit["id"], [
        {"step_order": 1, "role_required": "line_manager", "sla_hours": 24},
        {"step_order": 2, "role_required": "she_officer", "sla_hours": 24},
        {"step_order": 3, "role_required": "she_manager", "sla_hours": 48},
        {"step_order": 4, "role_required": "she_hod", "sla_hours": 48},
    ])
    return {"ok": True, "permit": permit}


def get_permit(db, permit_id: int) -> dict | None:
    row = db.execute("SELECT * FROM permits WHERE id = %s", (permit_id,)).fetchone()
    return dict(row) if row else None


def get_permit_by_ref(db, ref: str) -> dict | None:
    row = db.execute("SELECT * FROM permits WHERE permit_ref = %s", (ref,)).fetchone()
    return dict(row) if row else None


def list_permits(db, status: str | None = None, org_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM permits"
    conds, params = [], []
    if status:
        conds.append("status = %s")
        params.append(status)
    if org_id:
        conds.append("org_id = %s")
        params.append(org_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def activate_permit(db, permit_id: int) -> dict:
    """After the approval chain completes, activate the permit."""
    db.execute(
        "UPDATE permits SET status = 'active' WHERE id = %s AND status = 'pending_approval'",
        (permit_id,),
    )
    db.commit()
    return get_permit(db, permit_id)


def close_permit(db, permit_id: int, *, site_restored: bool, checklist: dict,
                 closed_by: int | None = None) -> dict:
    permit = get_permit(db, permit_id)
    if permit is None:
        return {"ok": False, "message": "permit not found"}
    if not site_restored:
        return {"ok": False, "message": "site must be restored before closure"}
    db.execute(
        "UPDATE permits SET status = 'closed', closure_checklist = %s, site_restored = %s, "
        "closed_at = %s, closed_by = %s WHERE id = %s",
        (json.dumps(checklist), site_restored, datetime.now(timezone.utc).isoformat(),
         closed_by, permit_id),
    )
    db.commit()
    events.emit("permit.closed", {
        "permit_id": permit_id, "permit_ref": permit["permit_ref"],
        "vendor_id": permit["vendor_id"], "org_id": permit.get("org_id"),
        "entity_type": "permit", "entity_id": permit_id,
    }, db, user_id=closed_by, source_module="permit_to_work")
    return {"ok": True, "permit": get_permit(db, permit_id)}
