"""Reporting + Key Issues schedulers (guide 22).

- Report deadline check (daily 08:00) - BRN-012
- Key Issues aging (daily 09:00) - Section 3.2
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger("sheplatform.scheduler")


def check_report_deadlines(db) -> list[dict]:
    from sheplatform.modules.reporting.data_service import check_overdue_reports
    return check_overdue_reports(db)


def age_key_issues(db) -> list[dict]:
    """Increment age_days on open issues; escalate past threshold."""
    from sheplatform.core.notifications import notify_roles

    escalated = []
    rows = db.execute(
        "SELECT * FROM key_issues WHERE status IN ('open','in_progress')").fetchall()
    for r in rows:
        issue = dict(r)
        new_age = (issue.get("age_days") or 0) + 1
        should_escalate = (not issue.get("escalated")
                           and new_age >= (issue.get("escalation_threshold") or 30))
        db.execute(
            "UPDATE key_issues SET age_days = %s, escalated = %s, status = CASE WHEN %s "
            "THEN 'escalated' ELSE status END WHERE id = %s",
            (new_age, should_escalate, should_escalate, issue["id"]))
        if should_escalate:
            notify_roles(db, ["she_manager", "cro"],
                         f"Key issue escalated: {issue['title']}",
                         f"Issue aged {new_age} days past threshold {issue.get('escalation_threshold')}.")
            escalated.append(issue)
    if rows:
        db.commit()
    return escalated


def start_scheduler(db_factory):
    scheduler = BackgroundScheduler()

    def job_reports():
        db = db_factory()
        try:
            n = check_report_deadlines(db)
            if n:
                logger.info("report deadline alerts: %s", len(n))
        finally:
            db.close()

    def job_key_issues():
        db = db_factory()
        try:
            n = age_key_issues(db)
            if n:
                logger.info("key issues escalated: %s", len(n))
        finally:
            db.close()

    scheduler.add_job(job_reports, "cron", hour=8, minute=0, id="report_deadline_check")
    scheduler.add_job(job_key_issues, "cron", hour=9, minute=0, id="key_issues_aging")
    scheduler.start()
    return scheduler
