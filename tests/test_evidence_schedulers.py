"""Evidence vault + reporting/key-issues scheduler tests."""
from __future__ import annotations


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


class TestEvidence:
    def test_store_and_verify(self, db, tmp_path, monkeypatch):
        # isolate evidence dir per test
        from sheplatform.modules.evidence import data_service
        monkeypatch.setattr(data_service, "EVIDENCE_DIR", tmp_path / "evidence")

        officer = _mk_user(db, "she_officer", "ev1@test.com")
        content = b"incident photo evidence bytes"
        ev = data_service.store_evidence(
            db, entity_type="incident", entity_id=1, original_name="photo.jpg",
            file_bytes=content, mime_type="image/jpeg", uploaded_by=officer["id"])
        assert ev["file_hash"] == data_service._sha256(content)

        ok, msg = data_service.verify_file(db, ev["id"])
        assert ok is True
        assert msg == "ok"

    def test_tamper_detected(self, db, tmp_path, monkeypatch):
        from pathlib import Path
        from sheplatform.modules.evidence import data_service
        monkeypatch.setattr(data_service, "EVIDENCE_DIR", tmp_path / "evidence")

        officer = _mk_user(db, "she_officer", "ev2@test.com")
        ev = data_service.store_evidence(
            db, entity_type="risk", entity_id=2, original_name="scan.pdf",
            file_bytes=b"original", uploaded_by=officer["id"])

        # tamper with the stored file
        Path(ev["file_path"]).write_bytes(b"TAMPERED!!!")
        ok, msg = data_service.verify_file(db, ev["id"])
        assert ok is False
        assert msg == "hash mismatch"


class TestKeyIssuesScheduler:
    def test_aging_and_escalation(self, db):
        officer = _mk_user(db, "she_officer", "ev3@test.com")
        db.execute(
            "INSERT INTO key_issues (title, description, severity, age_days, "
            "escalation_threshold, status, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            ("Old non-conformance", "d", "high", 29, 30, "open", officer["id"]))
        db.commit()

        from sheplatform.modules.reporting.scheduler import age_key_issues
        escalated = age_key_issues(db)
        assert len(escalated) == 1

        row = db.execute("SELECT * FROM key_issues").fetchone()
        assert row["age_days"] == 30
        assert row["escalated"] == 1
        assert row["status"] == "escalated"
