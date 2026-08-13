"""ThemisIQ integration: inbound webhook receiver (spec Section 7).

Verifies X-ThemisIQ-Signature (HMAC-SHA256, constant-time compare).
Rejects unsigned/wrong-signed payloads with 401. Loop guard on SHE-origin echoes.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from sheplatform.config import settings
from sheplatform.core.audit import log_audit
from sheplatform.database import get_db

logger = logging.getLogger("sheplatform.integration")

router = APIRouter(prefix="/integrations/themisiq", tags=["integration"])


def _verify_signature(raw: bytes, header_value: str) -> bool:
    """Constant-time HMAC-SHA256 compare (spec 10)."""
    if not settings.THEMIS_WEBHOOK_SECRET:
        return False
    expected = "sha256=" + hmac.new(
        settings.THEMIS_WEBHOOK_SECRET.encode(), raw, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(header_value or "", expected)


def _handle_themis_event(event: dict, db) -> None:
    """Process a verified ThemisIQ event (spec 7.3)."""
    from sheplatform.core.notifications import notify_roles

    etype = event.get("event_type", "")
    data = event.get("data", {}) or {}

    # Loop guard (spec 8.1): ignore SHE-origin risks echoed back
    if data.get("source_module") == "SHE":
        logger.info("ignored echoed SHE-origin event: %s", etype)
        return

    source_entity_id = event.get("source_entity_id")

    if etype == "erm.risk.escalated":
        notify_roles(
            db, ["she_manager", "cro"],
            "Corporate risk escalated in ThemisIQ",
            f"Enterprise risk #{source_entity_id} crossed an escalation threshold.",
            link=f"/risks?themis_id={source_entity_id}",
        )
    elif etype == "erm.appetite.breached":
        notify_roles(
            db, ["she_manager", "cro"],
            "Risk appetite breached",
            "ThemisIQ reports a corporate risk appetite breach. CRO review required.",
        )
    elif etype in ("erm.risk.updated", "erm.risk.closed"):
        # keep optional read-only mirror in step; no human alert (spec 7.4)
        if data.get("mirror_she_risk_id"):
            status = "mitigated" if etype == "erm.risk.closed" else None
            if status:
                db.execute(
                    "UPDATE risks SET status = %s WHERE id = %s AND origin_system = 'themisiq'",
                    (status, data["mirror_she_risk_id"]))
                db.commit()


@router.post("/webhook")
async def themis_webhook(request: Request):
    raw = await request.body()
    sig = request.headers.get("X-ThemisIQ-Signature", "")

    if not _verify_signature(raw, sig):
        logger.warning("rejected webhook: bad signature (len=%s)", len(sig))
        raise HTTPException(status_code=401, detail="bad signature")

    try:
        event = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    db = get_db()
    try:
        _handle_themis_event(event, db)
        log_audit(db, None, event.get("organisation_id"),
                  "integration.webhook.received", "integration", 0,
                  new_value={"event_type": event.get("event_type"),
                             "source_entity_id": event.get("source_entity_id")})
        return JSONResponse({"ok": True})  # 2xx stops ThemisIQ retrying
    finally:
        db.close()
