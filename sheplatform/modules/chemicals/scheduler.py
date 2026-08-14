"""Chemicals scheduler (guide B2): SDS review/expiry alerts."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger("sheplatform.scheduler")


def check_sds_reviews(db) -> list[dict]:
    """Find chemicals whose SDS review date is within 30 days or past."""
    from sheplatform.modules.chemicals.data_service import check_sds_review_dates
    from sheplatform.core.notifications import notify_roles

    rows = check_sds_review_dates(db, horizon_days=30)
    for r in rows:
        status = "expired" if r.get("sds_status") == "expired" else "expiring"
        notify_roles(
            db,
            ["she_officer", "she_manager"],
            f"SDS review {status}: {r['chem_ref']}",
            f"SDS for {r['name']} ({r['chem_ref']}) review date is {r['sds_review_date']}. "
            "Upload a current SDS and update the review date.",
            link=f"/chemicals",
        )
    return rows


def start_scheduler(db_factory):
    scheduler = BackgroundScheduler()

    def job_sds():
        db = db_factory()
        try:
            n = check_sds_reviews(db)
            if n:
                logger.info("sds review alerts: %s", len(n))
        finally:
            db.close()

    scheduler.add_job(job_sds, "cron", hour=7, minute=0, id="sds_review_check")
    scheduler.start()
    return scheduler
