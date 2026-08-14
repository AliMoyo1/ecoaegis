"""Messaging channel webhooks: WhatsApp Cloud API and Twilio SMS (guide A5).

Incoming text/photo reports are normalised into observations so field staff can
report hazards without logging into the web UI.
"""
from __future__ import annotations

import base64
import hmac
from hashlib import sha1, sha256

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from sheplatform.config import settings
from sheplatform.database import get_db
from sheplatform.modules.observations import data_service as obs_service
from sheplatform.modules.observations.vision import classify_photo

router = APIRouter(prefix="/channels", tags=["channels"])


def _normalise_phone(phone: str) -> str:
    return (phone or "").strip().lstrip("+").replace(" ", "").replace("-", "")


def _find_user_by_phone(db, phone: str):
    norm = _normalise_phone(phone)
    if not norm:
        return None
    # Try exact normalised match, then trailing match.
    for pattern in (norm, f"%{norm}"):
        row = db.execute(
            "SELECT * FROM users WHERE REPLACE(REPLACE(REPLACE(phone, '+', ''), ' ', ''), '-', '') = %s OR phone LIKE %s LIMIT 1",
            (norm, pattern)).fetchone()
        if row:
            return dict(row)
    return None


async def _download_image(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


async def _create_observation_from_message(*, db, phone: str, text: str,
                                          image_url: str | None = None) -> dict:
    user = _find_user_by_phone(db, phone)
    org_id = user["org_id"] if user else settings.SMS_DEFAULT_ORG_ID
    user_id = user["id"] if user else None

    title = (text or "Field report").split(".")[0][:80] or "Field report"
    description = text or "Reported via messaging channel."

    if image_url:
        content = await _download_image(image_url)
        result = await classify_photo(content, filename="photo.jpg")
        title = result.get("title") or title
        description = f"{description}\nAI vision: {result.get('description', '')}".strip()
        obs = obs_service.create_observation(
            db, obs_type=result.get("obs_type", "hazard"), title=title,
            description=description, location="", severity=result.get("severity", "medium"),
            reported_by=user_id, org_id=org_id, photo_path="messaging_photo.jpg",
            ai_metadata={"vision": result, "channel": "messaging"})
        return {"observation": obs, "vision": result}

    obs = obs_service.create_observation(
        db, obs_type="hazard", title=title, description=description, location="",
        severity="medium", reported_by=user_id, org_id=org_id,
        ai_metadata={"channel": "messaging", "raw_text": text})
    return {"observation": obs}


@router.get("/whatsapp/webhook")
async def whatsapp_verify(request: Request):
    """WhatsApp Cloud API subscription verification."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode != "subscribe" or token != settings.WHATSAPP_VERIFY_TOKEN:
        raise HTTPException(status_code=403, detail="verification failed")
    return PlainTextResponse(challenge or "")


@router.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request):
    """Receive WhatsApp messages (text + images) and turn them into observations."""
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    # Fail closed: an unconfigured secret must NOT mean "accept anything". In
    # production a missing secret is a misconfiguration, so reject. In DEBUG
    # (local dev with no Meta app) we allow through so the flow can be exercised.
    if not settings.WHATSAPP_APP_SECRET:
        if not settings.DEBUG:
            raise HTTPException(status_code=503, detail="WhatsApp channel not configured")
    else:
        expected = hmac.new(
            settings.WHATSAPP_APP_SECRET.encode(), body, sha256).hexdigest()
        if not signature.startswith("sha256=") or not hmac.compare_digest(
                signature[7:], expected):
            raise HTTPException(status_code=403, detail="signature mismatch")

    payload = await request.json()
    entries = payload.get("entry", [])
    created = []
    db = get_db()
    try:
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    phone = msg.get("from", "")
                    text = ""
                    image_url = None
                    if msg.get("type") == "text":
                        text = msg.get("text", {}).get("body", "")
                    elif msg.get("type") == "image":
                        image_id = msg.get("image", {}).get("id", "")
                        # WhatsApp Cloud API image URL retrieval would require
                        # a graph API call; for now we classify later if the URL
                        # is provided in a follow-up or via Twilio media.
                        text = msg.get("image", {}).get("caption", "") or "Photo report"
                    created.append(await _create_observation_from_message(
                        db=db, phone=phone, text=text, image_url=image_url))
        return JSONResponse({"ok": True, "created": created})
    finally:
        db.close()


@router.post("/twilio/sms")
async def twilio_sms(
    request: Request,
    From: str = Form(...),  # noqa: N803
    Body: str = Form(""),  # noqa: N803
    MediaUrl0: str = Form(""),  # noqa: N803
):
    """Receive Twilio SMS/MMS and turn them into observations."""
    # Twilio does not send signatures by default for plain HTTP webhooks;
    # validate X-Twilio-Signature if TWILIO_AUTH_TOKEN is set.
    signature = request.headers.get("X-Twilio-Signature", "")
    # Fail closed, same as the WhatsApp webhook: an unset token must not disable
    # verification in production.
    if not settings.TWILIO_AUTH_TOKEN:
        if not settings.DEBUG:
            raise HTTPException(status_code=503, detail="SMS channel not configured")
    else:
        url = str(request.url)
        params = dict(await request.form())
        expected = _twilio_signature(url, params, settings.TWILIO_AUTH_TOKEN)
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=403, detail="signature mismatch")

    db = get_db()
    try:
        result = await _create_observation_from_message(
            db=db, phone=From, text=Body, image_url=MediaUrl0 or None)
        return JSONResponse({"ok": True, **result})
    finally:
        db.close()


def _twilio_signature(url: str, params: dict, auth_token: str) -> str:
    """Compute Twilio's X-Twilio-Signature.

    Twilio signs with HMAC-SHA1 then base64, NOT SHA-256/hex: the signature is
    base64(HMAC-SHA1(auth_token, url + concat(sorted key+value pairs))).
    Note: `url` must be the exact public URL Twilio called; behind a reverse
    proxy honour X-Forwarded-Proto/Host or the signature will not match.
    """
    sorted_params = "".join(f"{k}{params[k]}" for k in sorted(params))
    payload = url + sorted_params
    digest = hmac.new(auth_token.encode(), payload.encode("utf-8"), sha1).digest()
    return base64.b64encode(digest).decode()
