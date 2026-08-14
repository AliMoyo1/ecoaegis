"""Outbound SMS (guide C2: lone-worker escalation).

A5 (modules/channels) only ever built the inbound half - WhatsApp/Twilio
webhooks that turn an incoming message into an observation. Nothing in this
codebase sends a message out until now. Reuses the same Twilio account
already configured for inbound signature verification.

SMS, not WhatsApp, for this specific job: Twilio's WhatsApp Business API
only allows free-form replies within 24h of the user's last message, or a
pre-approved template outside that window - unworkable for an escalation
that can happen at any time. SMS has no such constraint.

This is a best-effort notification channel. The escalation itself (see
modules/lone_worker/scheduler.py) never depends on it succeeding - the
in-app notify_roles() call to the SHE Manager is the guaranteed half.
"""
from __future__ import annotations

import logging

import httpx

from sheplatform.config import settings

logger = logging.getLogger("sheplatform.messaging")


def send_sms(to_phone: str, body: str) -> dict:
    """Best-effort outbound SMS via Twilio. Never raises - a delivery
    failure (or missing config) must not block the caller's escalation
    logic, only degrade the reach of this one channel."""
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_FROM_NUMBER):
        logger.warning("send_sms skipped: Twilio not configured")
        return {"ok": False, "message": "Twilio not configured"}
    if not to_phone:
        return {"ok": False, "message": "no destination phone number"}
    try:
        resp = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json",
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            data={"To": to_phone, "From": settings.TWILIO_FROM_NUMBER, "Body": body},
            timeout=10.0,
        )
        resp.raise_for_status()
        return {"ok": True, "sid": resp.json().get("sid")}
    except Exception as exc:
        logger.warning("send_sms failed: %s", exc)
        return {"ok": False, "message": str(exc)}
