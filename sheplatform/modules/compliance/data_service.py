"""Compliance obligations register data service (competitor benchmark gap #5).

Central statutory calendar: NSSA, EMA, ZRP, labour obligations with owners,
frequencies and renewal dates. Overdue detection for the scheduler.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sheplatform.core.audit import log_audit

FREQUENCIES = ("annual", "semi_annual", "quarterly", "monthly", "event_based", "continuous")


def _next_ref(db) -> str:
    row = db.execute("SELECT obligation_ref FROM compliance_obligations "
                     "ORDER BY id DESC LIMIT 1").fetchone()
    seq = int(row["obligation_ref"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"OBL-{seq:03d}"


def create_obligation(db, *, regulation: str, obligation: str, regulator: str,
                      owner_id: int, frequency: str, next_due_date: str = "",
                      created_by: int, org_id: int | None = None) -> dict:
    if frequency not in FREQUENCIES:
        raise ValueError("invalid frequency")
    ref = _next_ref(db)
    db.execute(
        "INSERT INTO compliance_obligations (obligation_ref, regulation, obligation, "
        "regulator, owner_id, frequency, next_due_date, status, org_id, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, %s)",
        (ref, regulation, obligation, regulator, owner_id, frequency,
         next_due_date or None, org_id, created_by))
    db.commit()
    log_audit(db, created_by, org_id, "obligation.created", "compliance", ref,
              new_value={"regulation": regulation, "regulator": regulator})
    return dict(db.execute("SELECT * FROM compliance_obligations WHERE obligation_ref = %s",
                           (ref,)).fetchone())


def list_obligations(db, status: str | None = None, regulator: str | None = None,
                     org_id: int | None = None) -> list[dict]:
    sql = ("SELECT o.*, u.email AS owner_email FROM compliance_obligations o "
           "LEFT JOIN users u ON u.id = o.owner_id")
    conds, params = [], []
    if status:
        conds.append("o.status = %s")
        params.append(status)
    if regulator:
        conds.append("o.regulator = %s")
        params.append(regulator)
    if org_id:
        conds.append("o.org_id = %s")
        params.append(org_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY o.next_due_date ASC NULLS LAST, o.id DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def mark_compliant(db, ob_id: int, user_id: int, evidence: str = "") -> dict:
    row = db.execute("SELECT * FROM compliance_obligations WHERE id = %s", (ob_id,)).fetchone()
    if not row:
        raise ValueError("obligation not found")
    now = datetime.now(timezone.utc).isoformat()
    db.execute("UPDATE compliance_obligations SET status = 'compliant', evidence_path = %s, "
               "updated_at = %s WHERE id = %s", (evidence or row["evidence_path"], now, ob_id))
    db.commit()
    log_audit(db, user_id, None, "obligation.compliant", "compliance", row["obligation_ref"])
    return dict(db.execute("SELECT * FROM compliance_obligations WHERE id = %s", (ob_id,)).fetchone())


def age_obligations(db) -> int:
    """Scheduler: active obligations past due become overdue."""
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        "UPDATE compliance_obligations SET status = 'overdue', updated_at = %s "
        "WHERE status = 'active' AND next_due_date IS NOT NULL AND next_due_date < %s",
        (now, now))
    db.commit()
    return cur.rowcount
