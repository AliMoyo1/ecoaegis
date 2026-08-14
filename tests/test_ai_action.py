"""AI action framework tests (guide 3.3)."""
from __future__ import annotations

import pytest

from sheplatform.modules.ai import service as ai_service


class TestSafeJson:
    def test_clean_object(self):
        assert ai_service._safe_json('{"a": 1}')["a"] == 1

    def test_fenced_object(self):
        text = "```json\n{\"a\": 1}\n```"
        assert ai_service._safe_json(text)["a"] == 1

    def test_garbage_returns_empty(self):
        assert ai_service._safe_json("not json") == {}

    def test_clean_array(self):
        assert ai_service._safe_json_array('[{"a": 1}]')[0]["a"] == 1

    def test_fenced_array(self):
        text = "```json\n[{\"a\": 1}]\n```"
        assert ai_service._safe_json_array(text)[0]["a"] == 1

    def test_garbage_array_returns_empty(self):
        assert ai_service._safe_json_array("not json") == []


@pytest.mark.asyncio
async def test_draft_actions_returns_parsed_json(db, monkeypatch):
    from sheplatform.core.auth import hash_password
    from sheplatform.modules.incidents import data_service as inc_service
    user = {"id": 1, "email": "ai@test.com", "password_hash": hash_password("Test1234!"),
            "first_name": "A", "last_name": "I", "role_key": "she_officer", "org_id": 1}
    db.execute(
        "INSERT INTO users (id, email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (user["id"], user["email"], user["password_hash"], user["first_name"],
         user["last_name"], user["role_key"], user["org_id"]))
    db.commit()

    inc = inc_service.create_incident(
        db, title="Fall from height", description="Worker fell from ladder",
        severity="high", incident_type="accident", occurred_at="2026-08-01T10:00:00+00:00",
        reported_by=user["id"])
    inc_id = inc["id"]
    db.execute("UPDATE incidents SET root_cause = %s WHERE id = %s", ("ladder not secured", inc_id))
    db.commit()

    async def fake_ask_ai(prompt, system=None, max_tokens=2000):
        return ('[{"title": "Secure ladder", "description": "Tie off ladder", '
                '"type": "corrective", "suggested_role": "she_officer", "due_in_days": 7}]')

    monkeypatch.setattr(ai_service, "ask_ai", fake_ask_ai)
    result = await ai_service.draft_corrective_actions(inc_id, org_id=1)
    assert result["ok"] is True
    assert len(result["draft_actions"]) == 1
    assert result["draft_actions"][0]["title"] == "Secure ladder"


@pytest.mark.asyncio
async def test_draft_actions_rejects_cross_org(db):
    from sheplatform.core.auth import hash_password
    from sheplatform.modules.incidents import data_service as inc_service
    db.execute(
        "INSERT INTO users (id, email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (1, "ai@test.com", hash_password("Test1234!"), "A", "I", "she_officer", 1))
    db.commit()
    inc = inc_service.create_incident(
        db, title="Fall", description="d", severity="low", incident_type="near_miss",
        occurred_at="2026-08-01T10:00:00+00:00", reported_by=1)
    result = await ai_service.draft_corrective_actions(inc["id"], org_id=2)
    assert result["ok"] is False
