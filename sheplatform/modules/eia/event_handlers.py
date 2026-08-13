"""SHEIA event handlers (guide 21, FNR-SHE-050).

eia.decision 'rejected': notify project team + SHE Manager + CRO, create risk.
"""
from __future__ import annotations

from sheplatform.core import events


@events.on("eia.decision")
def handle_eia_decision(payload: dict, db) -> None:
    if payload.get("decision") != "rejected":
        return

    from sheplatform.core.notifications import notify_roles
    notify_roles(
        db, ["she_manager", "cro"],
        f"EIA REJECTED: {payload['project_ref']}",
        f"Project {payload['project_ref']} was rejected by EMA. Project remains blocked.",
        link=f"/eia/api/projects/{payload['project_id']}",
    )

    from sheplatform.modules.risk_register.data_service import create_risk
    create_risk(
        db,
        hazard_description=f"EIA rejected: {payload['project_ref']}",
        risk_category="regulatory",
        likelihood=4,
        impact=4,
        control_effectiveness=2,
        managerial_response="mitigate",
        status="open",
        source_type="eia",
        source_id=payload["project_id"],
        origin_module="SHEIA",
        created_by=payload.get("user_id"),
        org_id=payload.get("org_id"),
    )
