"""SHEIA - Environmental Impact Assessment data service (guide 13, Module 7).

Business rules:
- BRN-SHE-004: EIA clearance gate - project blocked until EIA approved
- FNR-SHE-048: consultant must be EMA-accredited before engagement
- BRN-SHE-014: consultants sourced through Procurement (procurement_ref generated)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sheplatform.core import events


def next_project_ref(db) -> str:
    year = datetime.now(timezone.utc).strftime("%Y")
    prefix = f"EIA-{year}-"
    row = db.execute(
        "SELECT project_ref FROM eia_projects WHERE project_ref LIKE %s ORDER BY id DESC LIMIT 1",
        (f"{prefix}%",),
    ).fetchone()
    if row is None:
        return f"{prefix}001"
    m = re.search(r"(\d+)$", row["project_ref"])
    return f"{prefix}{(int(m.group(1)) if m else 0) + 1:03d}"


def create_project(db, *, project_name: str, department: str = "", project_type: str = "",
                   location: str = "", created_by: int | None = None,
                   org_id: int | None = None) -> dict:
    ref = next_project_ref(db)
    db.execute(
        "INSERT INTO eia_projects (project_ref, project_name, department, project_type, "
        "location, eia_required, screening_completed, screening_result, status, blocked, "
        "created_by, org_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ref, project_name, department, project_type, location,
         None, False, "pending", "screening", False, created_by, org_id),
    )
    db.commit()
    project = get_project_by_ref(db, ref)
    return project


def get_project(db, project_id: int) -> dict | None:
    row = db.execute("SELECT * FROM eia_projects WHERE id = %s", (project_id,)).fetchone()
    return dict(row) if row else None


def get_project_by_ref(db, ref: str) -> dict | None:
    row = db.execute("SELECT * FROM eia_projects WHERE project_ref = %s", (ref,)).fetchone()
    return dict(row) if row else None


def list_projects(db, status: str | None = None, org_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM eia_projects"
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


def complete_screening(db, project_id: int, *, eia_required: bool) -> dict:
    """Screening decision. BRN-004: if EIA required, project stays blocked."""
    project = get_project(db, project_id)
    if project is None:
        return {"ok": False, "message": "project not found"}
    screening_result = "required" if eia_required else "not_required"
    blocked = eia_required  # gate: blocked until EIA approved
    status = "prospectus" if eia_required else "closed"
    db.execute(
        "UPDATE eia_projects SET eia_required = %s, screening_completed = 1, "
        "screening_result = %s, blocked = %s, status = %s WHERE id = %s",
        (eia_required, screening_result, blocked, status, project_id))
    db.commit()
    return {"ok": True, "project": get_project(db, project_id)}


def register_consultant(db, *, name: str, company: str = "",
                        ema_accreditation_number: str = "",
                        org_id: int | None = None) -> dict:
    """Register an EIA consultant. BRN-014: generates a procurement ref."""
    db.execute(
        "INSERT INTO eia_consultants (name, company, ema_accreditation_number, "
        "ema_accreditation_verified, procurement_ref, status, org_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (name, company, ema_accreditation_number, bool(ema_accreditation_number),
         f"PRC-EIA-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{name[:4].upper()}",
         "verified" if ema_accreditation_number else "pending", org_id),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM eia_consultants ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row)


def list_consultants(db, org_id: int | None = None) -> list[dict]:
    if org_id:
        rows = db.execute("SELECT * FROM eia_consultants WHERE org_id = %s ORDER BY id DESC",
                          (org_id,)).fetchall()
    else:
        rows = db.execute("SELECT * FROM eia_consultants ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def assign_consultant(db, project_id: int, consultant_id: int) -> dict:
    """FNR-SHE-048: consultant must be EMA-accredited before engagement."""
    consultant = db.execute(
        "SELECT * FROM eia_consultants WHERE id = %s", (consultant_id,)).fetchone()
    if consultant is None:
        return {"ok": False, "message": "consultant not found"}
    if not consultant["ema_accreditation_verified"]:
        return {"ok": False, "message": "FNR-048: consultant must be EMA-accredited",
                "code": "FNR-048"}
    db.execute(
        "UPDATE eia_projects SET consultant_id = %s, status = 'assessment' WHERE id = %s",
        (consultant_id, project_id))
    db.commit()
    return {"ok": True, "project": get_project(db, project_id)}


def submit_to_ema(db, project_id: int, submission_ref: str = "") -> dict:
    db.execute(
        "UPDATE eia_projects SET status = 'submitted_ema', ema_submission_date = %s, "
        "ema_submission_ref = %s, ema_decision = 'pending' WHERE id = %s",
        (datetime.now(timezone.utc).isoformat(), submission_ref, project_id))
    db.commit()
    return {"ok": True, "project": get_project(db, project_id)}


def record_ema_decision(db, project_id: int, decision: str) -> dict:
    """EMA decision. 'approved' or 'conditional' UNBLOCKS the project (BRN-004)."""
    project = get_project(db, project_id)
    if project is None:
        return {"ok": False, "message": "project not found"}
    if decision not in ("approved", "rejected", "conditional"):
        return {"ok": False, "message": "invalid decision"}
    blocked = decision == "rejected"
    status = "rejected" if decision == "rejected" else ("monitoring" if decision == "approved" else "monitoring")
    db.execute(
        "UPDATE eia_projects SET ema_decision = %s, ema_decision_date = %s, "
        "blocked = %s, status = %s WHERE id = %s",
        (decision, datetime.now(timezone.utc).isoformat(), blocked, status, project_id))
    db.commit()
    events.emit("eia.decision", {
        "project_id": project_id,
        "project_ref": project["project_ref"],
        "decision": decision,
        "blocked": blocked,
        "org_id": project.get("org_id"),
        "entity_type": "eia_project", "entity_id": project_id,
    }, db, user_id=None, source_module="eia")
    return {"ok": True, "project": get_project(db, project_id)}


def is_blocked(db, project_id: int) -> bool:
    project = get_project(db, project_id)
    return bool(project and project["blocked"])
