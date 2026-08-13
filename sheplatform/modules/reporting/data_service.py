"""SHER - Reporting data service (guide 16, Module 10).

- FNR-SHE-036: report types with predefined approval chains + deadlines
- BRN-SHE-012: overdue reports auto-flag to SHE Manager + CRO, escalate to COO
- FNR-SHE-039: action items from approved reports
- FNR-SHE-040: Key Issues Tracker update on approval
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sheplatform.core import events

# Approval chains per report type (guide 16)
REPORT_CHAINS = {
    "weekly_operational": [
        {"step_order": 1, "role_required": "she_manager", "sla_hours": 24},
    ],
    "monthly_management": [
        {"step_order": 1, "role_required": "she_manager", "sla_hours": 24},
        {"step_order": 2, "role_required": "cro", "sla_hours": 48},
    ],
    "project": [
        {"step_order": 1, "role_required": "she_manager", "sla_hours": 24},
        {"step_order": 2, "role_required": "cro", "sla_hours": 48},
    ],
    "board": [
        {"step_order": 1, "role_required": "she_manager", "sla_hours": 24},
        {"step_order": 2, "role_required": "cro", "sla_hours": 48},
        {"step_order": 3, "role_required": "coo", "sla_hours": 72},
        {"step_order": 4, "role_required": "board_chair", "sla_hours": 72},
    ],
    "nssa": [
        {"step_order": 1, "role_required": "she_manager", "sla_hours": 24},
        {"step_order": 2, "role_required": "cro", "sla_hours": 24},
    ],
    "ema": [
        {"step_order": 1, "role_required": "she_manager", "sla_hours": 24},
        {"step_order": 2, "role_required": "cro", "sla_hours": 24},
    ],
    "annual_sustainability": [
        {"step_order": 1, "role_required": "she_manager", "sla_hours": 48},
        {"step_order": 2, "role_required": "cro", "sla_hours": 72},
        {"step_order": 3, "role_required": "coo", "sla_hours": 72},
        {"step_order": 4, "role_required": "board_chair", "sla_hours": 72},
    ],
}


def next_report_ref(db) -> str:
    year = datetime.now(timezone.utc).strftime("%Y")
    prefix = f"RPT-{year}-"
    row = db.execute(
        "SELECT report_ref FROM she_reports WHERE report_ref LIKE %s ORDER BY id DESC LIMIT 1",
        (f"{prefix}%",)).fetchone()
    if row is None:
        return f"{prefix}001"
    m = re.search(r"(\d+)$", row["report_ref"])
    return f"{prefix}{(int(m.group(1)) if m else 0) + 1:03d}"


def create_report(db, *, report_type: str, title: str, period_start: str = "",
                  period_end: str = "", submission_deadline: str = "",
                  created_by: int | None = None, org_id: int | None = None) -> dict:
    if report_type not in REPORT_CHAINS:
        return {"ok": False, "message": "invalid report type"}
    ref = next_report_ref(db)
    db.execute(
        "INSERT INTO she_reports (report_ref, report_type, title, period_start, period_end, "
        "submission_deadline, status, created_by, org_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ref, report_type, title, period_start or None, period_end or None,
         submission_deadline or None, "draft", created_by, org_id))
    db.commit()
    row = db.execute("SELECT * FROM she_reports WHERE report_ref = %s", (ref,)).fetchone()
    return {"ok": True, "report": dict(row)}


def get_report(db, report_id: int) -> dict | None:
    row = db.execute("SELECT * FROM she_reports WHERE id = %s", (report_id,)).fetchone()
    return dict(row) if row else None


def list_reports(db, status: str | None = None, org_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM she_reports"
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


def submit_for_approval(db, report_id: int) -> dict:
    """Move report to review + create its approval chain."""
    from sheplatform.core.workflow import create_approval_chain

    report = get_report(db, report_id)
    if report is None:
        return {"ok": False, "message": "report not found"}
    db.execute("UPDATE she_reports SET status = 'review' WHERE id = %s", (report_id,))
    db.commit()
    create_approval_chain(db, "report", report_id, REPORT_CHAINS[report["report_type"]])
    return {"ok": True, "report": get_report(db, report_id)}


def approve_step(db, report_id: int, step_id: int, approver: dict, decision: str,
                 comments: str = "") -> dict:
    """Advance the report approval chain."""
    from sheplatform.core.workflow import advance_approval

    result = advance_approval(db, "report", report_id, step_id, approver, decision, comments)
    if not result["ok"]:
        return result

    if result.get("complete"):
        status = "rejected" if decision == "rejected" else "approved"
        db.execute("UPDATE she_reports SET status = %s WHERE id = %s", (status, report_id))
        db.commit()
        if status == "approved":
            events.emit("report.approved", {
                "report_id": report_id,
                "report_ref": get_report(db, report_id)["report_ref"],
                "report_type": get_report(db, report_id)["report_type"],
                "org_id": get_report(db, report_id).get("org_id"),
                "entity_type": "report", "entity_id": report_id,
            }, db, user_id=approver.get("id"), source_module="reporting")
    return result


def add_action_item(db, report_id: int, action_text: str,
                    assigned_to: int | None = None, due_date: str = "") -> dict:
    db.execute(
        "INSERT INTO report_action_items (report_id, action_text, assigned_to, due_date) "
        "VALUES (%s,%s,%s,%s)",
        (report_id, action_text, assigned_to, due_date or None))
    db.commit()
    row = db.execute("SELECT * FROM report_action_items ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row)


def add_key_issue(db, report_id: int, title: str, description: str = "",
                  severity: str = "medium", org_id: int | None = None,
                  created_by: int | None = None) -> dict:
    db.execute(
        "INSERT INTO key_issues (title, description, severity, source_report_id, org_id, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (title, description, severity, report_id, org_id, created_by))
    db.commit()
    row = db.execute("SELECT * FROM key_issues ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row)


def list_key_issues(db, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM key_issues"
    params = []
    if status:
        sql += " WHERE status = %s"
        params.append(status)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def check_overdue_reports(db) -> list[dict]:
    """BRN-SHE-012: overdue reports -> flag + notify; persistent -> COO escalation."""
    from sheplatform.core.notifications import notify_roles

    now = datetime.now(timezone.utc).isoformat()
    rows = db.execute(
        "SELECT * FROM she_reports WHERE submission_deadline IS NOT NULL "
        "AND submission_deadline < %s AND status IN ('draft','review')",
        (now,)).fetchall()
    alerts = []
    for r in rows:
        report = dict(r)
        notify_roles(db, ["she_manager", "cro"],
                     f"Report overdue: {report['report_ref']}",
                     f"{report['title']} passed its deadline ({report['submission_deadline']}).",
                     link=f"/reports/api/{report['id']}")
        # mark and escalate to COO if very late (grace 48h)
        from datetime import timedelta
        if datetime.fromisoformat(report["submission_deadline"]) < datetime.now(timezone.utc) - timedelta(hours=48):
            notify_roles(db, ["coo"], f"ESCALATED overdue report: {report['report_ref']}",
                         f"{report['title']} is over 48h past deadline.")
            db.execute("UPDATE she_reports SET status = 'overdue' WHERE id = %s", (report["id"],))
            db.commit()
        alerts.append(report)
    return alerts
