"""Document control tests: lifecycle, versioning, acknowledgement."""
from __future__ import annotations

import pytest


def _mk_user(db, role, email):
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key) "
        "VALUES (%s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "F", "L", role),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


class TestDocumentLifecycle:
    def test_full_lifecycle(self, db):
        officer = _mk_user(db, "she_officer", "doc1@test.com")
        manager = _mk_user(db, "she_manager", "doc2@test.com")

        from sheplatform.modules.documents.data_service import (
            create_document, submit_for_review, approve_document)
        doc = create_document(db, title="Working at Height SOP", doc_type="sop",
                              description="Ladder and scaffold rules", version="1.0",
                              created_by=officer["id"])
        assert doc["status"] == "draft"
        assert doc["doc_ref"].startswith("DOC-")

        doc = submit_for_review(db, doc["id"], officer["id"])
        assert doc["status"] == "in_review"

        doc = approve_document(db, doc["id"], manager["id"])
        assert doc["status"] == "approved"
        assert doc["approved_by"] == manager["id"]

    def test_approve_requires_review(self, db):
        officer = _mk_user(db, "she_officer", "doc3@test.com")
        manager = _mk_user(db, "she_manager", "doc4@test.com")
        from sheplatform.modules.documents.data_service import create_document, approve_document
        doc = create_document(db, title="X", doc_type="policy", created_by=officer["id"])
        with pytest.raises(ValueError, match="must be 'in_review'"):
            approve_document(db, doc["id"], manager["id"])

    def test_version_supersedes(self, db):
        officer = _mk_user(db, "she_officer", "doc5@test.com")
        from sheplatform.modules.documents.data_service import create_document, list_documents
        v1 = create_document(db, title="Evacuation Plan", doc_type="policy",
                             version="1.0", created_by=officer["id"])
        v2 = create_document(db, title="Evacuation Plan", doc_type="policy",
                             version="2.0", supersedes=v1["id"], created_by=officer["id"])
        assert v2["supersedes"] == v1["id"]
        # v1 now superseded
        docs = list_documents(db)
        v1_row = next(d for d in docs if d["id"] == v1["id"])
        assert v1_row["status"] == "superseded"


class TestAcknowledgement:
    def test_ack_and_unacknowledged(self, db):
        officer = _mk_user(db, "she_officer", "doc6@test.com")
        manager = _mk_user(db, "she_manager", "doc7@test.com")
        emp = _mk_user(db, "employee", "doc8@test.com")

        from sheplatform.modules.documents.data_service import (
            create_document, submit_for_review, approve_document, acknowledge_document,
            unacknowledged_users)
        doc = create_document(db, title="PPE Policy", doc_type="policy", created_by=officer["id"])
        submit_for_review(db, doc["id"], officer["id"])
        approve_document(db, doc["id"], manager["id"])

        # emp acknowledges
        acknowledge_document(db, doc["id"], emp["id"])

        # everyone except emp should be unacknowledged (officer + manager)
        unacked = unacknowledged_users(db, doc["id"])
        unacked_ids = {u["id"] for u in unacked}
        assert emp["id"] not in unacked_ids
        assert officer["id"] in unacked_ids

    def test_ack_only_approved(self, db):
        officer = _mk_user(db, "she_officer", "doc9@test.com")
        emp = _mk_user(db, "employee", "doc10@test.com")
        from sheplatform.modules.documents.data_service import create_document, acknowledge_document
        doc = create_document(db, title="Draft doc", doc_type="sop", created_by=officer["id"])
        with pytest.raises(ValueError, match="must be 'approved'"):
            acknowledge_document(db, doc["id"], emp["id"])
