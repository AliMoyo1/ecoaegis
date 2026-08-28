"""Scheduled cleanup for privacy-minimal Mapbox admission evidence."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from sheplatform.modules.map import provider_admission_service

logger = logging.getLogger("sheplatform.scheduler")


def prune_provider_admissions(db_factory) -> int:
    """Retain only the current and prior UTC month of opaque admission rows."""
    db = db_factory()
    try:
        removed = provider_admission_service.prune_old_admissions(db)
        logger.info("map provider admission retention removed %s rows", removed)
        return removed
    except Exception:
        db.rollback()
        logger.exception("map provider admission retention failed")
        raise
    finally:
        db.close()


def start_scheduler(db_factory):
    """Run idempotent admission retention daily at 03:20 UTC."""
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        lambda: prune_provider_admissions(db_factory),
        "cron",
        hour=3,
        minute=20,
        id="map_provider_admission_retention",
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.start()
    return scheduler
