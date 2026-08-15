"""Incidents scheduler (guide 22): statutory deadline checks.

- BRN-SHE-002: alert SHE Officer + CRO on critical incidents approaching the 48h NSSA deadline.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger("sheplatform.scheduler")


def check_statutory_deadlines(db) -> list[dict]:
    """Find critical incidents within 12h of their 48h deadline, not yet nssa_notified."""
    now = datetime.now(timezone.utc)
    warning_window = (now + timedelta(hours=12)).isoformat()
    rows = db.execute(
        "SELECT * FROM incidents WHERE severity = 'critical' "
        "AND statutory_deadline IS NOT NULL AND statutory_deadline <= %s "
        "AND statutory_deadline > %s AND nssa_notified = FALSE",
        (warning_window, now.isoformat()),
    ).fetchall()
    alerts = []
    for r in rows:
        from sheplatform.core.notifications import notify_roles
        incident = dict(r)
        notify_roles(
            db,
            ["she_officer", "cro"],
            f"NSSA deadline approaching: {incident['incident_ref']}",
            f"Incident {incident['incident_ref']} ({incident['title']}) must be "
            f"notified to NSSA by {incident['statutory_deadline']}.",
            link=f"/incidents/api/{incident['id']}",
        )
        alerts.append(incident)
    return alerts


def check_overdue_deadlines(db) -> list[dict]:
    """Critical incidents PAST the deadline and still not notified (escalate to CRO + COO)."""
    now = datetime.now(timezone.utc).isoformat()
    rows = db.execute(
        "SELECT * FROM incidents WHERE severity = 'critical' "
        "AND statutory_deadline IS NOT NULL AND statutory_deadline < %s AND nssa_notified = FALSE",
        (now,),
    ).fetchall()
    alerts = []
    for r in rows:
        from sheplatform.core.notifications import notify_roles
        incident = dict(r)
        notify_roles(
            db,
            ["cro", "coo"],
            f"OVERDUE NSSA notification: {incident['incident_ref']}",
            f"Incident {incident['incident_ref']} passed its statutory deadline "
            f"({incident['statutory_deadline']}) without NSSA notification.",
            link=f"/incidents/api/{incident['id']}",
        )
        alerts.append(incident)
    return alerts


def start_scheduler(db_factory):
    scheduler = BackgroundScheduler()

    def job_statutory():
        db = db_factory()
        try:
            n = check_statutory_deadlines(db)
            if n:
                logger.info("statutory deadline alerts: %s", len(n))
        finally:
            db.close()

    def job_overdue():
        db = db_factory()
        try:
            n = check_overdue_deadlines(db)
            if n:
                logger.info("overdue deadline alerts: %s", len(n))
        finally:
            db.close()

    scheduler.add_job(job_statutory, "interval", minutes=15, id="statutory_deadline_check")
    scheduler.add_job(job_overdue, "interval", minutes=30, id="overdue_deadline_check")
    scheduler.start()
    return scheduler
