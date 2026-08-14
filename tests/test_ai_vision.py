"""AI client vision tests (guide 3.2)."""
from __future__ import annotations

import pytest

from sheplatform.core import ai_client


def _small_png():
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
        "0000000a4944415408d76360000000020001e221bc330000000049454e44ae426082"
    )


class TestVisionClient:
    @pytest.mark.asyncio
    async def test_vision_unconfigured_returns_graceful(self, monkeypatch):
        monkeypatch.setattr(ai_client.settings, "AI_PROVIDER", "kimi")
        monkeypatch.setattr(ai_client.settings, "KIMI_API_KEY", "")
        monkeypatch.setattr(ai_client.settings, "KIMI_BASE_URL", "")
        result = await ai_client.ask_ai_vision("describe", _small_png(), "image/png")
        assert result == "AI vision not configured."

    @pytest.mark.asyncio
    async def test_openai_vision_request_shape(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(ai_client.settings, "AI_PROVIDER", "gemini")
        monkeypatch.setattr(ai_client.settings, "GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(ai_client.settings, "GEMINI_BASE_URL", "https://gemini.test")
        monkeypatch.setattr(ai_client.settings, "GEMINI_MODEL", "gemini-vision")

        async def fake_post(self, url, *, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            class Resp:
                def raise_for_status(self): pass
                def json(self): return {"choices": [{"message": {"content": " hazard"}}]}
            return Resp()

        import httpx
        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        result = await ai_client.ask_ai_vision("describe", _small_png(), "image/png")
        assert result == "hazard"
        msg = captured["json"]["messages"][1]
        assert msg["role"] == "user"
        assert len(msg["content"]) == 2
        assert msg["content"][0]["type"] == "text"
        assert msg["content"][1]["type"] == "image_url"
        assert msg["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_anthropic_vision_request_shape(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(ai_client.settings, "AI_PROVIDER", "anthropic")
        monkeypatch.setattr(ai_client.settings, "ANTHROPIC_API_KEY", "test-key")

        class FakeMessages:
            async def create(self, *, model, max_tokens, system, messages):
                captured.update({"model": model, "system": system, "messages": messages})
                class Block:
                    type = "text"
                    text = "hazard"
                class Msg:
                    content = [Block()]
                return Msg()

        import anthropic
        monkeypatch.setattr(anthropic.AsyncAnthropic, "messages", FakeMessages())
        result = await ai_client.ask_ai_vision("describe", _small_png(), "image/png")
        assert result == "hazard"
        content = captured["messages"][0]["content"]
        assert content[1]["type"] == "image"
        assert content[1]["source"]["type"] == "base64"
        assert content[1]["source"]["media_type"] == "image/png"
