"""Tests for messaging channel webhooks (A5)."""
import base64
import hmac
from hashlib import sha1, sha256
from unittest.mock import AsyncMock, patch

import pytest

from sheplatform.config import settings
from sheplatform.core.auth import hash_password
from sheplatform.database import get_db


def _twilio_sig(url: str, params: dict, token: bytes) -> str:
    """The real Twilio signature: base64(HMAC-SHA1(url + sorted k+v))."""
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(token, payload.encode("utf-8"), sha1).digest()).decode()


@pytest.fixture
def channel_env(client, monkeypatch):
    monkeypatch.setattr("sheplatform.config.settings.WHATSAPP_VERIFY_TOKEN", "test-token")
    monkeypatch.setattr("sheplatform.config.settings.WHATSAPP_APP_SECRET", "test-secret")
    monkeypatch.setattr("sheplatform.config.settings.TWILIO_AUTH_TOKEN", "twilio-token")
    db = get_db()
    try:
        db.execute(
            "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id, phone) "
            "VALUES ('field@test.com', %s, 'Field', 'Worker', 'employee', 1, '+263783047375') "
            "ON CONFLICT DO NOTHING",
            (hash_password("password"),))
        db.commit()
    finally:
        db.close()
    return client


def test_whatsapp_verification(channel_env):
    resp = channel_env.get(
        "/channels/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=test-token&hub.challenge=12345")
    assert resp.status_code == 200
    assert resp.text == "12345"


def test_whatsapp_verification_bad_token(channel_env):
    resp = channel_env.get(
        "/channels/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=12345")
    assert resp.status_code == 403


def test_whatsapp_text_message_creates_observation(channel_env):
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "1",
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "263783047375",
                        "type": "text",
                        "text": {"body": "Oil spill near generator."}
                    }]
                }
            }]
        }]
    }
    import json
    body = json.dumps(payload).encode()
    expected = hmac.new(b"test-secret", body, sha256).hexdigest()
    resp = channel_env.post(
        "/channels/whatsapp/webhook",
        content=body,
        headers={"X-Hub-Signature-256": f"sha256={expected}", "Content-Type": "application/json"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"]
    assert len(data["created"]) == 1
    assert data["created"][0]["observation"]["title"] == "Oil spill near generator"


def test_twilio_sms_creates_observation(channel_env):
    url = "http://testserver/channels/twilio/sms"
    params = {"Body": "Machine guard missing", "From": "+263783047375", "MediaUrl0": "http://example.com/photo.jpg"}
    sig = _twilio_sig(url, params, b"twilio-token")
    with patch("sheplatform.modules.channels.routes._download_image") as mock_dl:
        mock_dl.return_value = b"\xff\xd8\xfffake"
        with patch("sheplatform.modules.channels.routes.classify_photo", new=AsyncMock(return_value={
            "title": "Broken guard",
            "obs_type": "hazard",
            "severity": "high",
            "description": "Worker near unguarded machinery.",
            "controls": [],
            "confidence": 0.8,
            "raw_text": "...",
            "data_uri": "data:image/jpeg;base64,xxx",
        })):
            resp = channel_env.post(
                "/channels/twilio/sms",
                data=params,
                headers={"X-Twilio-Signature": sig})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"]
    assert data["observation"]["title"] == "Broken guard"
    assert data["observation"]["severity"] == "high"


def test_twilio_signature_validation(channel_env):
    resp = channel_env.post(
        "/channels/twilio/sms",
        data={"From": "+263783047375", "Body": "Test"},
        headers={"X-Twilio-Signature": "bad"})
    assert resp.status_code == 403


def test_whatsapp_fails_closed_when_secret_unset_in_production(client, monkeypatch):
    """Regression: an unconfigured app secret must REJECT (not accept) an
    unsigned webhook POST in production. Previously the verification was skipped
    entirely when the secret was empty, accepting spoofed reports.
    """
    monkeypatch.setattr("sheplatform.config.settings.WHATSAPP_APP_SECRET", "")
    monkeypatch.setattr("sheplatform.config.settings.DEBUG", False)
    resp = client.post(
        "/channels/whatsapp/webhook",
        content=b'{"entry": []}',
        headers={"Content-Type": "application/json"})
    assert resp.status_code == 503


def test_whatsapp_rejects_bad_signature(channel_env):
    """With a secret configured, a wrong signature is rejected."""
    resp = channel_env.post(
        "/channels/whatsapp/webhook",
        content=b'{"entry": []}',
        headers={"X-Hub-Signature-256": "sha256=deadbeef", "Content-Type": "application/json"})
    assert resp.status_code == 403
