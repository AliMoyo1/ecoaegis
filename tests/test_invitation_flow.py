"""Tier-1 Increment D: invitation flow - admin invite creates a pending
(inactive, no-usable-password) account + 'invite' token; the invitee accepts
via a tokenized link, which sets their password and activates the account."""
from __future__ import annotations

from sheplatform.core import auth


def _mk_user(db, email, role="employee", org_id=1, pw="Test1234!"):
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, 'T', 'U', %s, %s)",
        (email, auth.hash_password(pw), role, org_id))
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _login(client, email, pw="Test1234!"):
    client.post("/login", data={"email": email, "password": pw})
    return client.cookies.get("she_csrf", "")


def _pending_invite(db, email="p@inv.com", role="employee", org_id=1):
    """Create a pending account + 'invite' token directly (exercises the public
    accept flow without the admin route)."""
    uid = db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id, "
        "is_active) VALUES (%s, %s, 'P', 'Q', %s, %s, FALSE) RETURNING id",
        (email, auth.unusable_password_hash(), role, org_id)).fetchone()["id"]
    db.commit()
    return uid, auth.issue_auth_token(db, uid, "invite", 72)


class TestAdminInvite:
    def test_invite_creates_pending_inactive_user(self, client, db):
        _mk_user(db, "admin@inv.com", role="super_admin")
        csrf = _login(client, "admin@inv.com")
        resp = client.post("/admin/users/invite",
                           data={"email": "Newbie@INV.com", "first_name": "New",
                                 "last_name": "Bie", "role_key": "employee"},
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] and "invite_link" in body
        row = db.execute("SELECT is_active, org_id, role_key, must_change_password "
                         "FROM users WHERE email='newbie@inv.com'").fetchone()  # lower-cased
        assert not row["is_active"]                 # pending until accept
        assert row["org_id"] == 1                    # inviter's org
        assert row["role_key"] == "employee"
        assert not row["must_change_password"]       # they set their own on accept

    def test_invite_duplicate_email_rejected(self, client, db):
        _mk_user(db, "admin2@inv.com", role="super_admin")
        _mk_user(db, "taken@inv.com")
        csrf = _login(client, "admin2@inv.com")
        resp = client.post("/admin/users/invite",
                           data={"email": "taken@inv.com", "first_name": "X",
                                 "last_name": "Y", "role_key": "employee"},
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400

    def test_invite_unknown_role_rejected(self, client, db):
        _mk_user(db, "admin3@inv.com", role="super_admin")
        csrf = _login(client, "admin3@inv.com")
        resp = client.post("/admin/users/invite",
                           data={"email": "who@inv.com", "first_name": "X",
                                 "last_name": "Y", "role_key": "wizard"},
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400
        assert db.execute("SELECT id FROM users WHERE email='who@inv.com'").fetchone() is None

    def test_non_admin_cannot_invite(self, client, db):
        _mk_user(db, "officer@inv.com", role="she_officer")
        csrf = _login(client, "officer@inv.com")
        resp = client.post("/admin/users/invite",
                           data={"email": "x@inv.com", "first_name": "X",
                                 "last_name": "Y", "role_key": "employee"},
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 403


class TestAcceptInvite:
    def test_accept_page_valid_and_invalid(self, client, db):
        _, raw = _pending_invite(db, "page@inv.com")
        good = client.get(f"/auth/accept-invite?token={raw}")
        assert good.status_code == 200 and "Activate account" in good.text
        bad = client.get("/auth/accept-invite?token=garbage")
        assert bad.status_code == 200 and "invalid or has expired" in bad.text

    def test_accept_mismatch_rejected(self, client, db):
        _, raw = _pending_invite(db, "mm@inv.com")
        r = client.post("/auth/accept-invite",
                        data={"token": raw, "new_password": "Brand New9",
                              "confirm_password": "different9"})
        assert r.status_code == 400

    def test_accept_weak_password_rejected(self, client, db):
        _, raw = _pending_invite(db, "weak@inv.com")
        r = client.post("/auth/accept-invite",
                        data={"token": raw, "new_password": "short",
                              "confirm_password": "short"})
        assert r.status_code == 400

    def test_accept_bad_token_rejected(self, client, db):
        r = client.post("/auth/accept-invite",
                        data={"token": "garbage", "new_password": "Brand New9",
                              "confirm_password": "Brand New9"})
        assert r.status_code == 400

    def test_accept_activates_sets_password_and_is_single_use(self, client, db):
        uid, raw = _pending_invite(db, "accept@inv.com")
        done = client.post("/auth/accept-invite",
                           data={"token": raw, "new_password": "Chosen Pass9",
                                 "confirm_password": "Chosen Pass9"}, follow_redirects=False)
        assert done.status_code == 303 and done.headers["location"] == "/login"
        row = db.execute("SELECT password_hash, is_active, must_change_password "
                         "FROM users WHERE id=%s", (uid,)).fetchone()
        assert row["is_active"]                       # activated
        assert not row["must_change_password"]
        assert auth.verify_password("Chosen Pass9", row["password_hash"])
        assert auth.verify_auth_token(db, raw, "invite") is None   # consumed
        # single-use: replaying the token fails
        again = client.post("/auth/accept-invite",
                            data={"token": raw, "new_password": "Another Pw9",
                                  "confirm_password": "Another Pw9"})
        assert again.status_code == 400


class TestInviteEndToEnd:
    def test_invite_then_accept_then_login(self, client, db):
        _mk_user(db, "boss@inv.com", role="super_admin")
        csrf = _login(client, "boss@inv.com")
        resp = client.post("/admin/users/invite",
                           data={"email": "hire@inv.com", "first_name": "New",
                                 "last_name": "Hire", "role_key": "employee"},
                           headers={"X-CSRF-Token": csrf})
        token = resp.json()["invite_link"].split("token=")[1]
        # a fresh client (the invitee) accepts, then signs in with their new password
        invitee = client
        invitee.cookies.clear()
        invitee.post("/auth/accept-invite",
                     data={"token": token, "new_password": "First Login9",
                           "confirm_password": "First Login9"})
        invitee.post("/login", data={"email": "hire@inv.com", "password": "First Login9"})
        assert invitee.cookies.get("she_session")     # a real session was issued
