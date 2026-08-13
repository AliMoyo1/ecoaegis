"""Outbound webhook delivery (guide 24 webhook pattern, ThemisIQ-proven).

Signed HMAC-SHA256 POSTs, 3 retries with exponential backoff, delivery log.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("sheplatform.webhooks")


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _log_delivery(db, url: str, payload: dict, code: int, success: bool) -> None:
    try:
        db.execute(
            "INSERT INTO webhook_logs (url, payload, status_code, success, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (url, json.dumps(payload), code, success, datetime.now(timezone.utc).isoformat()),
        )
        db.commit()
    except Exception:
        logger.exception("webhook log write failed")


def deliver_event_webhooks(db, event_type: str, payload: dict) -> None:
    """Fan out an emitted event to all active webhook subscriptions."""
    try:
        rows = db.execute(
            "SELECT id, url, secret, event_type FROM webhooks WHERE active = 1 AND event_type = %s",
            (event_type,),
        ).fetchall()
    except Exception:
        logger.exception("webhook subscription lookup failed")
        return
    for row in rows:
        asyncio.create_task(_deliver(row["url"], payload, row["secret"]))


async def _deliver(url: str, payload: dict, secret: str, db=None) -> None:
    body = json.dumps(payload).encode()
    signature = _sign(body, secret)
    backoff = [1, 2, 4]
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url, content=body,
                    headers={"Content-Type": "application/json", "X-SHE-Signature": signature},
                )
            if resp.status_code == 200 or 300 <= resp.status_code < 400:
                return  # success
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                return  # permanent failure, no retry
        except Exception:
            logger.exception("webhook attempt %s failed to %s", attempt + 1, url)
        await asyncio.sleep(backoff[min(attempt, 2)])
