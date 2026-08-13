"""SHER event handlers (guide 21, BRS rows 7 + 9).

report.approved:
- Row 7: report gaps -> training needs
- Row 9: Board recommendations -> workplan actions (stub: logged for Phase 2 workplan)
"""
from __future__ import annotations

from sheplatform.core import events


@events.on("report.approved")
def handle_report_approved(payload: dict, db) -> None:
    report_type = payload.get("report_type", "")
    # Row 7: create a training need placeholder for approved reports with gaps
    if report_type in ("board", "annual_sustainability", "monthly_management"):
        db.execute(
            "INSERT INTO training_needs (need_ref, title, description, source_trigger, "
            "source_id, delivery_method, status, org_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (f"TN-{payload['report_id']:05d}",
             f"Training review from {payload.get('report_ref', 'report')}",
             "Review report gaps and schedule required training.",
             "audit", payload["report_id"], "pending", "identified",
             payload.get("org_id")))
        db.commit()
