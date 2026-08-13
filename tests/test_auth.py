"""Auth tests (guide 5.3 success criteria + 26)."""
from __future__ import annotations

from sheplatform.core import auth


class TestPassword:
    def test_hash_starts_with_bcrypt_12(self):
        h = auth.hash_password("Test1234!")
        assert h.startswith("$2b$12$")

    def test_verify_correct(self):
        h = auth.hash_password("Test1234!")
        assert auth.verify_password("Test1234!", h)

    def test_verify_wrong(self):
        h = auth.hash_password("Test1234!")
        assert not auth.verify_password("WrongPass1!", h)

    def test_verify_garbage_hash(self):
        assert not auth.verify_password("x", "not-a-bcrypt-hash")


class TestSessions:
    def test_create_and_get(self, db):
        from sheplatform.core.auth import hash_password
        db.execute(
            "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("sess@test.com", hash_password("Test1234!"), "S", "User", "employee", 1),
        )
        db.commit()
        user_id = db.execute("SELECT id FROM users WHERE email = 'sess@test.com'").fetchone()["id"]

        raw = auth.create_session(db, user_id)
        assert len(raw) == 64  # token_urlsafe(48) -> 64 chars

        user = auth.get_session_user(db, raw)
        assert user is not None
        assert user["id"] == user_id
        assert user["role_key"] == "employee"

    def test_expired_session_rejected(self, db, monkeypatch):
        from datetime import datetime, timedelta, timezone
        from sheplatform.core.auth import hash_password
        db.execute(
            "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("exp@test.com", hash_password("Test1234!"), "E", "User", "employee", 1),
        )
        db.commit()
        user_id = db.execute("SELECT id FROM users WHERE email = 'exp@test.com'").fetchone()["id"]

        import hashlib, secrets
        raw = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        db.execute(
            "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
            (user_id, token_hash, past),
        )
        db.commit()

        assert auth.get_session_user(db, raw) is None

    def test_destroy_session(self, db):
        from sheplatform.core.auth import hash_password
        db.execute(
            "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("kill@test.com", hash_password("Test1234!"), "K", "User", "employee", 1),
        )
        db.commit()
        user_id = db.execute("SELECT id FROM users WHERE email = 'kill@test.com'").fetchone()["id"]
        raw = auth.create_session(db, user_id)
        auth.destroy_session(db, raw)
        assert auth.get_session_user(db, raw) is None
