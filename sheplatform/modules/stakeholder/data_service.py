"""Stakeholder Engagement data service (guide 20, Module 14).

- FNR-SHE-066: automated engagement reminders
- FNR-SHE-067: quarterly feedback capture (Q1-Q4)
- FNR-SHE-068: regulatory submission tracking per stakeholder
"""
from __future__ import annotations

import re
from datetime import datetime, timezone


def next_engagement_ref(db) -> str:
    row = db.execute(
        "SELECT engagement_ref FROM stakeholder_engagements ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return "ENG-0001"
    m = re.search(r"(\d+)$", row["engagement_ref"])
    return f"ENG-{(int(m.group(1)) if m else 0) + 1:04d}"


def create_stakeholder(db, *, name: str, category: str = "community",
                       contact_person: str = "", phone: str = "", email: str = "",
                       engagement_method: str = "", org_id: int | None = None) -> dict:
    db.execute(
        "INSERT INTO stakeholders (name, category, contact_person, phone, email, "
        "engagement_method, org_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (name, category, contact_person, phone, email, engagement_method, org_id))
    db.commit()
    row = db.execute("SELECT * FROM stakeholders ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row)


def list_stakeholders(db, org_id: int | None = None) -> list[dict]:
    if not org_id:
        return []  # fail closed: no tenant scope -> no data (audit S5)
    return [dict(r) for r in db.execute(
        "SELECT * FROM stakeholders WHERE org_id = %s ORDER BY id DESC",
        (org_id,)).fetchall()]


def create_engagement(db, *, stakeholder_id: int, engagement_issue: str,
                      she_objectives: str = "", target_date: str = "",
                      frequency: str = "", responsible_person: int | None = None,
                      linked_module: str = "", created_by: int | None = None,
                      org_id: int | None = None) -> dict:
    ref = next_engagement_ref(db)
    db.execute(
        "INSERT INTO stakeholder_engagements (engagement_ref, stakeholder_id, engagement_issue, "
        "she_objectives, target_date, frequency, responsible_person, linked_module, status, "
        "created_by, org_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ref, stakeholder_id, engagement_issue, she_objectives, target_date or None,
         frequency, responsible_person, linked_module, "active", created_by, org_id))
    db.commit()
    row = db.execute(
        "SELECT * FROM stakeholder_engagements WHERE engagement_ref = %s", (ref,)).fetchone()
    return dict(row)


def list_engagements(db, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM stakeholder_engagements"
    if status:
        sql += f" WHERE status = '{status}'"
    sql += " ORDER BY id DESC"
    return [dict(r) for r in db.execute(sql).fetchall()]


def record_quarterly_feedback(db, engagement_id: int, quarter: int, feedback: str) -> dict:
    """FNR-SHE-067: capture quarterly feedback."""
    col = {1: "q1_feedback", 2: "q2_feedback", 3: "q3_feedback", 4: "q4_feedback"}.get(quarter)
    if col is None:
        return {"ok": False, "message": "quarter must be 1-4"}
    db.execute(
        "UPDATE stakeholder_engagements SET "
        "q1_feedback = CASE WHEN %s = 1 THEN %s ELSE q1_feedback END, "
        "q2_feedback = CASE WHEN %s = 2 THEN %s ELSE q2_feedback END, "
        "q3_feedback = CASE WHEN %s = 3 THEN %s ELSE q3_feedback END, "
        "q4_feedback = CASE WHEN %s = 4 THEN %s ELSE q4_feedback END "
        "WHERE id = %s",
        (quarter, feedback, quarter, feedback, quarter, feedback, quarter, feedback,
         engagement_id))
    db.commit()
    row = db.execute("SELECT * FROM stakeholder_engagements WHERE id = %s", (engagement_id,)).fetchone()
    return {"ok": True, "engagement": dict(row)}


def complete_engagement(db, engagement_id: int) -> dict:
    db.execute("UPDATE stakeholder_engagements SET status = 'completed' WHERE id = %s",
               (engagement_id,))
    db.commit()
    row = db.execute("SELECT * FROM stakeholder_engagements WHERE id = %s", (engagement_id,)).fetchone()
    return {"ok": True, "engagement": dict(row)}


def check_overdue_engagements(db) -> list[dict]:
    """FNR-SHE-066: engagements past target_date -> flag overdue + notify."""
    from sheplatform.core.notifications import notify_roles

    now = datetime.now(timezone.utc).isoformat()
    rows = db.execute(
        "SELECT * FROM stakeholder_engagements WHERE target_date IS NOT NULL "
        "AND target_date < %s AND status = 'active'", (now,)).fetchall()
    results = []
    for r in rows:
        eng = dict(r)
        db.execute("UPDATE stakeholder_engagements SET status = 'overdue' WHERE id = %s", (eng["id"],))
        notify_roles(db, ["she_officer", "she_manager"],
                     f"Engagement overdue: {eng['engagement_ref']}",
                     f"{eng['engagement_issue']} past target date {eng['target_date']}.")
        eng["status"] = "overdue"
        results.append(eng)
    if rows:
        db.commit()
    return results
