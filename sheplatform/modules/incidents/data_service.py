"""SHEIMI - Incident data service (guide 8).

Business rules enforced:
- BRN-SHE-002: critical incidents get a 48-hour statutory deadline from reported_at
- Ref format INC-YYYY-NNN (guide 8)
- Close flow emits incident.closed for the event bus (Risk Register handler)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sheplatform.core import events


def next_incident_ref(db) -> str:
    """Generate INC-YYYY-NNN from the most recent incident this year.

    Order by id DESC (insertion order), NOT incident_ref: ref strings sort
    lexicographically, so 'INC-2026-1000' < 'INC-2026-999' and ORDER BY ref
    would pick the wrong max once refs reach 4 digits.
    """
    year = datetime.now(timezone.utc).strftime("%Y")
    prefix = f"INC-{year}-"
    row = db.execute(
        "SELECT incident_ref FROM incidents WHERE incident_ref LIKE %s ORDER BY id DESC LIMIT 1",
        (f"{prefix}%",),
    ).fetchone()
    if row is None:
        return f"{prefix}001"
    match = re.search(r"(\d+)$", row["incident_ref"])
    nxt = (int(match.group(1)) if match else 0) + 1
    return f"{prefix}{nxt:03d}"


def create_incident(db, *, title: str, description: str, severity: str,
                    incident_type: str, occurred_at: str, location: str = "",
                    reported_by: int, org_id: int | None = None,
                    latitude: float | None = None, longitude: float | None = None) -> dict:
    """Create an incident. Sets statutory_deadline = reported_at + 48h for critical (BRN-002)."""
    ref = next_incident_ref(db)
    reported_at = datetime.now(timezone.utc)
    statutory_deadline = None
    if severity == "critical":
        statutory_deadline = (reported_at + timedelta(hours=48)).isoformat()

    db.execute(
        "INSERT INTO incidents (incident_ref, title, description, severity, incident_type, "
        "location, latitude, longitude, occurred_at, reported_at, reported_by, org_id, "
        "statutory_deadline) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ref, title, description, severity, incident_type, location, latitude, longitude,
         occurred_at, reported_at.isoformat(), reported_by, org_id, statutory_deadline),
    )
    db.commit()
    row = db.execute("SELECT * FROM incidents WHERE incident_ref = %s", (ref,)).fetchone()
    incident = dict(row)

    _add_timeline(db, incident["id"], f"Incident reported ({severity})", "reported", reported_by)
    events.emit("incident.created", {
        "incident_id": incident["id"], "ref": ref, "severity": severity, "title": title,
        "entity_type": "incident", "entity_id": incident["id"],
    }, db, user_id=reported_by, source_module="incidents")
    return incident


def _add_timeline(db, incident_id: int, event_text: str, event_type: str = "update",
                  created_by: int | None = None) -> None:
    db.execute(
        "INSERT INTO incident_timeline (incident_id, event_text, event_type, occurred_at, created_by) "
        "VALUES (%s, %s, %s, %s, %s)",
        (incident_id, event_text, event_type,
         datetime.now(timezone.utc).isoformat(), created_by),
    )
    db.commit()


def list_incidents(db, status: str | None = None, severity: str | None = None,
                   incident_type: str | None = None,
                   org_id: int | None = None, limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM incidents"
    conds, params = [], []
    if status:
        conds.append("status = %s")
        params.append(status)
    if severity:
        conds.append("severity = %s")
        params.append(severity)
    if incident_type:
        conds.append("incident_type = %s")
        params.append(incident_type)
    if org_id:
        conds.append("org_id = %s")
        params.append(org_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_incident(db, incident_id: int) -> dict | None:
    row = db.execute("SELECT * FROM incidents WHERE id = %s", (incident_id,)).fetchone()
    return dict(row) if row else None


def add_timeline_entry(db, incident_id: int, event_text: str, created_by: int) -> dict:
    _add_timeline(db, incident_id, event_text, "update", created_by)
    row = db.execute(
        "SELECT * FROM incident_timeline WHERE incident_id = %s ORDER BY id DESC LIMIT 1",
        (incident_id,),
    ).fetchone()
    return dict(row)


def get_timeline(db, incident_id: int) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM incident_timeline WHERE incident_id = %s ORDER BY id", (incident_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def assign_team(db, incident_id: int, user_ids: list[int], by_user: int) -> dict:
    incident = get_incident(db, incident_id)
    if incident is None:
        return {"ok": False, "message": "incident not found"}
    db.execute(
        "UPDATE incidents SET status = 'investigation', assigned_to = %s WHERE id = %s",
        (user_ids[0] if user_ids else None, incident_id),
    )
    db.execute("UPDATE incidents SET investigation_team = %s WHERE id = %s",
               (__import__("json").dumps(user_ids), incident_id))
    db.commit()
    _add_timeline(db, incident_id, f"Investigation team assigned: {len(user_ids)} member(s)",
                  "assignment", by_user)
    return {"ok": True, "incident": get_incident(db, incident_id)}


def submit_root_cause_report(db, incident_id: int, root_cause: str, immediate_cause: str,
                             contributing_factors: str, by_user: int) -> dict:
    incident = get_incident(db, incident_id)
    if incident is None:
        return {"ok": False, "message": "incident not found"}
    db.execute(
        "UPDATE incidents SET root_cause = %s, immediate_cause = %s, contributing_factors = %s, "
        "status = 'under_review' WHERE id = %s",
        (root_cause, immediate_cause, contributing_factors, incident_id),
    )
    db.commit()
    _add_timeline(db, incident_id, "Root cause report submitted for approval", "report", by_user)
    return {"ok": True, "incident": get_incident(db, incident_id)}


def close_incident(db, incident_id: int, by_user: int) -> dict:
    """Close an incident. Emits incident.closed -> Risk Register handler (BRS row 3)."""
    incident = get_incident(db, incident_id)
    if incident is None:
        return {"ok": False, "message": "incident not found"}
    db.execute(
        "UPDATE incidents SET status = 'closed', closed_at = %s, closed_by = %s WHERE id = %s",
        (datetime.now(timezone.utc).isoformat(), by_user, incident_id),
    )
    db.commit()
    _add_timeline(db, incident_id, "Incident closed", "closed", by_user)
    events.emit("incident.closed", {
        "incident_id": incident_id,
        "ref": incident["incident_ref"],
        "root_cause": incident.get("root_cause"),
        "severity": incident["severity"],
        "title": incident["title"],
        "org_id": incident.get("org_id"),
        "user_id": by_user,
        "entity_type": "incident",
        "entity_id": incident_id,
    }, db, user_id=by_user, source_module="incidents")
    return {"ok": True, "incident": get_incident(db, incident_id)}


def set_statutory_notified(db, incident_id: int, body: str, notified: bool = True) -> dict:
    """Mark a statutory body as notified (NSSA/EMA/ZRP)."""
    col = {"nssa": "nssa_notified", "ema": "ema_notified", "zrp": "zrp_notified"}.get(body)
    if col is None:
        return {"ok": False, "message": "unknown body"}
    db.execute(
        f"UPDATE incidents SET {col} = %s, {col.replace('_notified', '_notified_at')} = %s WHERE id = %s",
        (1 if notified else 0, datetime.now(timezone.utc).isoformat() if notified else None, incident_id),
    )
    db.commit()
    return {"ok": True, "incident": get_incident(db, incident_id)}
