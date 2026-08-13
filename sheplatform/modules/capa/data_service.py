"""CAPA (Corrective and Preventive Actions) data service.

Closed-loop workflow (competitor benchmark gap #1):
  open -> in_progress -> completed -> verified
Verification must be done by a DIFFERENT user than the assignee (2-person rule,
audit requirement). Ageing: overdue when due_date passes while not completed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sheplatform.core.audit import log_audit

STATUSES = ("open", "in_progress", "overdue", "completed", "verified")


def _next_ref(db, year: int | None = None) -> str:
    year = year or datetime.now(timezone.utc).year
    row = db.execute(
        "SELECT action_ref FROM corrective_actions WHERE action_ref LIKE %s "
        "ORDER BY id DESC LIMIT 1", (f"CA-{year}-%",)).fetchone()
    seq = int(row["action_ref"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"CA-{year}-{seq:03d}"


def create_action(db, *, title: str, description: str, source_type: str,
                  source_id: int, priority: str, assigned_to: int,
                  due_date: str, created_by: int, org_id: int | None = None) -> dict:
    if source_type not in ("incident", "audit", "inspection", "grievance", "drill", "report"):
        raise ValueError("invalid source_type")
    if priority not in ("critical", "high", "medium", "low"):
        raise ValueError("invalid priority")
    ref = _next_ref(db)
    db.execute(
        "INSERT INTO corrective_actions (action_ref, source_type, source_id, title, "
        "description, priority, status, assigned_to, due_date, org_id, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'open', %s, %s, %s, %s)",
        (ref, source_type, source_id, title, description, priority, assigned_to,
         due_date, org_id, created_by))
    db.commit()
    row = db.execute("SELECT * FROM corrective_actions WHERE action_ref = %s", (ref,)).fetchone()
    log_audit(db, created_by, org_id, "capa.action_created", "capa", ref,
              new_value={"title": title, "priority": priority, "assigned_to": assigned_to})
    return dict(row)


def list_actions(db, status: str | None = None, org_id: int | None = None) -> list[dict]:
    sql = ("SELECT ca.*, u.email AS assignee_email, u.first_name AS assignee_first, "
           "u.last_name AS assignee_last, v.email AS verifier_email "
           "FROM corrective_actions ca "
           "LEFT JOIN users u ON u.id = ca.assigned_to "
           "LEFT JOIN users v ON v.id = ca.verified_by")
    conds, params = [], []
    if status:
        conds.append("ca.status = %s")
        params.append(status)
    if org_id:
        conds.append("ca.org_id = %s")
        params.append(org_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY ca.id DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def start_action(db, action_id: int, user_id: int) -> dict:
    _require_assignee(db, action_id, user_id, allow_she=True)
    db.execute("UPDATE corrective_actions SET status = 'in_progress', updated_at = %s "
               "WHERE id = %s", (datetime.now(timezone.utc).isoformat(), action_id))
    db.commit()
    return dict(db.execute("SELECT * FROM corrective_actions WHERE id = %s", (action_id,)).fetchone())


def complete_action(db, action_id: int, user_id: int, completion_note: str = "") -> dict:
    """Assignee marks completed with a note."""
    row = _require_assignee(db, action_id, user_id, allow_she=True)
    now = datetime.now(timezone.utc).isoformat()
    new_desc = row["description"] or ""
    if completion_note:
        new_desc = f"{new_desc}\nCompletion: {completion_note}".strip()
    db.execute("UPDATE corrective_actions SET status = 'completed', completed_at = %s, "
               "description = %s, updated_at = %s WHERE id = %s",
               (now, new_desc, now, action_id))
    db.commit()
    log_audit(db, user_id, None, "capa.action_completed", "capa",
              db.execute("SELECT action_ref FROM corrective_actions WHERE id = %s",
                         (action_id,)).fetchone()["action_ref"])
    return dict(db.execute("SELECT * FROM corrective_actions WHERE id = %s", (action_id,)).fetchone())


def verify_action(db, action_id: int, user_id: int, verification_note: str = "") -> dict:
    """2-person rule: verifier MUST differ from assignee."""
    row = db.execute("SELECT * FROM corrective_actions WHERE id = %s", (action_id,)).fetchone()
    if not row:
        raise ValueError("action not found")
    if row["status"] != "completed":
        raise ValueError("only completed actions can be verified")
    if row["assigned_to"] == user_id:
        raise ValueError("verifier cannot be the assignee (2-person rule)")
    now = datetime.now(timezone.utc).isoformat()
    new_desc = row["description"] or ""
    if verification_note:
        new_desc = f"{new_desc}\nVerification: {verification_note}".strip()
    db.execute("UPDATE corrective_actions SET status = 'verified', verified_by = %s, "
               "verified_at = %s, description = %s, updated_at = %s WHERE id = %s",
               (user_id, now, new_desc, now, action_id))
    db.commit()
    log_audit(db, user_id, None, "capa.action_verified", "capa",
              row["action_ref"], new_value={"verified_by": user_id})
    return dict(db.execute("SELECT * FROM corrective_actions WHERE id = %s", (action_id,)).fetchone())


def _require_assignee(db, action_id: int, user_id: int, allow_she: bool = True) -> dict:
    row = db.execute("SELECT * FROM corrective_actions WHERE id = %s", (action_id,)).fetchone()
    if not row:
        raise ValueError("action not found")
    # SHE officers/managers may manage any action; others must be the assignee
    if allow_she:
        from sheplatform.core.rbac import has_capability
        from sheplatform.core.auth import get_user_by_id
        user = get_user_by_id(db, user_id)
        if user and has_capability(user, "capa.manage_all"):
            return dict(row)
    if row["assigned_to"] != user_id:
        raise ValueError("not assigned to this user")
    return dict(row)


def age_actions(db) -> int:
    """Scheduler: mark overdue where due_date passed and not completed/verified."""
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        "UPDATE corrective_actions SET status = 'overdue', updated_at = %s "
        "WHERE status IN ('open','in_progress') AND due_date IS NOT NULL AND due_date < %s",
        (now, now))
    db.commit()
    return cur.rowcount
