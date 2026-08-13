"""Approval chain engine (guide 5.6, BRN-SHE-005).

Chains cannot be skipped, bypassed, or delegated to lower-authority roles.
Atomic check-and-set prevents double-approval races.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


def create_approval_chain(db, entity_type: str, entity_id: int, steps: list[dict]) -> None:
    """Create a sequential approval chain.

    steps = [
        {"step_order": 1, "role_required": "cro", "sla_hours": 24},
        {"step_order": 2, "role_required": "ceo", "sla_hours": 48},
    ]
    First step becomes 'pending', the rest 'waiting'.
    """
    from sheplatform.core.audit import log_audit

    db.execute(
        "INSERT INTO approval_chains (entity_type, entity_id, status, created_at) "
        "VALUES (%s, %s, %s, %s)",
        (entity_type, entity_id, "active", datetime.now(timezone.utc).isoformat()),
    )
    db.commit()
    chain_id = db.execute(
        "SELECT id FROM approval_chains WHERE entity_type = %s AND entity_id = %s ORDER BY id DESC LIMIT 1",
        (entity_type, entity_id),
    ).fetchone()["id"]

    for i, step in enumerate(steps):
        status = "pending" if i == 0 else "waiting"
        db.execute(
            "INSERT INTO approval_chain_steps "
            "(chain_id, step_order, role_required, sla_hours, sla_deadline, status) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (chain_id, step["step_order"], step["role_required"], step.get("sla_hours", 48),
             (datetime.now(timezone.utc) + timedelta(hours=step.get("sla_hours", 48))).isoformat(),
             status),
        )
    db.commit()
    log_audit(db, None, None, "approval_chain.created", entity_type, entity_id,
              new_value={"steps": steps})


def advance_approval(db, entity_type: str, entity_id: int, step_id: int,
                     approver: dict, decision: str, comments: str = "") -> dict:
    """Record an approval decision. Validates role, order, and atomicity.

    Returns {"ok": bool, "next_step": int|None, "complete": bool, "message": str}
    """
    from sheplatform.core.audit import log_audit

    chain = db.execute(
        "SELECT * FROM approval_chains WHERE entity_type = %s AND entity_id = %s AND status = 'active' "
        "ORDER BY id DESC LIMIT 1",
        (entity_type, entity_id),
    ).fetchone()
    if chain is None:
        return {"ok": False, "message": "no active approval chain"}

    step = db.execute(
        "SELECT * FROM approval_chain_steps WHERE id = %s AND chain_id = %s",
        (step_id, chain["id"]),
    ).fetchone()
    if step is None:
        return {"ok": False, "message": "step not found"}

    # Role check (BRN-SHE-005: no delegation to lower authority)
    if approver.get("role_key") != step["role_required"]:
        return {"ok": False, "message": f"requires role {step['role_required']}"}

    # Atomic check-and-set: only a 'pending' step can be decided
    cur = db.execute(
        "UPDATE approval_chain_steps SET status = %s, decided_at = %s, decided_by = %s, comments = %s "
        "WHERE id = %s AND status = 'pending'",
        (decision, datetime.now(timezone.utc).isoformat(), approver.get("id"), comments, step_id),
    )
    db.commit()
    if cur.rowcount == 0:
        return {"ok": False, "message": "step already handled"}

    log_audit(db, approver.get("id"), None, f"approval.{decision}", entity_type, entity_id,
              old_value={"step": step["step_order"]}, new_value={"decision": decision, "comments": comments})

    if decision == "rejected":
        db.execute("UPDATE approval_chains SET status = 'rejected' WHERE id = %s", (chain["id"],))
        db.commit()
        return {"ok": True, "complete": True, "message": "chain rejected", "next_step": None}

    # Approved: advance the next waiting step
    nxt = db.execute(
        "SELECT id, step_order FROM approval_chain_steps "
        "WHERE chain_id = %s AND step_order = %s AND status = 'waiting'",
        (chain["id"], step["step_order"] + 1),
    ).fetchone()
    if nxt is None:
        db.execute("UPDATE approval_chains SET status = 'completed' WHERE id = %s", (chain["id"],))
        db.commit()
        return {"ok": True, "complete": True, "message": "chain complete", "next_step": None}

    db.execute("UPDATE approval_chain_steps SET status = 'pending' WHERE id = %s", (nxt["id"],))
    db.commit()
    return {"ok": True, "complete": False, "message": "advanced", "next_step": nxt["id"]}


def check_sla_breaches(db) -> list[dict]:
    """Called by scheduler. Finds pending steps past SLA deadline, escalates."""
    now = datetime.now(timezone.utc).isoformat()
    rows = db.execute(
        "SELECT * FROM approval_chain_steps WHERE status = 'pending' AND sla_deadline < %s",
        (now,),
    ).fetchall()
    breaches = []
    for r in rows:
        db.execute(
            "UPDATE approval_chain_steps SET status = 'escalated', comments = "
            "COALESCE(comments, '') || ' [SLA breach auto-escalated]' WHERE id = %s",
            (r["id"],),
        )
        breaches.append(dict(r))
    if breaches:
        db.commit()
    return breaches
