"""Tier-1 Increment A: user lifecycle, per-user audit, role preview, and the
is_active session-validation fix."""
from __future__ import annotations

from sheplatform.core import auth


def _mk_user(db, email, role="employee", org_id=1, active=True):
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id, is_active) "
        "VALUES (%s, %s, 'T', 'U', %s, %s, %s)",
        (email, auth.hash_password("Test1234!"), role, org_id, active))
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _login(client, email):
    client.post("/login", data={"email": email, "password": "Test1234!"})
    return client.cookies.get("she_csrf", "")


class TestSessionActiveEnforcement:
    def test_deactivated_user_session_stops_validating(self, db):
        u = _mk_user(db, "sess@test.com")
        token = auth.create_session(db, u["id"])
        assert auth.get_session_user(db, token) is not None
        db.execute("UPDATE users SET is_active = FALSE WHERE id = %s", (u["id"],))
        db.commit()
        assert auth.get_session_user(db, token) is None  # the fix

    def test_revoke_user_sessions_deletes_all(self, db):
        u = _mk_user(db, "revoke@test.com")
        auth.create_session(db, u["id"])
        auth.create_session(db, u["id"])
        assert auth.revoke_user_sessions(db, u["id"]) == 2
        assert db.execute("SELECT COUNT(*) AS c FROM sessions WHERE user_id = %s",
                          (u["id"],)).fetchone()["c"] == 0


class TestDeactivateReactivate:
    def test_deactivate_revokes_sessions_and_flags_inactive(self, client, db):
        _mk_user(db, "admin@test.com", role="super_admin")
        target = _mk_user(db, "victim@test.com")
        auth.create_session(db, target["id"])
        csrf = _login(client, "admin@test.com")
        resp = client.post(f"/admin/users/{target['id']}/deactivate", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        assert resp.json()["sessions_revoked"] == 1
        row = db.execute("SELECT is_active FROM users WHERE id = %s", (target["id"],)).fetchone()
        assert not row["is_active"]

    def test_cannot_deactivate_self(self, client, db):
        admin = _mk_user(db, "self@test.com", role="super_admin")
        csrf = _login(client, "self@test.com")
        resp = client.post(f"/admin/users/{admin['id']}/deactivate", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400

    def test_cannot_deactivate_last_super_admin(self, client, db):
        # she_manager (also has admin.users.manage) tries to deactivate the sole super_admin
        _mk_user(db, "mgr@test.com", role="she_manager")
        sole_admin = _mk_user(db, "sole@test.com", role="super_admin")
        csrf = _login(client, "mgr@test.com")
        resp = client.post(f"/admin/users/{sole_admin['id']}/deactivate", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400
        assert "super admin" in resp.json()["message"]

    def test_reactivate(self, client, db):
        _mk_user(db, "admin2@test.com", role="super_admin")
        target = _mk_user(db, "back@test.com", active=False)
        csrf = _login(client, "admin2@test.com")
        resp = client.post(f"/admin/users/{target['id']}/reactivate", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200
        assert db.execute("SELECT is_active FROM users WHERE id = %s",
                          (target["id"],)).fetchone()["is_active"]

    def test_cross_org_target_is_404(self, client, db):
        db.execute("INSERT INTO organisations (id, name, slug) VALUES (2, 'Org2', 'org2') "
                   "ON CONFLICT DO NOTHING")
        db.commit()
        _mk_user(db, "admin3@test.com", role="super_admin", org_id=1)
        other = _mk_user(db, "other@test.com", org_id=2)
        csrf = _login(client, "admin3@test.com")
        resp = client.post(f"/admin/users/{other['id']}/deactivate", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 404


class TestRoleChange:
    def test_change_role_audited(self, client, db):
        _mk_user(db, "admin4@test.com", role="super_admin")
        target = _mk_user(db, "promote@test.com", role="employee")
        csrf = _login(client, "admin4@test.com")
        resp = client.post(f"/admin/users/{target['id']}/role",
                           data={"role_key": "she_officer"}, headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200
        assert db.execute("SELECT role_key FROM users WHERE id = %s",
                          (target["id"],)).fetchone()["role_key"] == "she_officer"
        assert db.execute("SELECT COUNT(*) AS c FROM audit_log WHERE action = 'user.role_change'"
                          ).fetchone()["c"] == 1

    def test_unknown_role_rejected(self, client, db):
        _mk_user(db, "admin5@test.com", role="super_admin")
        target = _mk_user(db, "t5@test.com")
        csrf = _login(client, "admin5@test.com")
        resp = client.post(f"/admin/users/{target['id']}/role",
                           data={"role_key": "wizard"}, headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400

    def test_cannot_demote_last_super_admin(self, client, db):
        _mk_user(db, "mgr2@test.com", role="she_manager")
        sole = _mk_user(db, "sole2@test.com", role="super_admin")
        csrf = _login(client, "mgr2@test.com")
        resp = client.post(f"/admin/users/{sole['id']}/role",
                           data={"role_key": "employee"}, headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400


class TestAuditHistoryAndRolePreview:
    def test_per_user_audit_history(self, client, db):
        _mk_user(db, "admin6@test.com", role="super_admin")
        target = _mk_user(db, "hist@test.com", role="employee")
        csrf = _login(client, "admin6@test.com")
        client.post(f"/admin/users/{target['id']}/role",
                    data={"role_key": "she_officer"}, headers={"X-CSRF-Token": csrf})
        resp = client.get(f"/admin/users/{target['id']}/audit")
        assert resp.status_code == 200
        actions = [h["action"] for h in resp.json()["history"]]
        assert "user.role_change" in actions

    def test_role_preview_lists_capabilities(self, client, db):
        _mk_user(db, "admin7@test.com", role="super_admin")
        _login(client, "admin7@test.com")
        resp = client.get("/admin/api/roles/super_admin/preview")
        assert resp.status_code == 200
        assert resp.json()["capabilities"]  # super_admin has capabilities

    def test_role_preview_unknown_404(self, client, db):
        _mk_user(db, "admin8@test.com", role="super_admin")
        _login(client, "admin8@test.com")
        assert client.get("/admin/api/roles/wizard/preview").status_code == 404


class TestNonAdminForbidden:
    def test_officer_cannot_deactivate(self, client, db):
        _mk_user(db, "officer@test.com", role="she_officer")
        target = _mk_user(db, "t9@test.com")
        csrf = _login(client, "officer@test.com")
        resp = client.post(f"/admin/users/{target['id']}/deactivate", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 403
