"""Synchronous event bus (guide 5.7). Handlers registered via @on; emit()
inserts to events table, runs handlers, fans out to webhooks. Handler failures
log but never block the source operation.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("sheplatform.events")

_handlers: dict[str, list] = {}


def on(event_type: str):
    def decorator(fn):
        _handlers.setdefault(event_type, []).append(fn)
        return fn
    return decorator


def emit(event_type: str, payload: dict, db, user_id: int | None = None,
         source_module: str = "sheplatform") -> None:
    # 1. Persist event
    try:
        db.execute(
            "INSERT INTO events (event_type, source_module, entity_type, entity_id, payload, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (event_type, source_module,
             payload.get("entity_type"), payload.get("entity_id"),
             json.dumps(payload), user_id),
        )
        db.commit()
    except Exception:
        logger.exception("event persist failed")

    # 2. Run registered in-process handlers
    for fn in _handlers.get(event_type, []):
        try:
            fn(payload, db)
        except Exception:
            logger.exception("event handler %s failed for %s", fn.__name__, event_type)

    # 3. Outbound webhooks (guide 5.7 - fan out)
    try:
        from sheplatform.core.webhooks import deliver_event_webhooks
        deliver_event_webhooks(db, event_type, payload)
    except Exception:
        logger.exception("webhook fan-out failed for %s", event_type)
