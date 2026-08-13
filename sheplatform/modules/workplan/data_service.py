"""SHEAWPM - Annual Workplan data service (guide 18, Module 12).

Business rules:
- BRN-SHE-006: preventive/detective balance >= 40% preventive before submission
- BRN-SHE-011: block closure if no mock drill completed this year
- FNR-SHE-030: mid-year adjustment requires SHE Manager approval
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sheplatform.core import events


def next_plan_ref(db) -> str:
    row = db.execute("SELECT plan_ref FROM annual_workplan ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return "WP-0001"
    m = re.search(r"(\d+)$", row["plan_ref"])
    return f"WP-{(int(m.group(1)) if m else 0) + 1:04d}"


def create_workplan(db, *, fiscal_year: str, created_by: int | None = None,
                    org_id: int | None = None) -> dict:
    ref = next_plan_ref(db)
    db.execute(
        "INSERT INTO annual_workplan (plan_ref, fiscal_year, status, created_by, org_id) "
        "VALUES (%s,%s,%s,%s,%s)", (ref, fiscal_year, "draft", created_by, org_id))
    db.commit()
    row = db.execute("SELECT * FROM annual_workplan WHERE plan_ref = %s", (ref,)).fetchone()
    return dict(row)


def get_workplan(db, plan_id: int) -> dict | None:
    row = db.execute("SELECT * FROM annual_workplan WHERE id = %s", (plan_id,)).fetchone()
    return dict(row) if row else None


def list_workplans(db, org_id: int | None = None) -> list[dict]:
    if org_id:
        return [dict(r) for r in db.execute(
            "SELECT * FROM annual_workplan WHERE org_id = %s ORDER BY id DESC",
            (org_id,)).fetchall()]
    return [dict(r) for r in db.execute("SELECT * FROM annual_workplan ORDER BY id DESC").fetchall()]


def add_task(db, *, workplan_id: int, title: str, control_type: str,
             cost_estimate: float | None = None, milestone_date: str = "",
             assigned_to: int | None = None) -> dict:
    if control_type not in ("preventive", "detective"):
        return {"ok": False, "message": "control_type must be preventive or detective"}
    db.execute(
        "INSERT INTO workplan_tasks (workplan_id, title, control_type, cost_estimate, "
        "milestone_date, assigned_to) VALUES (%s,%s,%s,%s,%s,%s)",
        (workplan_id, title, control_type, cost_estimate, milestone_date or None, assigned_to))
    db.commit()
    row = db.execute("SELECT * FROM workplan_tasks ORDER BY id DESC LIMIT 1").fetchone()
    return {"ok": True, "task": dict(row)}


def list_tasks(db, workplan_id: int) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM workplan_tasks WHERE workplan_id = %s ORDER BY id", (workplan_id,)).fetchall()
    return [dict(r) for r in rows]


def _preventive_pct(db, workplan_id: int) -> float:
    tasks = list_tasks(db, workplan_id)
    if not tasks:
        return 0.0
    preventive = sum(1 for t in tasks if t["control_type"] == "preventive")
    return round(preventive * 100.0 / len(tasks), 1)


def submit_for_review(db, plan_id: int) -> dict:
    """BRN-006: reject submission if preventive < 40%."""
    pct = _preventive_pct(db, plan_id)
    if pct < 40:
        return {"ok": False, "message": f"BRN-006: preventive share must be >= 40% (currently {pct}%)",
                "code": "BRN-006"}
    db.execute("UPDATE annual_workplan SET status = 'committee_review', preventive_pct = %s "
               "WHERE id = %s", (pct, plan_id))
    db.commit()
    return {"ok": True, "workplan": get_workplan(db, plan_id)}


def approve_workplan(db, plan_id: int, approver_id: int) -> dict:
    db.execute(
        "UPDATE annual_workplan SET status = 'active', approved_by = %s WHERE id = %s",
        (approver_id, plan_id))
    db.commit()
    return {"ok": True, "workplan": get_workplan(db, plan_id)}


def close_workplan(db, plan_id: int) -> dict:
    """BRN-011: block closure if no mock drill completed this year."""
    from sheplatform.modules.emergency.data_service import drills_completed_this_year
    if not drills_completed_this_year(db):
        return {"ok": False,
                "message": "BRN-011: cannot close workplan year without a completed mock drill",
                "code": "BRN-011"}
    db.execute("UPDATE annual_workplan SET status = 'closed' WHERE id = %s", (plan_id,))
    db.commit()
    events.emit("workplan.closed", {
        "plan_id": plan_id,
        "entity_type": "annual_workplan", "entity_id": plan_id,
    }, db, user_id=None, source_module="workplan")
    return {"ok": True, "workplan": get_workplan(db, plan_id)}
