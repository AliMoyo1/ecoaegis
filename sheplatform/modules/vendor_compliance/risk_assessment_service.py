"""Risk Assessment data service (SHECMV subsystem, guide 10).

Assessment must be APPROVED before any PTW referencing it can be created (BRN-001).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone


def next_assessment_ref(db) -> str:
    row = db.execute(
        "SELECT assessment_ref FROM risk_assessments ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "RA-0001"
    m = re.search(r"(\d+)$", row["assessment_ref"])
    return f"RA-{(int(m.group(1)) if m else 0) + 1:04d}"


def create_assessment(db, *, vendor_id: int, scope_of_work: str,
                      workforce_size: int | None = None, equipment_list: str = "",
                      site_conditions: str = "", risk_rating: str = "medium",
                      created_by: int | None = None, org_id: int | None = None) -> dict:
    ref = next_assessment_ref(db)
    db.execute(
        "INSERT INTO risk_assessments (vendor_id, assessment_ref, scope_of_work, "
        "workforce_size, equipment_list, site_conditions, risk_rating, status, "
        "created_by, org_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (vendor_id, ref, scope_of_work, workforce_size, equipment_list,
         site_conditions, risk_rating, "draft", created_by, org_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM risk_assessments WHERE assessment_ref = %s", (ref,)).fetchone()
    return dict(row)


def get_assessment(db, assessment_id: int) -> dict | None:
    row = db.execute("SELECT * FROM risk_assessments WHERE id = %s", (assessment_id,)).fetchone()
    return dict(row) if row else None


def list_assessments(db, vendor_id: int | None = None, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM risk_assessments"
    conds, params = [], []
    if vendor_id:
        conds.append("vendor_id = %s")
        params.append(vendor_id)
    if status:
        conds.append("status = %s")
        params.append(status)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def approve_assessment(db, assessment_id: int, approver_id: int) -> dict:
    db.execute(
        "UPDATE risk_assessments SET status = 'approved', approved_by = %s, approved_at = %s "
        "WHERE id = %s AND status IN ('draft','submitted')",
        (approver_id, datetime.now(timezone.utc).isoformat(), assessment_id),
    )
    db.commit()
    return {"ok": True, "assessment": get_assessment(db, assessment_id)}
