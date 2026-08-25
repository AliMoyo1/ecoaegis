"""SHET&A event handler (guide 21, BRS row 8, BRN-SHE-009).

incident.closed -> training alignment on *major* incident closure:
- create a training need traceable to the incident (FNR-SHE-041 source trigger),
  and
- flag a Key Issue that stays visible in the Key Issues Tracker until resolved
  (BRN-SHE-009: "Any unaddressed training gaps ... must remain flagged and
  visible in the Key Issues Tracker until resolved").

Complements reporting.event_handlers (report.approved -> training need):
that path covers report-driven gaps; this one covers incident-closure gaps.
"""
from __future__ import annotations

from sheplatform.core import events


@events.on("incident.closed")
def handle_incident_closed(payload: dict, db) -> None:
    # BRN-SHE-009 is triggered by *major* incident closure.
    if payload.get("severity") not in ("critical", "high"):
        return
    incident_id = payload.get("incident_id")
    ref = payload.get("ref") or "incident"
    root_cause = (payload.get("root_cause") or "").strip()
    org_id = payload.get("org_id")

    from sheplatform.modules.training import data_service as training

    detail = (f"Confirm the root cause of {ref} is reflected in the next "
              f"scheduled training module.")
    if root_cause:
        detail += f" Root cause: {root_cause}"

    # FNR-SHE-041: training need traceable to its source trigger (the incident).
    training.create_need(
        db, title=f"Training alignment: {ref}", description=detail,
        source_trigger="incident", source_id=incident_id, org_id=org_id)

    # BRN-SHE-009: flag in the Key Issues Tracker; stays 'open' (visible) until
    # a SHE Officer confirms the gap is addressed and resolves it.
    db.execute(
        "INSERT INTO key_issues (title, description, severity, status, org_id) "
        "VALUES (%s, %s, %s, 'open', %s)",
        (f"Training gap unconfirmed: {ref}", detail,
         "high" if payload.get("severity") == "critical" else "medium", org_id))
    db.commit()
