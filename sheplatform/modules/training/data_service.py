"""SHET&A - Training data service (guide 15, Module 9).

- FNR-SHE-041: needs assessment from audits, incidents, change management
- FNR-SHE-042/BRN-014: outsourced delivery generates a Procurement request
- FNR-SHE-044: attendance links to competency matrix
- FNR-SHE-045: refresher scheduling from evaluation
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sheplatform.core import events


def _next_ref(db, table, column, prefix) -> str:
    row = db.execute(f"SELECT {column} FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return f"{prefix}0001"
    m = re.search(r"(\d+)$", row[column])
    return f"{prefix}{(int(m.group(1)) if m else 0) + 1:04d}"


def create_need(db, *, title: str, description: str = "", source_trigger: str = "gap_analysis",
                source_id: int | None = None, created_by: int | None = None,
                org_id: int | None = None) -> dict:
    ref = _next_ref(db, "training_needs", "need_ref", "TN-")
    db.execute(
        "INSERT INTO training_needs (need_ref, title, description, source_trigger, "
        "source_id, delivery_method, status, created_by, org_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ref, title, description, source_trigger, source_id, "pending",
         "identified", created_by, org_id))
    db.commit()
    row = db.execute("SELECT * FROM training_needs WHERE need_ref = %s", (ref,)).fetchone()
    return dict(row)


def list_needs(db, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM training_needs"
    if status:
        sql += f" WHERE status = '{status}'"
    sql += " ORDER BY id DESC"
    return [dict(r) for r in db.execute(sql).fetchall()]


def schedule_session(db, *, need_id: int | None, title: str, scheduled_date: str,
                     trainer: str = "", delivery_method: str = "internal",
                     created_by: int | None = None, org_id: int | None = None) -> dict:
    """Schedule a training session. BRN-014: outsourced -> procurement_ref generated."""
    ref = _next_ref(db, "training_sessions", "session_ref", "TS-")
    procurement_ref = None
    if delivery_method == "outsourced":
        procurement_ref = f"PRC-TRN-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    db.execute(
        "INSERT INTO training_sessions (session_ref, need_id, title, trainer, "
        "scheduled_date, status, created_by, org_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (ref, need_id, title, trainer, scheduled_date, "scheduled", created_by, org_id))
    db.commit()
    if need_id:
        db.execute("UPDATE training_needs SET status = 'scheduled', delivery_method = %s "
                   "WHERE id = %s", (delivery_method, need_id))
        db.commit()
    row = db.execute("SELECT * FROM training_sessions WHERE session_ref = %s", (ref,)).fetchone()
    session = dict(row)
    session["procurement_ref"] = procurement_ref
    return session


def list_sessions(db) -> list[dict]:
    return [dict(r) for r in db.execute("SELECT * FROM training_sessions ORDER BY id DESC").fetchall()]


def record_attendance(db, *, session_id: int, user_id: int, attended: bool = True,
                      competency_score: float | None = None,
                      evaluation: str = "") -> dict:
    """FNR-SHE-044: record attendance, link to competency matrix."""
    db.execute(
        "INSERT INTO training_attendance (session_id, user_id, attended, competency_score, "
        "evaluation) VALUES (%s,%s,%s,%s,%s)",
        (session_id, user_id, attended, competency_score, evaluation))
    db.commit()

    session = db.execute("SELECT * FROM training_sessions WHERE id = %s", (session_id,)).fetchone()
    if attended and session and competency_score is not None:
        # Link to competency matrix with refresher due in 12 months (FNR-SHE-045)
        from datetime import timedelta
        refresher = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
        db.execute(
            "INSERT INTO competency_matrix (user_id, competency_name, level, certified, "
            "expiry_date, source_session_id) VALUES (%s,%s,%s,%s,%s,%s)",
            (user_id, session["title"], _level_from_score(competency_score), True,
             refresher, session_id))
        db.commit()

    row = db.execute(
        "SELECT * FROM training_attendance ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row)


def _level_from_score(score: float) -> str:
    if score >= 85:
        return "expert"
    if score >= 70:
        return "advanced"
    if score >= 50:
        return "intermediate"
    return "basic"


def get_competency(db, user_id: int) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM competency_matrix WHERE user_id = %s ORDER BY id DESC", (user_id,)).fetchall()
    return [dict(r) for r in rows]
