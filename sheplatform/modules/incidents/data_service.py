"""SHEIMI - Incident data service (guide 8).

Business rules enforced:
- BRN-SHE-002: critical incidents get a 48-hour statutory deadline from reported_at
- Ref format INC-YYYY-NNN (guide 8)
- Close flow emits incident.closed for the event bus (Risk Register handler)
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from sheplatform.core import events
from sheplatform.core.ai_client import ask_ai
from sheplatform.database import resolve_org


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
                    latitude: float | None = None, longitude: float | None = None,
                    ai_metadata: dict | None = None,
                    idempotency_key: str | None = None,
                    immediate_actions: str = "", estimated_cost: float | None = None,
                    witnesses: list | None = None) -> dict:
    """Create an incident. Sets statutory_deadline = reported_at + 48h for critical (BRN-002).

    If idempotency_key is provided and already exists, returns the existing record
    with no side effects (safe for offline replay).

    B5: immediate_actions/estimated_cost/witnesses are the incident-level intake
    depth fields (1:1 with the incident). Injured-person detail is 1:many and
    lives in incident_injuries via add_injury(), added after create since it
    needs the new incident's id.
    """
    import json
    if idempotency_key:
        row = db.execute("SELECT * FROM incidents WHERE idempotency_key = %s", (idempotency_key,)).fetchone()
        if row:
            return {**dict(row), "_idempotent": True}
    org_id = resolve_org(db, org_id, reported_by)
    ref = next_incident_ref(db)
    reported_at = datetime.now(timezone.utc)
    statutory_deadline = None
    if severity == "critical":
        statutory_deadline = (reported_at + timedelta(hours=48)).isoformat()
    ai_metadata_json = json.dumps(ai_metadata) if ai_metadata else "{}"
    witnesses_json = json.dumps(witnesses) if witnesses else "[]"

    db.execute(
        "INSERT INTO incidents (incident_ref, idempotency_key, title, description, severity, incident_type, "
        "location, latitude, longitude, occurred_at, reported_at, reported_by, org_id, "
        "statutory_deadline, ai_metadata, immediate_actions, estimated_cost, witnesses) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ref, idempotency_key, title, description, severity, incident_type, location, latitude, longitude,
         occurred_at, reported_at.isoformat(), reported_by, org_id, statutory_deadline, ai_metadata_json,
         immediate_actions or None, estimated_cost, witnesses_json),
    )
    db.commit()
    row = db.execute("SELECT * FROM incidents WHERE incident_ref = %s", (ref,)).fetchone()
    incident = dict(row)

    _add_timeline(db, incident["id"], f"Incident reported ({severity})", "reported", reported_by)
    events.emit("incident.created", {
        "incident_id": incident["id"], "ref": ref, "severity": severity, "title": title,
        "entity_type": "incident", "entity_id": incident["id"],
    }, db, user_id=reported_by, source_module="incidents")
    # Index for FTS/hybrid retrieval (A4)
    from sheplatform.modules.incidents.retrieval import index_incident
    index_incident(db, incident["id"], title, description, incident_type, severity)
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
    if not org_id:
        return []  # fail closed: no tenant scope -> no data (audit S5)
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


# ---- B5: incident intake depth (injured-person detail, LTIFR) ----

def add_injury(db, incident_id: int, *, injured_name: str = "",
               injured_type: str = "employee", body_part: str = "",
               injury_type: str = "", lost_time_days: int = 0,
               medical_treatment: str = "", created_by: int | None = None,
               org_id: int | None = None) -> dict:
    """Record an injured person against an incident. One incident can have
    several (e.g. one event injuring multiple workers), hence a child table
    rather than columns on incidents. org_id is denormalised onto the row so
    LTIFR (get_ltifr_stats) never needs to join back to incidents.

    Does not re-check the incident's org here, matching this file's existing
    convention (get_timeline, assign_team, etc. all trust the caller already
    resolved and org-checked the incident, e.g. via api_detail's guard); the
    tenant boundary for this data lives at the route layer.
    """
    incident = get_incident(db, incident_id)
    if incident is None:
        return {"ok": False, "message": "incident not found"}
    db.execute(
        "INSERT INTO incident_injuries (incident_id, injured_name, injured_type, body_part, "
        "injury_type, lost_time_days, medical_treatment, org_id, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (incident_id, injured_name or None, injured_type, body_part or None, injury_type or None,
         lost_time_days or 0, medical_treatment or None,
         org_id or incident.get("org_id"), created_by))
    db.commit()
    row = db.execute(
        "SELECT * FROM incident_injuries WHERE incident_id = %s ORDER BY id DESC LIMIT 1",
        (incident_id,)).fetchone()
    note = f"Injury recorded: {injured_name or 'unnamed'} ({body_part or 'unspecified'})"
    if lost_time_days:
        note += f", {lost_time_days} lost-time day(s)"
    _add_timeline(db, incident_id, note, "injury", created_by)
    return {"ok": True, "injury": dict(row)}


def list_injuries(db, incident_id: int) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM incident_injuries WHERE incident_id = %s ORDER BY id", (incident_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_ltifr_stats(db, org_id: int | None, period_start: str | None = None,
                    period_end: str | None = None) -> dict:
    """Lost Time Injury Frequency Rate: (lost-time injuries x 1,000,000) /
    hours worked. This is the ISO 45001 / international-standard million-hour
    base, NOT the US OSHA 200,000-hour TRIR base (verified against current
    sources; Zimbabwe/NSSA and the rest of the world outside the US follow the
    million-hour convention).

    hours_worked is read from organisations.settings.annual_exposure_hours
    (see api_set_exposure_hours). If it has never been configured, the real
    counts are still returned but ltifr is None rather than a fabricated rate
    (guide Section 7 rule 1: never invent a figure the data does not support).

    Defaults to a trailing 12-month window when no period is given, which is
    the conventional LTIFR reporting window; callers building a report for a
    specific statutory period pass period_start/period_end explicitly.
    """
    if not org_id:
        return {"lost_time_injuries": 0, "total_lost_days": 0, "hours_worked": None,
                "ltifr": None, "period_start": period_start, "period_end": period_end}
    if not period_start:
        period_start = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    if not period_end:
        period_end = datetime.now(timezone.utc).isoformat()

    row = db.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(lost_time_days), 0) AS total_days "
        "FROM incident_injuries WHERE org_id = %s AND lost_time_days > 0 "
        "AND created_at >= %s AND created_at <= %s",
        (org_id, period_start, period_end)).fetchone()
    lti_count = row["n"] if row else 0
    total_days = row["total_days"] if row else 0

    org_row = db.execute("SELECT settings FROM organisations WHERE id = %s", (org_id,)).fetchone()
    org_settings = json.loads(org_row["settings"] or "{}") if org_row else {}
    hours_worked = org_settings.get("annual_exposure_hours")

    ltifr = None
    if hours_worked:
        ltifr = round((lti_count * 1_000_000) / float(hours_worked), 2)

    return {
        "lost_time_injuries": lti_count,
        "total_lost_days": total_days,
        "hours_worked": hours_worked,
        "ltifr": ltifr,
        "period_start": period_start,
        "period_end": period_end,
    }


def set_exposure_hours(db, org_id: int, annual_exposure_hours: float) -> dict:
    """Configure the LTIFR denominator for an org. Stored on the existing
    organisations.settings JSONB column rather than a new table/column: it is
    a single admin-set number, not a growing dataset.
    """
    row = db.execute("SELECT settings FROM organisations WHERE id = %s", (org_id,)).fetchone()
    if row is None:
        return {"ok": False, "message": "organisation not found"}
    org_settings = json.loads(row["settings"] or "{}")
    org_settings["annual_exposure_hours"] = annual_exposure_hours
    db.execute("UPDATE organisations SET settings = %s WHERE id = %s",
              (json.dumps(org_settings), org_id))
    db.commit()
    return {"ok": True, "annual_exposure_hours": annual_exposure_hours}


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

    # Severity-graded approval chain (audit fix: report was never routed to
    # CRO/COO/CEO/Board - the RBAC capability existed as dead code).
    from sheplatform.core.workflow import create_approval_chain
    severity = incident["severity"]
    if severity == "critical":
        steps = [
            {"step_order": 1, "role_required": "cro", "sla_hours": 24},
            {"step_order": 2, "role_required": "coo", "sla_hours": 24},
            {"step_order": 3, "role_required": "ceo", "sla_hours": 48},
            {"step_order": 4, "role_required": "board_chair", "sla_hours": 48},
        ]
    elif severity == "high":
        steps = [
            {"step_order": 1, "role_required": "cro", "sla_hours": 24},
            {"step_order": 2, "role_required": "coo", "sla_hours": 48},
            {"step_order": 3, "role_required": "ceo", "sla_hours": 48},
        ]
    elif severity == "medium":
        steps = [
            {"step_order": 1, "role_required": "cro", "sla_hours": 48},
            {"step_order": 2, "role_required": "coo", "sla_hours": 48},
        ]
    else:  # low
        steps = [
            {"step_order": 1, "role_required": "cro", "sla_hours": 72},
        ]
    create_approval_chain(db, "incident", incident_id, steps)
    return {"ok": True, "incident": get_incident(db, incident_id)}


def get_pending_approval_step(db, incident_id: int) -> dict | None:
    """Pending step in the incident's active approval chain (for UI)."""
    row = db.execute(
        "SELECT s.id, s.step_order, s.role_required, s.sla_hours, s.status "
        "FROM approval_chain_steps s "
        "JOIN approval_chains c ON c.id = s.chain_id "
        "WHERE c.entity_type = 'incident' AND c.entity_id = %s AND c.status = 'active' "
        "AND s.status = 'pending' ORDER BY s.step_order LIMIT 1",
        (incident_id,)).fetchone()
    return dict(row) if row else None


def approval_complete(db, incident_id: int) -> bool:
    """True when the incident's approval chain has been completed (all steps approved)."""
    row = db.execute(
        "SELECT status FROM approval_chains "
        "WHERE entity_type = 'incident' AND entity_id = %s "
        "ORDER BY id DESC LIMIT 1", (incident_id,)).fetchone()
    return bool(row and row["status"] == "completed")


def approve_report_step(db, incident_id: int, step_id: int, approver: dict,
                        decision: str, comments: str = "") -> dict:
    """Approve/reject one step of the incident report approval chain."""
    from sheplatform.core.workflow import advance_approval

    result = advance_approval(db, "incident", incident_id, step_id, approver, decision, comments)
    if not result["ok"]:
        return result

    if result.get("complete"):
        if decision == "rejected":
            db.execute("UPDATE incidents SET status = 'open' WHERE id = %s", (incident_id,))
            _add_timeline(db, incident_id, "Root cause report REJECTED - returned for revision",
                          "report", approver.get("id"))
        else:
            _add_timeline(db, incident_id, "Root cause report approved - clearance to close",
                          "report", approver.get("id"))
            events.emit("incident.report_approved", {
                "incident_id": incident_id,
                "ref": get_incident(db, incident_id)["incident_ref"],
                "org_id": get_incident(db, incident_id).get("org_id"),
                "entity_type": "incident", "entity_id": incident_id,
            }, db, user_id=approver.get("id"), source_module="incidents")
        db.commit()
    return result


def close_incident(db, incident_id: int, by_user: int) -> dict:
    """Close an incident. Emits incident.closed -> Risk Register handler (BRS row 3).

    Gate: the root-cause report approval chain must be COMPLETE before close
    (audit fix - the gate was set but never checked downstream).
    """
    incident = get_incident(db, incident_id)
    if incident is None:
        return {"ok": False, "message": "incident not found"}
    if incident["status"] == "under_review" and not approval_complete(db, incident_id):
        return {"ok": False, "message": "root cause report not yet approved by the review chain"}
    # BRN-SHE-002 / FNR-SHE-026 (audit P0-6): critical and high incidents must
    # have their statutory notifications (NSSA/EMA/ZRP) recorded before closure.
    if incident["severity"] in ("critical", "high"):
        missing = [b for b in ("nssa", "ema", "zrp")
                   if not incident.get(f"{b}_notified")]
        if missing:
            return {"ok": False,
                    "message": f"statutory notification required before close: {', '.join(missing).upper()}"}
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
        "UPDATE incidents SET "
        "nssa_notified = CASE WHEN %s = 'nssa' THEN %s ELSE nssa_notified END, "
        "ema_notified = CASE WHEN %s = 'ema' THEN %s ELSE ema_notified END, "
        "zrp_notified = CASE WHEN %s = 'zrp' THEN %s ELSE zrp_notified END, "
        "nssa_notified_at = CASE WHEN %s = 'nssa' THEN %s ELSE nssa_notified_at END, "
        "ema_notified_at = CASE WHEN %s = 'ema' THEN %s ELSE ema_notified_at END, "
        "zrp_notified_at = CASE WHEN %s = 'zrp' THEN %s ELSE zrp_notified_at END "
        "WHERE id = %s",
        (body, notified, body, notified, body, notified,
         body, datetime.now(timezone.utc).isoformat() if notified else None,
         body, datetime.now(timezone.utc).isoformat() if notified else None,
         body, datetime.now(timezone.utc).isoformat() if notified else None,
         incident_id))
    db.commit()
    return {"ok": True, "incident": get_incident(db, incident_id)}
