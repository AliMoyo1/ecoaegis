"""MFA tests (audit fix: pyotp/qrcode were installed but never used)."""
from __future__ import annotations

import pytest


def _mk_user(db, role, email):
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "F", "L", role, 1),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


class TestMFAEnrollment:
    def test_enroll_generates_secret_and_backup_codes(self, db):
        user = _mk_user(db, "she_officer", "mfa1@test.com")
        from sheplatform.core import mfa
        out = mfa.enroll(db, user["id"], user["email"])

        assert len(out["secret"]) == 32  # base32 TOTP secret
        assert out["uri"].startswith("otpauth://totp/")
        assert len(out["backup_codes"]) == 10

        row = db.execute("SELECT mfa_secret, mfa_enabled FROM users WHERE id = %s",
                         (user["id"],)).fetchone()
        assert row["mfa_secret"] == out["secret"]
        assert bool(row["mfa_enabled"]) is False  # not enabled until confirmed

        codes = db.execute("SELECT code_hash FROM mfa_backup_codes WHERE user_id = %s",
                           (user["id"],)).fetchall()
        assert len(codes) == 10

    def test_confirm_enroll_requires_valid_code(self, db):
        user = _mk_user(db, "she_officer", "mfa2@test.com")
        from sheplatform.core import mfa
        out = mfa.enroll(db, user["id"], user["email"])

        # wrong code rejected
        r = mfa.confirm_enroll(db, user["id"], "000000")
        assert r["ok"] is False

        # correct TOTP code enables
        import pyotp
        code = pyotp.TOTP(out["secret"]).now()
        r = mfa.confirm_enroll(db, user["id"], code)
        assert r["ok"] is True
        row = db.execute("SELECT mfa_enabled FROM users WHERE id = %s",
                         (user["id"],)).fetchone()
        assert bool(row["mfa_enabled"]) is True


class TestMFAVerification:
    def test_totp_verify(self, db):
        user = _mk_user(db, "she_officer", "mfa3@test.com")
        from sheplatform.core import mfa
        out = mfa.enroll(db, user["id"], user["email"])
        mfa.confirm_enroll(db, user["id"], __import__("pyotp").TOTP(out["secret"]).now())

        import pyotp
        r = mfa.verify(db, user["id"], pyotp.TOTP(out["secret"]).now())
        assert r["ok"] is True

    def test_backup_code_single_use(self, db):
        user = _mk_user(db, "she_officer", "mfa4@test.com")
        from sheplatform.core import mfa
        out = mfa.enroll(db, user["id"], user["email"])
        mfa.confirm_enroll(db, user["id"], __import__("pyotp").TOTP(out["secret"]).now())

        code = out["backup_codes"][0]
        r = mfa.verify(db, user["id"], code)
        assert r["ok"] is True
        assert r.get("used_backup") is True

        # second use fails (single-use)
        r = mfa.verify(db, user["id"], code)
        assert r["ok"] is False

    def test_wrong_code_rejected(self, db):
        user = _mk_user(db, "she_officer", "mfa5@test.com")
        from sheplatform.core import mfa
        out = mfa.enroll(db, user["id"], user["email"])
        mfa.confirm_enroll(db, user["id"], __import__("pyotp").TOTP(out["secret"]).now())
        r = mfa.verify(db, user["id"], "999999")
        assert r["ok"] is False


class TestMFASession:
    def test_verify_session_marks_session(self, db):
        user = _mk_user(db, "she_officer", "mfa6@test.com")
        from sheplatform.core import auth, mfa
        raw = auth.create_session(db, user["id"])

        r = mfa.verify_session(db, user["id"], raw)
        assert r["ok"] is True

        # session user now shows mfa_verified
        session_user = auth.get_session_user(db, raw)
        assert bool(session_user["mfa_verified"]) is True

    def test_unverified_session_flagged(self, db):
        user = _mk_user(db, "she_officer", "mfa7@test.com")
        from sheplatform.core import auth
        raw = auth.create_session(db, user["id"])
        session_user = auth.get_session_user(db, raw)
        assert bool(session_user["mfa_verified"]) is False


class TestMFAStatus:
    def test_user_mfa_status(self, db):
        user = _mk_user(db, "she_officer", "mfa8@test.com")
        from sheplatform.core import mfa
        status = mfa.user_mfa_status(db, user["id"])
        assert status["enabled"] is False

        out = mfa.enroll(db, user["id"], user["email"])
        mfa.confirm_enroll(db, user["id"], __import__("pyotp").TOTP(out["secret"]).now())
        status = mfa.user_mfa_status(db, user["id"])
        assert status["enabled"] is True
        assert status["has_secret"] is True
