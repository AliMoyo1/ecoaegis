"""Tier-1 Increment B: session / device management (self-service + admin)."""
from __future__ import annotations

from sheplatform.core import auth


def _mk_user(db, email, role="employee", org_id=1):
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, 'T', 'U', %s, %s)",
        (email, auth.hash_password("Test1234!"), role, org_id))
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _login(client, email):
    client.post("/login", data={"email": email, "password": "Test1234!"})
    return client.cookies.get("she_csrf", "")


class TestAuthHelpers:
    def test_list_sessions_excludes_expired(self, db):
        u = _mk_user(db, "ls@test.com")
        auth.create_session(db, u["id"])
        db.execute(
            "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (%s, 'x', %s)",
            (u["id"], "2000-01-01T00:00:00+00:00"))  # already expired
        db.commit()
        active = auth.list_sessions(db, u["id"])
        assert len(active) == 1

    def test_revoke_session_is_owner_scoped(self, db):
        me = _mk_user(db, "me@test.com")
        other = _mk_user(db, "other@test.com")
        other_tok = auth.create_session(db, other["id"])
        other_sid = db.execute("SELECT id FROM sessions WHERE user_id = %s", (other["id"],)).fetchone()["id"]
        # I cannot revoke another user's session by passing my id
        assert auth.revoke_session(db, other_sid, me["id"]) is False
        assert auth.get_session_user(db, other_tok) is not None  # still valid

    def test_revoke_other_sessions_keeps_current(self, db):
        u = _mk_user(db, "ro@test.com")
        keep = db.execute("SELECT id FROM sessions WHERE token_hash = %s",
                          (__import__("hashlib").sha256(auth.create_session(db, u["id"]).encode()).hexdigest(),)).fetchone()["id"]
        auth.create_session(db, u["id"])
        auth.create_session(db, u["id"])
        assert auth.revoke_other_sessions(db, u["id"], keep) == 2
        remaining = [s["id"] for s in auth.list_sessions(db, u["id"])]
        assert remaining == [keep]


class TestSelfService:
    def test_list_my_sessions_flags_current(self, client, db):
        _mk_user(db, "self@test.com")
        _login(client, "self@test.com")
        resp = client.get("/account/sessions")
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) >= 1
        assert sum(1 for s in sessions if s["is_current"]) == 1

    def test_revoke_others_leaves_only_current(self, client, db):
        u = _mk_user(db, "self2@test.com")
        _login(client, "self2@test.com")
        auth.create_session(db, u["id"])  # a second device
        auth.create_session(db, u["id"])  # a third
        csrf = client.cookies.get("she_csrf", "")
        resp = client.post("/account/sessions/revoke-others", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200 and resp.json()["revoked"] == 2
        assert len(auth.list_sessions(db, u["id"])) == 1

    def test_cannot_revoke_another_users_session(self, client, db):
        _mk_user(db, "self3@test.com")
        victim = _mk_user(db, "victim3@test.com")
        auth.create_session(db, victim["id"])
        victim_sid = db.execute("SELECT id FROM sessions WHERE user_id = %s",
                                (victim["id"],)).fetchone()["id"]
        csrf = _login(client, "self3@test.com")
        resp = client.delete(f"/account/sessions/{victim_sid}", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 404  # scoped to me -> not found


class TestAdminOversight:
    def test_admin_lists_and_revokes_user_sessions(self, client, db):
        _mk_user(db, "admin@test.com", role="super_admin")
        target = _mk_user(db, "target@test.com")
        auth.create_session(db, target["id"])
        auth.create_session(db, target["id"])
        csrf = _login(client, "admin@test.com")
        listed = client.get(f"/admin/api/users/{target['id']}/sessions")
        assert listed.status_code == 200 and len(listed.json()["sessions"]) == 2
        revoke = client.post(f"/admin/users/{target['id']}/sessions/revoke-all",
                             headers={"X-CSRF-Token": csrf})
        assert revoke.status_code == 200 and revoke.json()["revoked"] == 2
        assert auth.list_sessions(db, target["id"]) == []
        assert db.execute("SELECT COUNT(*) AS c FROM audit_log WHERE action = 'user.sessions_revoke_all'"
                          ).fetchone()["c"] == 1

    def test_admin_revoke_specific_session(self, client, db):
        _mk_user(db, "admin2@test.com", role="super_admin")
        target = _mk_user(db, "target2@test.com")
        auth.create_session(db, target["id"])
        sid = db.execute("SELECT id FROM sessions WHERE user_id = %s", (target["id"],)).fetchone()["id"]
        csrf = _login(client, "admin2@test.com")
        resp = client.delete(f"/admin/api/users/{target['id']}/sessions/{sid}",
                             headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200
        assert auth.list_sessions(db, target["id"]) == []

    def test_cross_org_user_sessions_404(self, client, db):
        db.execute("INSERT INTO organisations (id, name, slug) VALUES (2, 'Org2', 'o2') "
                   "ON CONFLICT DO NOTHING")
        db.commit()
        _mk_user(db, "admin3@test.com", role="super_admin", org_id=1)
        other = _mk_user(db, "other3@test.com", org_id=2)
        _login(client, "admin3@test.com")
        assert client.get(f"/admin/api/users/{other['id']}/sessions").status_code == 404

    def test_non_admin_cannot_view_others_sessions(self, client, db):
        _mk_user(db, "officer@test.com", role="she_officer")
        target = _mk_user(db, "t4@test.com")
        _login(client, "officer@test.com")
        assert client.get(f"/admin/api/users/{target['id']}/sessions").status_code == 403
