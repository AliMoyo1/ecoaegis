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


class TestLoginRateLimit:
    """Audit S4 fix: login had zero rate limiting or lockout."""

    def test_under_threshold_not_limited(self, db):
        for _ in range(4):
            auth.record_failed_login(db, "1.1.1.1")
        assert auth.is_login_rate_limited(db, "1.1.1.1") is False

    def test_at_threshold_limited(self, db):
        for _ in range(5):
            auth.record_failed_login(db, "2.2.2.2")
        assert auth.is_login_rate_limited(db, "2.2.2.2") is True

    def test_identifiers_are_independent(self, db):
        for _ in range(5):
            auth.record_failed_login(db, "3.3.3.3")
        assert auth.is_login_rate_limited(db, "3.3.3.3") is True
        assert auth.is_login_rate_limited(db, "4.4.4.4") is False

    def test_window_expiry_clears_the_limit(self, db):
        from datetime import datetime, timedelta, timezone
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        for _ in range(5):
            db.execute("INSERT INTO login_attempts (identifier, created_at) VALUES (%s, %s)",
                      ("5.5.5.5", old))
        db.commit()
        # all 5 attempts are outside the 5-minute window -> not limited
        assert auth.is_login_rate_limited(db, "5.5.5.5") is False

    def test_no_identifier_never_limited(self, db):
        assert auth.is_login_rate_limited(db, "") is False


class TestLoginRateLimitHTTP:
    """HTTP-level: the actual /login route, not just the auth.py functions."""

    def _mk_user(self, db, email="rl@test.com", password="Test1234!"):
        from sheplatform.core.auth import hash_password
        db.execute(
            "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (email, hash_password(password), "R", "L", "employee", 1),
        )
        db.commit()

    def test_sixth_attempt_blocked_even_with_correct_password(self, db):
        from fastapi.testclient import TestClient
        from sheplatform.main import app
        self._mk_user(db)
        client = TestClient(app)

        for _ in range(5):
            resp = client.post("/login", data={"email": "rl@test.com", "password": "wrong"})
            assert resp.status_code == 200  # re-rendered login page with an error

        # 6th attempt, even with the CORRECT password, must be blocked
        resp = client.post("/login", data={"email": "rl@test.com", "password": "Test1234!"})
        assert resp.status_code == 429
        assert "Too many" in resp.text

    def test_login_succeeds_before_threshold(self, db):
        from fastapi.testclient import TestClient
        from sheplatform.main import app
        self._mk_user(db, email="rl2@test.com")
        client = TestClient(app)

        for _ in range(3):
            client.post("/login", data={"email": "rl2@test.com", "password": "wrong"})

        resp = client.post("/login", data={"email": "rl2@test.com", "password": "Test1234!"},
                           follow_redirects=False)
        assert resp.status_code == 303
        assert "she_session" in resp.cookies
