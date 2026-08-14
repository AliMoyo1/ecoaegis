"""C3: document Q&A (FTS retrieval + grounded ask_ai answer with citation).

Data-service/retrieval tests use the plain `db` fixture. The ask_sops HTTP
path is tested through the real route with ask_ai mocked, matching
test_incident_ai_classify.py's convention.
"""
from __future__ import annotations

from unittest.mock import patch

from sheplatform.core.auth import hash_password
from sheplatform.modules.documents import data_service, retrieval


def _mk_user(db, role, email, org_id=1):
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "T", "U", role, org_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
    return dict(row)


def _mk_approved_document(db, org_id, title, description, creator_id):
    doc = data_service.create_document(
        db, title=title, doc_type="sop", description=description,
        created_by=creator_id, org_id=org_id)
    data_service.submit_for_review(db, doc["id"], creator_id)
    return data_service.approve_document(db, doc["id"], creator_id)


class TestRetrieval:
    def test_search_finds_approved_document_by_keyword(self, db):
        officer = _mk_user(db, "she_officer", "dq1@test.com")
        _mk_approved_document(db, 1, "Confined Space Entry Procedure",
                              "Steps for safely entering a confined space", officer["id"])
        results = retrieval.search_documents(db, "confined space entry", org_id=1)
        assert len(results) == 1
        assert results[0]["title"] == "Confined Space Entry Procedure"

    def test_draft_document_not_searchable(self, db):
        officer = _mk_user(db, "she_officer", "dq2@test.com")
        doc = data_service.create_document(
            db, title="Confined Space Draft", doc_type="sop",
            description="not yet approved", created_by=officer["id"], org_id=1)
        # never submitted/approved - not indexed, and search excludes non-approved anyway
        results = retrieval.search_documents(db, "confined space", org_id=1)
        assert results == []

    def test_org_isolation(self, db):
        db.execute("INSERT INTO organisations (name, slug) VALUES ('Other Org', 'other-dq')")
        db.commit()
        other_org = db.execute("SELECT id FROM organisations WHERE slug = 'other-dq'").fetchone()["id"]
        officer = _mk_user(db, "she_officer", "dq3@test.com", org_id=1)
        other_officer = _mk_user(db, "she_officer", "dq3-other@test.com", org_id=other_org)
        _mk_approved_document(db, 1, "Ladder Safety Procedure", "how to use ladders", officer["id"])
        _mk_approved_document(db, other_org, "Ladder Safety Other Org", "a different org's ladder doc",
                              other_officer["id"])
        results = retrieval.search_documents(db, "ladder safety", org_id=1)
        assert len(results) == 1
        assert results[0]["title"] == "Ladder Safety Procedure"


class TestAskSopsHttp:
    def _login(self, client, email):
        client.post("/login", data={"email": email, "password": "Test1234!"})
        return client.cookies.get("she_csrf", "")

    def test_no_match_returns_honest_message_without_calling_ai(self, client):
        """Regression: must not even attempt an AI call with zero grounding
        (guide's own point of failure: 'document Q&A citing the wrong SOP')."""
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "employee", "dq4@test.com")
        finally:
            db.close()
        csrf = self._login(client, "dq4@test.com")
        with patch("sheplatform.modules.documents.data_service.ask_ai") as mock_ai:
            resp = client.post("/documents/api/ask", data={"question": "how do I fly a helicopter"},
                               headers={"X-CSRF-Token": csrf})
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert "No matching" in data["answer"]
            assert data["sources"] == []
            mock_ai.assert_not_called()

    @patch("sheplatform.modules.documents.data_service.ask_ai",
          return_value="Enter the confined space only after atmospheric testing, per [DOC-001].")
    def test_match_returns_grounded_answer_with_source(self, mock_ai, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            officer = _mk_user(db, "employee", "dq5@test.com")
            _mk_approved_document(db, 1, "Confined Space Entry Procedure",
                                  "Test atmosphere before entry, use a permit", officer["id"])
        finally:
            db.close()
        csrf = self._login(client, "dq5@test.com")
        resp = client.post("/documents/api/ask",
                           data={"question": "what is the confined space entry procedure?"},
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert "atmospheric testing" in data["answer"]
        assert len(data["sources"]) == 1
        assert data["sources"][0]["title"] == "Confined Space Entry Procedure"
        mock_ai.assert_called_once()

    def test_ask_org_isolation(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            db.execute("INSERT INTO organisations (name, slug) VALUES ('Other Org3', 'other-dq-3')")
            db.commit()
            other_org = db.execute("SELECT id FROM organisations WHERE slug = 'other-dq-3'").fetchone()["id"]
            other_officer = _mk_user(db, "she_officer", "dq6-other@test.com", org_id=other_org)
            _mk_approved_document(db, other_org, "Forklift Operation Procedure",
                                  "how to operate a forklift safely", other_officer["id"])
            _mk_user(db, "employee", "dq6@test.com", org_id=1)
        finally:
            db.close()
        csrf = self._login(client, "dq6@test.com")
        with patch("sheplatform.modules.documents.data_service.ask_ai") as mock_ai:
            resp = client.post("/documents/api/ask", data={"question": "forklift operation procedure"},
                               headers={"X-CSRF-Token": csrf})
            assert resp.status_code == 200
            data = resp.json()
            assert "No matching" in data["answer"]
            mock_ai.assert_not_called()
