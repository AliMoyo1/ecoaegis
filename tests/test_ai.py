"""AI feature tests (guide 23). AI call is mocked - no network.

Verifies: grounding (never calls AI without data), graceful unconfigured
behavior, feature flows, error paths.
"""
from __future__ import annotations

import pytest


def _mk_user(db, role, email):
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "T", "U", role, 1),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
    return dict(row)


def _mk_incident(db, by_user, title="Spill", incident_type="environmental", severity="high"):
    from sheplatform.modules.incidents.data_service import create_incident
    return create_incident(
        db, title=title, description="Chemical spill in warehouse",
        severity=severity, incident_type=incident_type,
        occurred_at="2026-08-01T10:00:00+00:00", reported_by=by_user)


@pytest.fixture
def mock_ai(monkeypatch):
    """Replace ask_ai with a recorder that returns a canned reply."""
    calls = []

    async def fake_ask(prompt, system=None, max_tokens=2000):
        calls.append(prompt)
        return "GROUNDED RESPONSE"

    monkeypatch.setattr("sheplatform.modules.ai.service.ask_ai", fake_ask)
    return calls


class TestProviderResolution:
    def test_kimi_default(self):
        from sheplatform.core.ai_client import provider_info
        info = provider_info()
        assert info["provider"] == "kimi"  # default from .env
        assert info["model"] == "Kimi-K2.7-Code"
        assert "configured" in info

    def test_gemini_unconfigured(self, monkeypatch):
        monkeypatch.setattr("sheplatform.config.settings.AI_PROVIDER", "gemini")
        monkeypatch.setattr("sheplatform.config.settings.GEMINI_API_KEY", "")
        from sheplatform.core.ai_client import provider_info
        info = provider_info()
        assert info["provider"] == "gemini"
        assert info["configured"] is False

    def test_deepseek_resolution(self, monkeypatch):
        monkeypatch.setattr("sheplatform.config.settings.AI_PROVIDER", "deepseek")
        monkeypatch.setattr("sheplatform.config.settings.DEEPSEEK_API_KEY", "sk-test")
        from sheplatform.core.ai_client import _endpoint_and_key
        base, key, model = _endpoint_and_key()
        assert "api.deepseek.com" in base
        assert key == "sk-test"
        assert model == "deepseek-v4-flash"


@pytest.mark.asyncio
class TestIncidentCopilot:
    async def test_grounded_with_similar_incidents(self, db, mock_ai):
        officer = _mk_user(db, "she_officer", "ai1@test.com")
        i1 = _mk_incident(db, officer["id"], title="Spill A")
        i2 = _mk_incident(db, officer["id"], title="Spill B")

        result = await __import__("sheplatform.modules.ai.service", fromlist=["x"]).incident_copilot(i1["id"])
        assert result["ok"] is True
        assert result["incident_ref"] == i1["incident_ref"]
        assert len(mock_ai) == 1
        # grounding: the prompt must include real incident data
        assert "Spill A" in mock_ai[0]
        assert "Spill B" in mock_ai[0]

    async def test_missing_incident_no_ai_call(self, db, mock_ai):
        result = await __import__("sheplatform.modules.ai.service", fromlist=["x"]).incident_copilot(99999)
        assert result["ok"] is False
        assert mock_ai == []  # never call AI for a missing record


@pytest.mark.asyncio
class TestUnconfigured:
    async def test_graceful_message(self, db, monkeypatch):
        # no provider key configured -> graceful message, not an exception
        monkeypatch.setattr("sheplatform.config.settings.AI_PROVIDER", "gemini")
        monkeypatch.setattr("sheplatform.config.settings.GEMINI_API_KEY", "")
        officer = _mk_user(db, "she_officer", "ai2@test.com")
        inc = _mk_incident(db, officer["id"])

        result = await __import__("sheplatform.modules.ai.service", fromlist=["x"]).root_cause_assistant(inc["id"])
        assert result["ok"] is True
        assert "not configured" in result["result"].lower()


@pytest.mark.asyncio
class TestOtherFeatures:
    async def test_chat_grounded(self, db, mock_ai):
        officer = _mk_user(db, "she_officer", "ai3@test.com")
        _mk_incident(db, officer["id"], title="Chat incident")

        result = await __import__("sheplatform.modules.ai.service", fromlist=["x"]).chat("What incidents are open?")
        assert result["ok"] is True
        assert "Chat incident" in mock_ai[0]  # data snapshot included in prompt

    async def test_daily_briefing(self, db, mock_ai):
        officer = _mk_user(db, "she_officer", "ai4@test.com")
        _mk_incident(db, officer["id"], title="Briefing incident")

        result = await __import__("sheplatform.modules.ai.service", fromlist=["x"]).daily_briefing()
        assert result["ok"] is True
        assert mock_ai  # called with snapshot
