"""ThemisIQ integration: outbound sync client (spec Section 6, 11.4).

- risk_sync_map: she_risk_id -> themis_risk_id (idempotency, no duplicate creates)
- change hashing: skip PATCH when nothing material changed (spec 8.3)
- themis_sync_queue: retry with exponential backoff when ThemisIQ is unreachable
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sheplatform.config import settings
from sheplatform.modules.integration.mapping import (
    hash_body,
    is_corporate,
    is_themis_origin,
    map_she_to_themis,
)

logger = logging.getLogger("sheplatform.integration")

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 60  # 1min, 2min, 4min, 8min, 16min


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue_sync(db, she_risk_id: int, operation: str) -> None:
    """Queue an outbound sync (used when direct push fails or for later drain)."""
    existing = db.execute(
        "SELECT id FROM themis_sync_queue WHERE she_risk_id = %s AND operation = %s",
        (she_risk_id, operation)).fetchone()
    if existing:
        return  # already queued, do not duplicate
    db.execute(
        "INSERT INTO themis_sync_queue (she_risk_id, operation, attempts, next_retry_at) "
        "VALUES (%s, %s, 0, %s)",
        (she_risk_id, operation, _now()))
    db.commit()


def push(risk: dict, db) -> dict:
    """Push a SHE risk to ThemisIQ. Returns {"ok": bool, "action": ..., ...}.

    Guards (spec 8): origin tag, sync map, change hash.
    """
    if not settings.THEMIS_SYNC_ENABLED:
        return {"ok": False, "action": "disabled", "message": "THEMIS_SYNC_ENABLED is false"}
    if not settings.THEMIS_API_KEY or not settings.THEMIS_API_BASE:
        return {"ok": False, "action": "not_configured", "message": "ThemisIQ credentials missing"}
    if is_themis_origin(risk):
        return {"ok": False, "action": "loop_guard", "message": "ThemisIQ-origin risk, not echoed"}
    if not is_corporate(risk):
        return {"ok": False, "action": "below_threshold", "message": "below corporate materiality bar"}

    body = map_she_to_themis(risk)
    new_hash = hash_body(body)

    existing = db.execute(
        "SELECT themis_risk_id, last_sync_hash FROM risk_sync_map WHERE she_risk_id = %s",
        (risk["id"],)).fetchone()

    import httpx

    try:
        if existing is None:
            resp = httpx.post(
                f"{settings.THEMIS_API_BASE}/api/v1/risks",
                headers={"X-API-Key": settings.THEMIS_API_KEY, "Content-Type": "application/json"},
                json=body, timeout=10.0,
            )
            resp.raise_for_status()
            themis_id = resp.json()["id"]
            db.execute(
                "INSERT INTO risk_sync_map (she_risk_id, themis_risk_id, last_sync_hash, last_synced_at) "
                "VALUES (%s, %s, %s, %s)",
                (risk["id"], themis_id, new_hash, _now()))
            db.commit()
            return {"ok": True, "action": "create", "themis_risk_id": themis_id}
        else:
            if existing["last_sync_hash"] == new_hash:
                return {"ok": True, "action": "noop", "message": "nothing changed since last sync"}
            resp = httpx.patch(
                f"{settings.THEMIS_API_BASE}/api/v1/risks/{existing['themis_risk_id']}",
                headers={"X-API-Key": settings.THEMIS_API_KEY, "Content-Type": "application/json"},
                json=body, timeout=10.0,
            )
            resp.raise_for_status()
            db.execute(
                "UPDATE risk_sync_map SET last_sync_hash = %s, last_synced_at = %s, "
                "sync_error = NULL WHERE she_risk_id = %s",
                (new_hash, _now(), risk["id"]))
            db.commit()
            return {"ok": True, "action": "update", "themis_risk_id": existing["themis_risk_id"]}
    except Exception as e:
        logger.warning("ThemisIQ push failed for risk %s: %s", risk["id"], e)
        enqueue_sync(db, risk["id"], "create" if existing is None else "update")
        return {"ok": False, "action": "queued", "message": str(e)}


def drain_queue(db) -> dict:
    """Scheduler job: retry queued syncs with exponential backoff (spec 11.4)."""
    if not settings.THEMIS_SYNC_ENABLED:
        return {"drained": 0, "message": "sync disabled"}
    now = datetime.now(timezone.utc)
    rows = db.execute(
        "SELECT * FROM themis_sync_queue WHERE next_retry_at IS NULL OR next_retry_at <= %s "
        "ORDER BY id LIMIT 20", (now.isoformat(),)).fetchall()
    drained = 0
    for r in rows:
        item = dict(r)
        risk = db.execute("SELECT * FROM risks WHERE id = %s", (item["she_risk_id"],)).fetchone()
        if risk is None:
            db.execute("DELETE FROM themis_sync_queue WHERE id = %s", (item["id"],))
            db.commit()
            continue
        result = push(dict(risk), db)
        if result.get("ok"):
            db.execute("DELETE FROM themis_sync_queue WHERE id = %s", (item["id"],))
            db.commit()
            drained += 1
        else:
            attempts = item["attempts"] + 1
            if attempts >= MAX_ATTEMPTS:
                db.execute(
                    "UPDATE themis_sync_queue SET attempts = %s, last_error = %s WHERE id = %s",
                    (attempts, result.get("message", "failed"), item["id"]))
                db.commit()
                # alert SHE Manager that corporate sync is degraded (spec 11.4)
                from sheplatform.core.notifications import notify_roles
                notify_roles(db, ["she_manager"],
                             "Corporate risk sync degraded",
                             f"Risk #{item['she_risk_id']} failed to sync to ThemisIQ after "
                             f"{attempts} attempts. Check the integration config.")
            else:
                backoff = BACKOFF_BASE_SECONDS * (2 ** (attempts - 1))
                next_retry = (now + timedelta(seconds=backoff)).isoformat()
                db.execute(
                    "UPDATE themis_sync_queue SET attempts = %s, last_error = %s, next_retry_at = %s "
                    "WHERE id = %s",
                    (attempts, result.get("message", "failed"), next_retry, item["id"]))
                db.commit()
    return {"drained": drained, "remaining": len(rows) - drained}
