"""SHEER event handlers (guide 21, BRS row 6).

emergency.post_crisis -> EPRP improvement queue (drill_improvements entry).
"""
from __future__ import annotations

from sheplatform.core import events


@events.on("emergency.post_crisis")
def handle_post_crisis(payload: dict, db) -> None:
    db.execute(
        "INSERT INTO drill_improvements (drill_id, description, status) "
        "VALUES (NULL, %s, 'open')",
        (f"EPRP improvement from {payload.get('event_ref', 'emergency')}: "
         f"{payload.get('root_cause', 'post-crisis review')}",))
    db.commit()
