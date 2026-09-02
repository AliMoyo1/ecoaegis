"""Tier-1 Increment F: bulk user actions. One admin action over many in-org
users with a per-user partial-failure report; guards re-checked against live
state so a batch can never remove the last active super-admin."""
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


def _bulk(client, csrf, **payload):
    return client.post("/admin/users/bulk", json=payload, headers={"X-CSRF-Token": csrf})


class TestBulkValidation:
    def test_unknown_action_rejected(self, client, db):
        _mk_user(db, "a@b.com", role="super_admin")
        csrf = _login(client, "a@b.com")
        assert _bulk(client, csrf, action="nuke", user_ids=[1]).status_code == 400

    def test_empty_ids_rejected(self, client, db):
        _mk_user(db, "a2@b.com", role="super_admin")
        csrf = _login(client, "a2@b.com")
        assert _bulk(client, csrf, action="deactivate", user_ids=[]).status_code == 400

    def test_role_without_valid_role_rejected(self, client, db):
        _mk_user(db, "a3@b.com", role="super_admin")
        csrf = _login(client, "a3@b.com")
        assert _bulk(client, csrf, action="role", user_ids=[1], role_key="wizard").status_code == 400

    def test_non_admin_forbidden(self, client, db):
        _mk_user(db, "off@b.com", role="she_officer")
        csrf = _login(client, "off@b.com")
        assert _bulk(client, csrf, action="deactivate", user_ids=[1]).status_code == 403


class TestBulkDeactivate:
    def test_partial_failure_report(self, client, db):
        actor = _mk_user(db, "boss@b.com", role="super_admin")
        u1 = _mk_user(db, "u1@b.com")
        u2 = _mk_user(db, "u2@b.com")
        sess = auth.create_session(db, u1["id"])            # a live session to be revoked
        assert auth.get_session_user(db, sess) is not None
        csrf = _login(client, "boss@b.com")
        resp = _bulk(client, csrf, action="deactivate",
                     user_ids=[u1["id"], u2["id"], 9999, actor["id"]])
        assert resp.status_code == 200
        body = resp.json()
        assert body["succeeded"] == 2 and body["failed"] == 2
        by_id = {r["user_id"]: r for r in body["results"]}
        assert by_id[u1["id"]]["ok"] and by_id[u2["id"]]["ok"]
        assert not by_id[9999]["ok"] and "not found" in by_id[9999]["message"]
        assert not by_id[actor["id"]]["ok"] and "your own account" in by_id[actor["id"]]["message"]
        # effects: both deactivated, u1's session revoked
        assert not db.execute("SELECT is_active FROM users WHERE id=%s", (u1["id"],)).fetchone()["is_active"]
        assert auth.list_sessions(db, u1["id"]) == []

    def test_last_super_admin_protected_in_batch(self, client, db):
        # she_manager can manage users but is not itself a super_admin
        _mk_user(db, "mgr@b.com", role="she_manager")
        s = _mk_user(db, "solo-sa@b.com", role="super_admin")   # the only super_admin
        csrf = _login(client, "mgr@b.com")
        resp = _bulk(client, csrf, action="deactivate", user_ids=[s["id"]])
        r0 = resp.json()["results"][0]
        assert not r0["ok"] and "last active super admin" in r0["message"]
        assert db.execute("SELECT is_active FROM users WHERE id=%s", (s["id"],)).fetchone()["is_active"]


class TestBulkRole:
    def test_bulk_role_assign(self, client, db):
        _mk_user(db, "boss2@b.com", role="super_admin")
        u1 = _mk_user(db, "r1@b.com")
        u2 = _mk_user(db, "r2@b.com")
        csrf = _login(client, "boss2@b.com")
        resp = _bulk(client, csrf, action="role", user_ids=[u1["id"], u2["id"]],
                     role_key="she_officer")
        assert resp.json()["succeeded"] == 2
        for u in (u1, u2):
            assert db.execute("SELECT role_key FROM users WHERE id=%s",
                              (u["id"],)).fetchone()["role_key"] == "she_officer"

    def test_bulk_demote_last_super_admin_blocked(self, client, db):
        _mk_user(db, "mgr2@b.com", role="she_manager")
        s = _mk_user(db, "solo-sa2@b.com", role="super_admin")
        csrf = _login(client, "mgr2@b.com")
        resp = _bulk(client, csrf, action="role", user_ids=[s["id"]], role_key="employee")
        r0 = resp.json()["results"][0]
        assert not r0["ok"] and "last active super admin" in r0["message"]
        assert db.execute("SELECT role_key FROM users WHERE id=%s",
                          (s["id"],)).fetchone()["role_key"] == "super_admin"


class TestBulkResetPassword:
    def test_bulk_reset_password(self, client, db):
        _mk_user(db, "boss3@b.com", role="super_admin")
        u1 = _mk_user(db, "p1@b.com")
        u2 = _mk_user(db, "p2@b.com")
        auth.create_session(db, u1["id"])                    # to be revoked by the reset
        csrf = _login(client, "boss3@b.com")
        resp = _bulk(client, csrf, action="reset_password", user_ids=[u1["id"], u2["id"]])
        assert resp.json()["succeeded"] == 2
        for u in (u1, u2):
            row = db.execute("SELECT must_change_password FROM users WHERE id=%s",
                            (u["id"],)).fetchone()
            assert row["must_change_password"]
            tok = db.execute("SELECT COUNT(*) AS c FROM auth_tokens WHERE user_id=%s AND purpose='reset'",
                            (u["id"],)).fetchone()["c"]
            assert tok == 1
        assert auth.list_sessions(db, u1["id"]) == []        # session revoked
        # one reset email queued per user
        assert db.execute("SELECT COUNT(*) AS c FROM email_reminders "
                          "WHERE subject='EcoAegis password reset'").fetchone()["c"] == 2
