"""Attachments subsystem tests (guide 3.1)."""
from __future__ import annotations

import io

from sheplatform.core import attachments as attachments_service


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


def _small_png():
    # Minimal valid 1x1 PNG
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452"
        "00000001000000010802000000907753"
        "de0000000a4944415408d76360000000"
        "020001e2!21bc330000000049454e44ae"
        "426082"
        .replace("!", "")
    )


def _small_jpg():
    # Minimal valid JFIF header (not a full image, but enough for magic-byte test)
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"


class TestAttachmentCore:
    def test_save_attachment_writes_row_and_file(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(attachments_service, "ATTACHMENTS_DIR", tmp_path / "attachments")
        user = _mk_user(db, "she_officer", "att1@test.com")
        att = attachments_service.save_attachment(
            db, entity_type="incident", entity_id=1,
            file_bytes=_small_png(), original_name="screenshot.png",
            mime_type="image/png", kind="photo",
            org_id=1, uploaded_by=user["id"])

        assert att["entity_type"] == "incident"
        assert att["entity_id"] == 1
        assert att["mime_type"] == "image/png"
        assert att["kind"] == "photo"
        assert att["sha256"] == attachments_service._sha256(_small_png())
        assert (tmp_path / "attachments" / att["file_name"]).exists()

    def test_oversized_rejected(self, db):
        user = _mk_user(db, "she_officer", "att2@test.com")
        big = b"\xff\xd8\xff" + b"0" * (attachments_service.MAX_BYTES["photo"] + 1)
        try:
            attachments_service.save_attachment(
                db, entity_type="incident", entity_id=1,
                file_bytes=big, original_name="big.jpg", mime_type="image/jpeg",
                kind="photo", org_id=1, uploaded_by=user["id"])
        except ValueError as e:
            assert "exceeds maximum size" in str(e)
        else:
            assert False, "expected ValueError"

    def test_bad_magic_bytes_rejected(self, db):
        user = _mk_user(db, "she_officer", "att3@test.com")
        exe_renamed_jpg = b"MZ\x00\x00" + b"0" * 100
        try:
            attachments_service.save_attachment(
                db, entity_type="incident", entity_id=1,
                file_bytes=exe_renamed_jpg, original_name="evil.jpg",
                mime_type="image/jpeg", kind="photo", org_id=1,
                uploaded_by=user["id"])
        except ValueError as e:
            assert "does not match declared type" in str(e)
        else:
            assert False, "expected ValueError"

    def test_list_is_org_scoped(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(attachments_service, "ATTACHMENTS_DIR", tmp_path / "attachments")
        user = _mk_user(db, "she_officer", "att4@test.com")
        attachments_service.save_attachment(
            db, entity_type="incident", entity_id=1, file_bytes=_small_jpg(),
            original_name="a.jpg", mime_type="image/jpeg", kind="photo",
            org_id=1, uploaded_by=user["id"])

        items = attachments_service.list_attachments(db, "incident", 1, 1)
        assert len(items) == 1
        assert items[0]["org_id"] == 1

        no_org = attachments_service.list_attachments(db, "incident", 1, None)
        assert no_org == []

    def test_get_attachment_org_scoped(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(attachments_service, "ATTACHMENTS_DIR", tmp_path / "attachments")
        user = _mk_user(db, "she_officer", "att5@test.com")
        att = attachments_service.save_attachment(
            db, entity_type="incident", entity_id=1, file_bytes=_small_jpg(),
            original_name="a.jpg", mime_type="image/jpeg", kind="photo",
            org_id=1, uploaded_by=user["id"])

        assert attachments_service.get_attachment(db, att["id"], 1) is not None
        assert attachments_service.get_attachment(db, att["id"], 2) is None
        assert attachments_service.get_attachment(db, att["id"], None) is None

    def test_verify_file_detects_tamper(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(attachments_service, "ATTACHMENTS_DIR", tmp_path / "attachments")
        user = _mk_user(db, "she_officer", "att6@test.com")
        att = attachments_service.save_attachment(
            db, entity_type="incident", entity_id=1, file_bytes=_small_jpg(),
            original_name="a.jpg", mime_type="image/jpeg", kind="photo",
            org_id=1, uploaded_by=user["id"])

        ok, msg = attachments_service.verify_file(db, att["id"], 1)
        assert ok is True and msg == "ok"

        path = attachments_service.ATTACHMENTS_DIR / att["file_name"]
        path.write_bytes(b"TAMPERED")
        ok, msg = attachments_service.verify_file(db, att["id"], 1)
        assert ok is False and msg == "hash mismatch"

    def test_delete_attachment(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(attachments_service, "ATTACHMENTS_DIR", tmp_path / "attachments")
        user = _mk_user(db, "she_officer", "att7@test.com")
        att = attachments_service.save_attachment(
            db, entity_type="incident", entity_id=1, file_bytes=_small_jpg(),
            original_name="a.jpg", mime_type="image/jpeg", kind="photo",
            org_id=1, uploaded_by=user["id"])

        assert attachments_service.delete_attachment(db, att["id"], 1, user["id"]) is True
        assert attachments_service.get_attachment(db, att["id"], 1) is None


def _login(client, email, password="Test1234!") -> str:
    resp = client.post("/login", data={"email": email, "password": password})
    assert resp.status_code in (200, 303), f"login failed: {resp.status_code}"
    return client.cookies.get("she_csrf", "")


class TestAttachmentHTTP:
    def test_upload_and_list(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr(attachments_service, "ATTACHMENTS_DIR", tmp_path / "attachments")
        user = _mk_user(db, "she_officer", "att_http1@test.com")
        token = _login(client, user["email"])

        resp = client.post(
            "/attachments/api/incident/1",
            files={"file": ("photo.png", io.BytesIO(_small_png()), "image/png")},
            data={"kind": "photo"},
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        att = data["attachment"]

        resp = client.get("/attachments/api/incident/1")
        assert resp.status_code == 200
        assert len(resp.json()["attachments"]) == 1

        resp = client.get(f"/attachments/api/serve/{att['id']}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    def test_cross_org_returns_404(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr(attachments_service, "ATTACHMENTS_DIR", tmp_path / "attachments")
        # Ensure org 2 exists so FK does not fail
        db.execute("INSERT INTO organisations (id, name, slug) VALUES (2, 'Other Org', 'other-org') ON CONFLICT DO NOTHING")
        db.commit()

        officer = _mk_user(db, "she_officer", "att_http2@test.com")
        other = _mk_user(db, "she_officer", "att_http3@test.com")
        db.execute("UPDATE users SET org_id = 2 WHERE id = %s", (other["id"],))
        db.commit()

        token = _login(client, officer["email"])
        resp = client.post(
            "/attachments/api/incident/1",
            files={"file": ("photo.png", io.BytesIO(_small_png()), "image/png")},
            headers={"X-CSRF-Token": token},
        )
        att_id = resp.json()["attachment"]["id"]

        token2 = _login(client, other["email"])
        resp = client.get(
            f"/attachments/api/serve/{att_id}",
            headers={"X-CSRF-Token": token2})
        assert resp.status_code == 404

    def test_renamed_exe_rejected(self, client, db, tmp_path, monkeypatch):
        monkeypatch.setattr(attachments_service, "ATTACHMENTS_DIR", tmp_path / "attachments")
        user = _mk_user(db, "she_officer", "att_http4@test.com")
        token = _login(client, user["email"])

        exe = b"MZ\x00\x00" + b"0" * 100
        resp = client.post(
            "/attachments/api/incident/1",
            files={"file": ("photo.jpg", io.BytesIO(exe), "image/jpeg")},
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"] == "file content does not match declared type image/jpeg"
