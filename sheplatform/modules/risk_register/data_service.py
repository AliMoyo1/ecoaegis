"""Risk Register - data service (guide 9, Module 3).

- Residual = inherent / control_effectiveness (BRS 11.2)
- Scores >= 12 auto-flag as High (CRO dashboard)
- Drill-down to source record (FNR-SHE-059)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sheplatform.core import events


def next_risk_ref(db) -> str:
    """Generate RK-YYYY-NNN from the most recent risk this year.

    Order by id DESC (insertion order), NOT risk_ref: ref strings sort
    lexicographically, so 'RK-2026-1000' < 'RK-2026-999' and ORDER BY ref
    would pick the wrong max once refs reach 4 digits.
    """
    year = datetime.now(timezone.utc).strftime("%Y")
    prefix = f"RK-{year}-"
    row = db.execute(
        "SELECT risk_ref FROM risks WHERE risk_ref LIKE %s ORDER BY id DESC LIMIT 1",
        (f"{prefix}%",),
    ).fetchone()
    if row is None:
        return f"{prefix}001"
    match = re.search(r"(\d+)$", row["risk_ref"])
    nxt = (int(match.group(1)) if match else 0) + 1
    return f"{prefix}{nxt:03d}"


def _priority(residual: float) -> str:
    """BRS 11.2: >= 12 High (CRO), 8-11 Medium, <= 7 Low."""
    if residual >= 12:
        return "High"
    if residual >= 8:
        return "Medium"
    return "Low"


def _with_priority(risk: dict) -> dict:
    risk = dict(risk)
    risk["priority"] = _priority(float(risk["residual_score"] or 0))
    return risk


def create_risk(db, *, hazard_description: str, risk_category: str,
                likelihood: int, impact: int, control_effectiveness: int = 1,
                process_function: str = "", managerial_response: str = "mitigate",
                status: str = "open", source_type: str | None = None,
                source_id: int | None = None, origin_module: str | None = None,
                created_by: int | None = None, org_id: int | None = None,
                statutory_instrument: str = "") -> dict:
    """Create a risk. Computed columns handle inherent/residual/priority in DB."""
    ref = next_risk_ref(db)
    db.execute(
        "INSERT INTO risks (risk_ref, process_function, hazard_description, risk_category, "
        "likelihood, impact, control_effectiveness, managerial_response, status, "
        "source_type, source_id, origin_module, statutory_instrument, created_by, org_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ref, process_function, hazard_description, risk_category, likelihood, impact,
         control_effectiveness, managerial_response, status, source_type, source_id,
         origin_module, statutory_instrument, created_by, org_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM risks WHERE risk_ref = %s", (ref,)).fetchone()
    risk = _with_priority(row)
    events.emit("risk.created", {
        "risk_id": risk["id"], "ref": ref, "hazard_description": hazard_description,
        "residual_score": float(risk["residual_score"]),
        "entity_type": "risk", "entity_id": risk["id"],
    }, db, user_id=created_by, source_module="risk_register")
    return risk


def list_risks(db, category: str | None = None, status: str | None = None,
               org_id: int | None = None, limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM risks"
    conds, params = [], []
    if category:
        conds.append("risk_category = %s")
        params.append(category)
    if status:
        conds.append("status = %s")
        params.append(status)
    if org_id:
        conds.append("org_id = %s")
        params.append(org_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY residual_score DESC, id DESC LIMIT %s"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [_with_priority(r) for r in rows]


def get_risk(db, risk_id: int) -> dict | None:
    row = db.execute("SELECT * FROM risks WHERE id = %s", (risk_id,)).fetchone()
    return _with_priority(row) if row else None


def update_risk(db, risk_id: int, *, likelihood: int | None = None,
                impact: int | None = None, control_effectiveness: int | None = None,
                managerial_response: str | None = None, status: str | None = None,
                status_update: str | None = None, by_user: int | None = None) -> dict | None:
    risk = get_risk(db, risk_id)
    if risk is None:
        return None
    new_l = likelihood if likelihood is not None else risk["likelihood"]
    new_i = impact if impact is not None else risk["impact"]
    new_ce = control_effectiveness if control_effectiveness is not None else risk["control_effectiveness"]
    db.execute(
        "UPDATE risks SET likelihood = %s, impact = %s, control_effectiveness = %s, "
        "managerial_response = COALESCE(%s, managerial_response), "
        "status = COALESCE(%s, status), status_update = COALESCE(%s, status_update) WHERE id = %s",
        (new_l, new_i, new_ce, managerial_response, status, status_update, risk_id),
    )
    db.commit()
    updated = _with_priority(get_risk(db, risk_id))
    events.emit("risk.updated", {
        "risk_id": risk_id, "ref": updated["risk_ref"],
        "residual_score": float(updated["residual_score"]),
        "entity_type": "risk", "entity_id": risk_id,
    }, db, user_id=by_user, source_module="risk_register")
    return updated


# ---------------------------------------------------------------------------
# Event handler: incident.closed -> create/update risk (BRS cross-module row 3)
# ---------------------------------------------------------------------------
def _severity_to_likelihood_impact(severity: str) -> tuple[int, int]:
    """Map incident severity to risk likelihood x impact (operational view)."""
    mapping = {
        "critical": (5, 5),   # inherent 25
        "high": (4, 4),       # inherent 16
        "medium": (3, 3),     # inherent 9
        "low": (2, 2),        # inherent 4
        "near_miss": (2, 3),  # inherent 6
    }
    return mapping.get(severity, (3, 3))


@events.on("incident.closed")
def handle_incident_closed(payload: dict, db) -> None:
    """Create or update a Risk Register entry from a closed incident (BRS row 3)."""
    incident_id = payload["incident_id"]
    org_id = payload.get("org_id")
    # Find an existing risk linked to this incident (avoid duplicates)
    existing = db.execute(
        "SELECT * FROM risks WHERE source_type = 'incident' AND source_id = %s",
        (incident_id,),
    ).fetchone()

    likelihood, impact = _severity_to_likelihood_impact(payload.get("severity", "medium"))
    title = f"Incident: {payload.get('title', '')} ({payload.get('ref', '')})"

    if existing:
        update_risk(db, existing["id"], likelihood=likelihood, impact=impact,
                    status_update=f"Updated on incident close: {payload.get('root_cause', '')}",
                    by_user=payload.get("user_id"))
    else:
        create_risk(
            db,
            hazard_description=title[:500],
            risk_category="operational",
            likelihood=likelihood,
            impact=impact,
            control_effectiveness=3,
            managerial_response="mitigate",
            status="open",
            source_type="incident",
            source_id=incident_id,
            origin_module="SHEIMI",
            statutory_instrument="NSSA / EMA reporting obligations",
            created_by=payload.get("user_id"),
            org_id=org_id,
        )
