"""SHE EC&SC - External Communications data service (guide 17, Module 11).

Business rules:
- BRN-SHE-008: HOD review and approval BEFORE any external release
- FNR-SHE-016: effectiveness assessment mandatory before case closure
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sheplatform.core import events


def next_comms_ref(db) -> str:
    row = db.execute(
        "SELECT comms_ref FROM external_communications ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return "COM-0001"
    m = re.search(r"(\d+)$", row["comms_ref"])
    return f"COM-{(int(m.group(1)) if m else 0) + 1:04d}"


def create_comms(db, *, concern_description: str, target_segment: str = "",
                 communication_brief: str = "", medium: str = "digital",
                 frequency: str = "", created_by: int | None = None,
                 org_id: int | None = None) -> dict:
    ref = next_comms_ref(db)
    db.execute(
        "INSERT INTO external_communications (comms_ref, concern_description, target_segment, "
        "communication_brief, medium, frequency, status, created_by, org_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ref, concern_description, target_segment, communication_brief,
         medium, frequency, "draft", created_by, org_id))
    db.commit()
    comms = get_comms_by_ref(db, ref)
    return comms


def get_comms(db, comms_id: int) -> dict | None:
    row = db.execute("SELECT * FROM external_communications WHERE id = %s", (comms_id,)).fetchone()
    return dict(row) if row else None


def get_comms_by_ref(db, ref: str) -> dict | None:
    row = db.execute("SELECT * FROM external_communications WHERE comms_ref = %s", (ref,)).fetchone()
    return dict(row) if row else None


def list_comms(db, status: str | None = None, org_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM external_communications"
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


def submit_for_hod_review(db, comms_id: int) -> dict:
    db.execute("UPDATE external_communications SET status = 'hod_review' WHERE id = %s", (comms_id,))
    db.commit()
    return {"ok": True, "comms": get_comms(db, comms_id)}


def hod_approve(db, comms_id: int, hod_user_id: int, comments: str = "") -> dict:
    """BRN-008: HOD approval. Only she_hod role may approve."""
    hod = db.execute("SELECT * FROM users WHERE id = %s", (hod_user_id,)).fetchone()
    if hod is None or hod["role_key"] != "she_hod":
        return {"ok": False, "message": "BRN-008: requires HOD approval", "code": "BRN-008"}
    db.execute(
        "UPDATE external_communications SET status = 'approved', hod_approved = 1, "
        "hod_approved_by = %s, hod_approved_at = %s, hod_comments = %s WHERE id = %s",
        (hod_user_id, datetime.now(timezone.utc).isoformat(), comments, comms_id))
    db.commit()
    return {"ok": True, "comms": get_comms(db, comms_id)}


def dispatch(db, comms_id: int, by_user: int) -> dict:
    """BRN-008 enforcement: cannot dispatch without HOD sign-off."""
    comms = get_comms(db, comms_id)
    if comms is None:
        return {"ok": False, "message": "comms not found"}
    if not comms["hod_approved"]:
        return {"ok": False, "message": "BRN-008: cannot dispatch without HOD sign-off",
                "code": "BRN-008"}
    db.execute(
        "UPDATE external_communications SET status = 'dispatched', dispatched_at = %s, "
        "dispatch_confirmation = 1 WHERE id = %s",
        (datetime.now(timezone.utc).isoformat(), comms_id))
    db.commit()
    events.emit("comms.dispatched", {
        "comms_id": comms_id, "comms_ref": comms["comms_ref"],
        "medium": comms["medium"], "org_id": comms.get("org_id"),
        "entity_type": "external_communication", "entity_id": comms_id,
    }, db, user_id=by_user, source_module="external_comms")
    return {"ok": True, "comms": get_comms(db, comms_id)}


def close_with_effectiveness(db, comms_id: int, assessment: str) -> dict:
    """FNR-SHE-016: effectiveness assessment mandatory before closure."""
    if not assessment.strip():
        return {"ok": False, "message": "FNR-016: effectiveness assessment required",
                "code": "FNR-016"}
    db.execute(
        "UPDATE external_communications SET status = 'closed', effectiveness_assessment = %s "
        "WHERE id = %s", (assessment, comms_id))
    db.commit()
    return {"ok": True, "comms": get_comms(db, comms_id)}
