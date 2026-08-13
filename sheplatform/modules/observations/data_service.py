"""Observations data service (competitor benchmark gap #3).

"See something, say something" - any employee reports a hazard/near-miss/
unsafe act in seconds. SHE staff triage. High/critical observations
auto-create a corrective action.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sheplatform.core.audit import log_audit


def _next_ref(db, year: int | None = None) -> str:
    year = year or datetime.now(timezone.utc).year
    row = db.execute(
        "SELECT obs_ref FROM observations WHERE obs_ref LIKE %s "
        "ORDER BY id DESC LIMIT 1", (f"OBS-{year}-%",)).fetchone()
    seq = int(row["obs_ref"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"OBS-{year}-{seq:03d}"


def create_observation(db, *, obs_type: str, title: str, description: str,
                       location: str, severity: str, reported_by: int,
                       org_id: int | None = None) -> dict:
    if obs_type not in ("hazard", "near_miss", "unsafe_act", "unsafe_condition", "good_practice"):
        raise ValueError("invalid obs_type")
    if severity not in ("low", "medium", "high", "critical"):
        raise ValueError("invalid severity")
    ref = _next_ref(db)
    db.execute(
        "INSERT INTO observations (obs_ref, obs_type, title, description, location, "
        "severity, status, reported_by, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'open', %s, %s)",
        (ref, obs_type, title, description, location, severity, reported_by, org_id))
    db.commit()
    log_audit(db, reported_by, org_id, "observation.created", "observations", ref,
              new_value={"obs_type": obs_type, "severity": severity})
    return dict(db.execute("SELECT * FROM observations WHERE obs_ref = %s", (ref,)).fetchone())


def list_observations(db, status: str | None = None, severity: str | None = None,
                      org_id: int | None = None) -> list[dict]:
    sql = ("SELECT o.*, u.email AS reporter_email FROM observations o "
           "LEFT JOIN users u ON u.id = o.reported_by")
    conds, params = [], []
    if status:
        conds.append("o.status = %s")
        params.append(status)
    if severity:
        conds.append("o.severity = %s")
        params.append(severity)
    if not org_id:
        return []  # fail closed: no tenant scope -> no data (audit S5)
    conds.append("o.org_id = %s")
    params.append(org_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY o.id DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def acknowledge_observation(db, obs_id: int, user_id: int) -> dict:
    row = db.execute("SELECT * FROM observations WHERE id = %s", (obs_id,)).fetchone()
    if not row:
        raise ValueError("observation not found")
    db.execute("UPDATE observations SET status = 'acknowledged', updated_at = %s WHERE id = %s",
               (datetime.now(timezone.utc).isoformat(), obs_id))
    db.commit()
    return dict(db.execute("SELECT * FROM observations WHERE id = %s", (obs_id,)).fetchone())


def raise_corrective_action(db, obs_id: int, user_id: int, org_id: int | None = None) -> dict:
    """Triage: escalate to a corrective action. Returns the created CAPA ref."""
    row = db.execute("SELECT * FROM observations WHERE id = %s", (obs_id,)).fetchone()
    if not row:
        raise ValueError("observation not found")
    if row["status"] == "corrective_action":
        raise ValueError("already raised")
    row = dict(row)
    org_id = org_id or row.get("org_id")  # inherit tenant from the observation

    from sheplatform.modules.capa import data_service as capa
    from sheplatform.core.rbac import get_capa_default_assignee

    assignee = get_capa_default_assignee(db, org_id) or user_id
    action = capa.create_action(
        db, title=f"Observation: {row['title'][:80]}",
        description=(row.get("description") or "")[:500],
        source_type="incident", source_id=0,
        priority="critical" if row["severity"] == "critical" else "high",
        assigned_to=assignee, due_date="", created_by=user_id, org_id=org_id)
    db.execute("UPDATE observations SET status = 'corrective_action', updated_at = %s WHERE id = %s",
               (datetime.now(timezone.utc).isoformat(), obs_id))
    db.commit()
    return {"capa_ref": action["action_ref"],
            "observation": dict(db.execute("SELECT * FROM observations WHERE id = %s",
                                           (obs_id,)).fetchone())}


def close_observation(db, obs_id: int, user_id: int, resolution: str = "") -> dict:
    row = db.execute("SELECT * FROM observations WHERE id = %s", (obs_id,)).fetchone()
    if not row:
        raise ValueError("observation not found")
    now = datetime.now(timezone.utc).isoformat()
    new_desc = f"{row['description'] or ''}\nResolution: {resolution}".strip()
    db.execute("UPDATE observations SET status = 'closed', description = %s, updated_at = %s "
               "WHERE id = %s", (new_desc, now, obs_id))
    db.commit()
    log_audit(db, user_id, None, "observation.closed", "observations", row["obs_ref"])
    return dict(db.execute("SELECT * FROM observations WHERE id = %s", (obs_id,)).fetchone())
