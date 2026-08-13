"""ThemisIQ integration: event bus handlers (spec 6.1).

risk.created / risk.updated -> evaluate threshold -> push (or queue on failure).
"""
from __future__ import annotations

import logging

from sheplatform.core import events
from sheplatform.modules.integration.mapping import is_corporate, is_themis_origin
from sheplatform.modules.integration.themis_sync import push

logger = logging.getLogger("sheplatform.integration")


@events.on("risk.created")
@events.on("risk.updated")
def sync_risk_to_themis(payload: dict, db) -> None:
    risk = payload.get("risk")
    if not risk:
        return
    if not is_corporate(risk):
        return  # below threshold, stays local
    if is_themis_origin(risk):
        return  # do not echo a ThemisIQ-origin risk back (loop guard, spec 8.1)
    try:
        push(risk, db)
    except Exception as e:
        logger.exception("sync handler failed for risk %s: %s", risk.get("id"), e)
