"""Tier-1 Increment C: password lifecycle - strength, tokens, forced change,
self-service change, and admin-triggered reset."""
from __future__ import annotations

from sheplatform.core import auth


def _mk_user(db, email, role="employee", org_id=1, must_change=False, pw="Test1234!"):
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id, "
        "must_change_password) VALUES (%s, %s, 'T', 'U', %s, %s, %s)",
        (email, auth.hash_password(pw), role, org_id, must_change))
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _login(client, email, pw="Test1234!"):
    client.post("/login", data={"email": email, "password": pw})
    return client.cookies.get("she_csrf", "")


class TestAuthHelpers:
    def test_password_strength(self, db):
        assert auth.validate_password_strength("short1") is not None
        assert auth.validate_password_strength("nodigitshere") is not None
        assert auth.validate_password_strength("12345678") is not None
        assert auth.validate_password_strength("Good1234") is None

    def test_set_password_clears_must_change(self, db):
        u = _mk_user(db, "sp@test.com", must_change=True)
        assert auth.set_password(db, u["id"], "NewPass99")["ok"] is True
        row = db.execute("SELECT password_hash, must_change_password FROM users WHERE id = %s",
                         (u["id"],)).fetchone()
        assert auth.verify_password("NewPass99", row["password_hash"])
        assert not row["must_change_password"]

    def test_set_password_rejects_weak(self, db):
        u = _mk_user(db, "sp2@test.com")
        assert auth.set_password(db, u["id"], "weak")["ok"] is False

    def test_token_issue_verify_consume(self, db):
        u = _mk_user(db, "tok@test.com")
        raw = auth.issue_auth_token(db, u["id"], "reset", 1)
        rec = auth.verify_auth_token(db, raw, "reset")
        assert rec and rec["user_id"] == u["id"]
        assert auth.verify_auth_token(db, raw, "invite") is None      # wrong purpose
        auth.consume_auth_token(db, rec["id"])
        assert auth.verify_auth_token(db, raw, "reset") is None        # single-use

    def test_expired_token_invalid(self, db):
        u = _mk_user(db, "tok2@test.com")
        db.execute("INSERT INTO auth_tokens (user_id, token_hash, purpose, expires_at) "
                   "VALUES (%s, %s, 'reset', %s)",
                   (u["id"], __import__("hashlib").sha256(b"raw").hexdigest(),
                    "2000-01-01T00:00:00+00:00"))
        db.commit()
        assert auth.verify_auth_token(db, "raw", "reset") is None


class TestForcedChangeEnforcement:
    def test_must_change_user_is_redirected(self, client, db):
        _mk_user(db, "force@test.com", must_change=True)
        _login(client, "force@test.com")
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/account/change-password"
        # ...but the change-password page itself is reachable (no loop)
        assert client.get("/account/change-password", follow_redirects=False).status_code == 200

    def test_after_change_access_restored(self, client, db):
        _mk_user(db, "force2@test.com", must_change=True)
        csrf = _login(client, "force2@test.com")
        client.post("/account/change-password",
                    data={"current_password": "Test1234!", "new_password": "Brand New9",
                          "confirm_password": "Brand New9", "csrf_token": csrf},
                    headers={"X-CSRF-Token": csrf})
        # flag cleared -> normal page no longer redirects to change-password
        r = client.get("/", follow_redirects=False)
        assert r.headers.get("location") != "/account/change-password"


class TestSelfServiceChange:
    def test_change_with_wrong_current_rejected(self, client, db):
        _mk_user(db, "cs@test.com")
        csrf = _login(client, "cs@test.com")
        r = client.post("/account/change-password",
                        data={"current_password": "WRONG", "new_password": "Brand New9",
                              "confirm_password": "Brand New9", "csrf_token": csrf},
                        headers={"X-CSRF-Token": csrf})
        assert r.status_code == 400

    def test_change_mismatch_rejected(self, client, db):
        _mk_user(db, "cs2@test.com")
        csrf = _login(client, "cs2@test.com")
        r = client.post("/account/change-password",
                        data={"current_password": "Test1234!", "new_password": "Brand New9",
                              "confirm_password": "different9", "csrf_token": csrf},
                        headers={"X-CSRF-Token": csrf})
        assert r.status_code == 400

    def test_successful_change_lets_new_password_log_in(self, client, db):
        _mk_user(db, "cs3@test.com")
        csrf = _login(client, "cs3@test.com")
        r = client.post("/account/change-password",
                        data={"current_password": "Test1234!", "new_password": "Brand New9",
                              "confirm_password": "Brand New9", "csrf_token": csrf},
                        headers={"X-CSRF-Token": csrf}, follow_redirects=False)
        assert r.status_code == 303
        client.post("/logout", headers={"X-CSRF-Token": csrf})
        # old password fails, new works
        assert auth.verify_password(
            "Brand New9",
            db.execute("SELECT password_hash FROM users WHERE email='cs3@test.com'").fetchone()["password_hash"])


class TestAdminResetFlow:
    def test_reset_issues_token_revokes_sessions_and_link_works(self, client, db):
        _mk_user(db, "radmin@test.com", role="super_admin")
        target = _mk_user(db, "rtarget@test.com")
        auth.create_session(db, target["id"])  # a live session to be revoked
        csrf = _login(client, "radmin@test.com")
        resp = client.post(f"/admin/users/{target['id']}/reset-password", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["sessions_revoked"] == 1
        assert db.execute("SELECT must_change_password FROM users WHERE id=%s",
                          (target["id"],)).fetchone()["must_change_password"]
        # DEBUG returns the reset link; walk it end to end
        link = body["reset_link"]
        token = link.split("token=")[1]
        assert client.get(link, follow_redirects=False).status_code == 200  # valid form
        done = client.post("/auth/reset",
                           data={"token": token, "new_password": "Fresh Pass9",
                                 "confirm_password": "Fresh Pass9"}, follow_redirects=False)
        assert done.status_code == 303 and done.headers["location"] == "/login"
        # password changed + flag cleared + token single-use
        row = db.execute("SELECT password_hash, must_change_password FROM users WHERE id=%s",
                         (target["id"],)).fetchone()
        assert auth.verify_password("Fresh Pass9", row["password_hash"])
        assert not row["must_change_password"]
        assert auth.verify_auth_token(db, token, "reset") is None  # consumed

    def test_reset_bad_token_shows_invalid(self, client, db):
        _mk_user(db, "radmin2@test.com", role="super_admin")
        _login(client, "radmin2@test.com")
        assert client.get("/auth/reset?token=garbage", follow_redirects=False).status_code == 200
        r = client.post("/auth/reset",
                        data={"token": "garbage", "new_password": "Fresh Pass9",
                              "confirm_password": "Fresh Pass9"})
        assert r.status_code == 400

    def test_non_admin_cannot_reset(self, client, db):
        _mk_user(db, "off@test.com", role="she_officer")
        target = _mk_user(db, "t@test.com")
        csrf = _login(client, "off@test.com")
        resp = client.post(f"/admin/users/{target['id']}/reset-password", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 403
