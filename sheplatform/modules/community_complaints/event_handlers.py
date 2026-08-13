"""SHECCM event handlers (guide 21, BRN-SHE-003).

grievance.closed with residual risk:
1. Flag asset in Risk Register (BRS row 4)
2. Mandate a mock drill (BRS row 5, BRN-SHE-003)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sheplatform.core import events


def _next_drill_ref(db) -> str:
    row = db.execute("SELECT drill_ref FROM mock_drills ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return "DRL-0001"
    m = re.search(r"(\d+)$", row["drill_ref"])
    return f"DRL-{(int(m.group(1)) if m else 0) + 1:04d}"


@events.on("grievance.closed")
def handle_grievance_closed(payload: dict, db) -> None:
    if not payload.get("is_residual_risk"):
        return  # only residual-risk closures trigger risk + drill

    # 1. Flag asset in Risk Register (BRS row 4)
    from sheplatform.modules.risk_register.data_service import create_risk
    severity = payload.get("severity", "medium")
    likelihood = {"critical": 5, "high": 4, "medium": 3, "low": 2}.get(severity, 3)
    create_risk(
        db,
        hazard_description=f"Community grievance (residual): {payload.get('case_ref', '')}",
        risk_category="operational",
        likelihood=likelihood,
        impact=likelihood,
        control_effectiveness=3,
        managerial_response="mitigate",
        status="open",
        source_type="grievance",
        source_id=payload["grievance_id"],
        origin_module="SHECCM",
        created_by=payload.get("user_id"),
        org_id=payload.get("org_id"),
    )

    # 2. Mandate a mock drill (BRN-SHE-003, BRS row 5)
    drill_ref = _next_drill_ref(db)
    scheduled = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    db.execute(
        "INSERT INTO mock_drills (drill_ref, drill_type, scheduled_date, status, org_id, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (drill_ref, "community_response", scheduled, "scheduled",
         payload.get("org_id"), payload.get("user_id")),
    )
    db.commit()
