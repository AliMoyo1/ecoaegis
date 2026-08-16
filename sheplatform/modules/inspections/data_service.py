"""Inspections data service (competitor benchmark gap #2).

Workflow: scheduled -> in_progress (run checklist) -> completed (findings).
Failed checklist items auto-create a corrective action (CAPA integration).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sheplatform.core.audit import log_audit
from sheplatform.modules.map.site_relationship_service import prepare_site_assignment

TYPES = ("safety", "health", "environmental", "fire", "housekeeping", "electrical")
DEFAULT_CHECKLIST = {
    "safety": ["PPE worn by all personnel", "Emergency exits clear and lit",
               "Fire extinguishers accessible and inspected", "Warning signage in place",
               "Housekeeping - walkways free of trip hazards"],
    "environmental": ["Spill kits available and stocked", "Waste segregation bins correct",
                      "No leaks or drips from equipment", "Chemical storage labelled and locked",
                      "Drainage points free of contamination"],
    "health": ["First aid kit stocked and in date", "Welfare facilities clean and working",
               "Drinking water available", "Ventilation adequate", "Rest areas clean"],
    "fire": ["Fire alarms tested and working", "Extinguishers charged and sealed",
             "Fire exits unlocked and clear", "Assembly point signage visible",
             "Fire warden assigned and trained"],
    "electrical": ["Cables intact, no fraying", "Distribution boards closed and labelled",
                   "No overloaded sockets", "PAT testing in date", "Generator area ventilated"],
    "housekeeping": ["Floors clean and dry", "Storage stacked safely", "Bins emptied",
                     "No clutter in corridors", "Work surfaces clear"],
}


def _next_ref(db, year: int | None = None) -> str:
    year = year or datetime.now(timezone.utc).year
    row = db.execute(
        "SELECT inspection_ref FROM inspections WHERE inspection_ref LIKE %s "
        "ORDER BY id DESC LIMIT 1", (f"INSP-{year}-%",)).fetchone()
    seq = int(row["inspection_ref"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"INSP-{year}-{seq:03d}"


def get_checklist(inspection_type: str) -> list[str]:
    return DEFAULT_CHECKLIST.get(inspection_type, DEFAULT_CHECKLIST["safety"])


def schedule_inspection(db, *, title: str, inspection_type: str, site_location: str,
                        scheduled_date: str, inspector_id: int, created_by: int,
                        org_id: int | None = None, site_id: int | None = None) -> dict:
    if inspection_type not in TYPES:
        raise ValueError("invalid inspection_type")
    org_id, site_id = prepare_site_assignment(
        db, site_id=site_id, org_id=org_id, user_id=created_by
    )
    ref = _next_ref(db)
    db.execute(
        "INSERT INTO inspections (inspection_ref, title, inspection_type, site_location, site_id, "
        "scheduled_date, status, inspector_id, org_id, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'scheduled', %s, %s, %s)",
        (ref, title, inspection_type, site_location, site_id, scheduled_date,
         inspector_id, org_id, created_by))
    db.commit()
    row = db.execute("SELECT * FROM inspections WHERE inspection_ref = %s", (ref,)).fetchone()
    log_audit(db, created_by, org_id, "inspection.scheduled", "inspections", row["id"],
              new_value={"inspection_ref": ref, "title": title,
                         "inspection_type": inspection_type, "site_id": site_id})
    return dict(row)


def list_inspections(db, status: str | None = None, org_id: int | None = None) -> list[dict]:
    sql = (
        "SELECT i.*, u.email AS inspector_email, s.site_code, s.site_name "
        "FROM inspections i "
        "LEFT JOIN users u ON u.id = i.inspector_id "
        "LEFT JOIN sites s ON s.id = i.site_id AND s.org_id = i.org_id"
    )
    conds, params = [], []
    if status:
        conds.append("i.status = %s")
        params.append(status)
    if not org_id:
        return []  # fail closed: no tenant scope -> no data (audit S5)
    conds.append("i.org_id = %s")
    params.append(org_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY i.id DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def start_inspection(db, inspection_id: int, user_id: int) -> dict:
    row = db.execute("SELECT * FROM inspections WHERE id = %s", (inspection_id,)).fetchone()
    if not row:
        raise ValueError("inspection not found")
    if row["status"] != "scheduled":
        raise ValueError("only scheduled inspections can be started")
    db.execute("UPDATE inspections SET status = 'in_progress', updated_at = %s WHERE id = %s",
               (datetime.now(timezone.utc).isoformat(), inspection_id))
    db.commit()
    return dict(db.execute("SELECT * FROM inspections WHERE id = %s", (inspection_id,)).fetchone())


def complete_inspection(db, inspection_id: int, user_id: int, findings: str,
                        results: list[dict], org_id: int | None = None) -> dict:
    """results: [{"item": str, "result": "pass|fail|na", "comment": str}]

    Failed items auto-create corrective actions (audit expectation).
    """
    row = db.execute("SELECT * FROM inspections WHERE id = %s", (inspection_id,)).fetchone()
    if not row:
        raise ValueError("inspection not found")
    if row["status"] not in ("scheduled", "in_progress"):
        raise ValueError("only scheduled/in-progress inspections can be completed")
    row = dict(row)
    org_id = org_id or row.get("org_id")  # inherit tenant from the inspection

    now = datetime.now(timezone.utc).isoformat()
    db.execute("UPDATE inspections SET status = 'completed', completed_date = %s, "
               "findings = %s, updated_at = %s WHERE id = %s",
               (now, findings, now, inspection_id))
    for r in results:
        db.execute("INSERT INTO inspection_results (inspection_id, checklist_item, result, comment) "
                   "VALUES (%s, %s, %s, %s)",
                   (inspection_id, r.get("item", ""), r.get("result", "na"), r.get("comment", "")))
    db.commit()
    log_audit(db, user_id, org_id, "inspection.completed", "inspections", inspection_id,
              new_value={"findings": findings, "fails": sum(1 for r in results if r.get("result") == "fail")})

    # auto-create CAPA for failed items
    from sheplatform.modules.capa import data_service as capa
    from sheplatform.core.rbac import get_capa_default_assignee
    created = []
    for r in results:
        if r.get("result") == "fail":
            assignee = get_capa_default_assignee(db, org_id) or user_id
            action = capa.create_action(
                db, title=f"Inspection: {row['title']} - {r.get('item', '')[:80]}",
                description=(r.get("comment") or "")[:500],
                source_type="inspection", source_id=inspection_id, priority="high",
                assigned_to=assignee, due_date="", created_by=user_id, org_id=org_id)
            created.append(action["action_ref"])
    return {"inspection": dict(db.execute("SELECT * FROM inspections WHERE id = %s",
                                          (inspection_id,)).fetchone()),
            "capa_created": created}


def age_inspections(db) -> int:
    """Scheduler: mark scheduled inspections past their date as overdue."""
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        "UPDATE inspections SET status = 'overdue', updated_at = %s "
        "WHERE status = 'scheduled' AND scheduled_date IS NOT NULL AND scheduled_date < %s",
        (now, now))
    db.commit()
    return cur.rowcount
