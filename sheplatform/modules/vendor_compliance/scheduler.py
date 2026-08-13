"""Vendor Compliance + PTW scheduler (guide 22): certification expiry checks."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger("sheplatform.scheduler")


def check_certifications(db) -> list[dict]:
    from sheplatform.modules.vendor_compliance.data_service import check_certification_expiry
    return check_certification_expiry(db)


def start_scheduler(db_factory):
    scheduler = BackgroundScheduler()

    def job_certs():
        db = db_factory()
        try:
            n = check_certifications(db)
            if n:
                logger.info("certification alerts: %s", len(n))
        finally:
            db.close()

    scheduler.add_job(job_certs, "cron", hour=6, minute=0, id="cert_expiry_check")
    scheduler.start()
    return scheduler
