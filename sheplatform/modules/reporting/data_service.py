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


def compile_annual_sustainability(db, report_id: int, org_id: int | None) -> dict:
    """SS14/AR-FR-005/010, FNR-SHE-062: auto-insert the ESG KPI summary into an
    annual sustainability report draft.

    Pulls the org's ESG KPI entries for the report's reporting year and writes a
    structured summary (per-KPI actual/target/variance/RAG, category rollups,
    overall RAG counts) into she_reports.content. Reused by the reporting UI and
    exportable via the report itself. Org-scoped and fails closed.
    """
    import json

    if not org_id:
        return {"ok": False, "message": "organisation scope required"}
    report = get_report(db, report_id)
    if report is None or report.get("org_id") != org_id:
        return {"ok": False, "message": "report not found"}
    if report["report_type"] != "annual_sustainability":
        return {"ok": False, "message": "only annual_sustainability reports can be compiled"}
    if report["status"] not in ("draft", "review"):
        return {"ok": False, "message": "report is locked (already approved/submitted)"}

    # Reporting year: from period_start, else period_end, else current year.
    year = str((report.get("period_start") or report.get("period_end")
                or datetime.now(timezone.utc).isoformat()))[:4]

    # Latest entry per KPI for that year, org-scoped (entries + KPI metadata).
    rows = db.execute(
        "SELECT k.kpi_code, k.name, k.unit, k.category, "
        "e.actual_value, e.target_value, e.variance, e.rag_status, e.period "
        "FROM esg_kpi_entries e JOIN esg_kpis k ON k.id = e.kpi_id "
        "WHERE e.org_id = %s AND e.period LIKE %s "
        "ORDER BY k.category, k.kpi_code, e.period DESC",
        (org_id, f"{year}%"),
    ).fetchall()

    kpis: dict[str, dict] = {}
    rag_counts = {"red": 0, "amber": 0, "green": 0}
    for r in rows:
        row = dict(r)
        code = row["kpi_code"]
        if code in kpis:  # already have the latest period (ORDER BY period DESC)
            continue
        kpis[code] = {
            "kpi_code": code, "name": row["name"], "unit": row["unit"],
            "category": row["category"], "actual": row["actual_value"],
            "target": row["target_value"], "variance": row["variance"],
            "rag": row["rag_status"], "period": row["period"],
        }
        if row["rag_status"] in rag_counts:
            rag_counts[row["rag_status"]] += 1

    by_category: dict[str, list] = {}
    for kpi in kpis.values():
        by_category.setdefault(kpi["category"] or "other", []).append(kpi)

    # content is TEXT/JSONB; the app stores JSON text and reads it back as a
    # string on both backends (SQLite TEXT + the PG JSONB->str caster).
    raw = report.get("content")
    if isinstance(raw, str):
        content = json.loads(raw or "{}")
    elif isinstance(raw, dict):
        content = dict(raw)
    else:
        content = {}
    content["esg_kpi_summary"] = {
        "reporting_year": year,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kpi_count": len(kpis),
        "rag_counts": rag_counts,
        "by_category": by_category,
    }
    db.execute(
        "UPDATE she_reports SET content = %s, updated_at = %s WHERE id = %s AND org_id = %s",
        (json.dumps(content), datetime.now(timezone.utc).isoformat(), report_id, org_id))
    db.commit()
    return {"ok": True, "summary": content["esg_kpi_summary"]}


def check_report_milestones(db) -> list[dict]:
    """SS14/AR-FR-001: reporting-calendar milestone alerts BEFORE the deadline.

    Notifies the SHE Manager when a draft/review report's submission deadline is
    7, 3, or 1 days away. Run daily, days-remaining lands on each milestone
    exactly once, so no per-milestone sent-tracking column is needed.
    """
    from sheplatform.core.notifications import notify_roles

    now = datetime.now(timezone.utc)
    rows = db.execute(
        "SELECT * FROM she_reports WHERE submission_deadline IS NOT NULL "
        "AND status IN ('draft','review')").fetchall()
    alerts = []
    for r in rows:
        report = dict(r)
        try:
            deadline = datetime.fromisoformat(str(report["submission_deadline"]))
        except (TypeError, ValueError):
            continue
        days_left = (deadline - now).days
        if days_left in (7, 3, 1):
            notify_roles(db, ["she_manager"],
                         f"Report due in {days_left} day(s): {report['report_ref']}",
                         f"{report['title']} is due {report['submission_deadline']}.",
                         link=f"/reports/api/{report['id']}")
            alerts.append({**report, "days_left": days_left})
    return alerts


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
