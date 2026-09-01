"""Tier-1 Increment E (SEC-SHE-001): enforced-MFA policy. Users whose role is in
MFA_REQUIRED_ROLES must enrol before using the platform. Off by default (empty
config); these tests turn it on via monkeypatch."""
from __future__ import annotations

import pyotp

from sheplatform.core import auth


def _mk_user(db, email, role, org_id=1, mfa_enabled=False, pw="Test1234!"):
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id, "
        "mfa_enabled) VALUES (%s, %s, 'T', 'U', %s, %s, %s)",
        (email, auth.hash_password(pw), role, org_id, mfa_enabled))
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _login(client, email, pw="Test1234!"):
    client.post("/login", data={"email": email, "password": pw})
    return client.cookies.get("she_csrf", "")


def _require(monkeypatch, *roles):
    monkeypatch.setattr("sheplatform.config.settings.MFA_REQUIRED_ROLES", frozenset(roles))


class TestEnforcementOffByDefault:
    def test_privileged_user_not_forced_when_unconfigured(self, client, db):
        # default MFA_REQUIRED_ROLES is empty -> enforcement is a no-op
        _mk_user(db, "sa@e.com", "super_admin")
        _login(client, "sa@e.com")
        r = client.get("/", follow_redirects=False)
        assert r.headers.get("location") != "/mfa/setup"


class TestEnforcementWhenConfigured:
    def test_required_role_without_mfa_redirected_to_setup(self, client, db, monkeypatch):
        _require(monkeypatch, "super_admin")
        _mk_user(db, "sa2@e.com", "super_admin")            # no MFA
        _login(client, "sa2@e.com")
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/mfa/setup"
        # the /mfa area itself stays reachable (no redirect loop)
        assert client.get("/mfa/setup", follow_redirects=False).status_code == 200

    def test_non_required_role_not_redirected(self, client, db, monkeypatch):
        _require(monkeypatch, "super_admin")
        _mk_user(db, "off@e.com", "she_officer")            # not in required set
        _login(client, "off@e.com")
        r = client.get("/", follow_redirects=False)
        assert r.headers.get("location") != "/mfa/setup"

    def test_enrolled_required_role_hits_challenge_not_setup(self, client, db, monkeypatch):
        _require(monkeypatch, "super_admin")
        _mk_user(db, "sa3@e.com", "super_admin", mfa_enabled=True)
        _login(client, "sa3@e.com")
        r = client.get("/", follow_redirects=False)
        # already enrolled -> the per-session challenge gate applies, not enrolment
        assert r.headers["location"] == "/mfa/challenge"

    def test_capability_route_also_enforces(self, client, db, monkeypatch):
        _require(monkeypatch, "super_admin")
        _mk_user(db, "sa4@e.com", "super_admin")
        _login(client, "sa4@e.com")
        r = client.get("/admin/users", follow_redirects=False)   # require_capability path
        assert r.status_code == 303 and r.headers["location"] == "/mfa/setup"


class TestConfirmVerifiesSession:
    def test_confirm_marks_session_verified(self, client, db):
        """A valid confirm code proves possession, so the confirming session is
        marked verified (no immediate re-challenge)."""
        _mk_user(db, "enr@e.com", "she_officer")
        csrf = _login(client, "enr@e.com")
        client.post("/mfa/api/enroll", headers={"X-CSRF-Token": csrf})
        secret = db.execute(
            "SELECT mfa_secret FROM users WHERE email='enr@e.com'").fetchone()["mfa_secret"]
        r = client.post("/mfa/api/confirm", data={"code": pyotp.TOTP(secret).now()},
                        headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200 and r.json()["ok"]
        row = db.execute("SELECT s.mfa_verified FROM sessions s JOIN users u ON u.id = s.user_id "
                         "WHERE u.email='enr@e.com'").fetchone()
        assert row["mfa_verified"]


class TestForcedEnrolmentEndToEnd:
    def test_forced_enrolment_resolves(self, client, db, monkeypatch):
        _require(monkeypatch, "super_admin")
        _mk_user(db, "sa5@e.com", "super_admin")
        csrf = _login(client, "sa5@e.com")
        assert client.get("/", follow_redirects=False).headers["location"] == "/mfa/setup"
        # enrol + confirm through the (allowed) /mfa routes
        client.post("/mfa/api/enroll", headers={"X-CSRF-Token": csrf})
        secret = db.execute(
            "SELECT mfa_secret FROM users WHERE email='sa5@e.com'").fetchone()["mfa_secret"]
        client.post("/mfa/api/confirm", data={"code": pyotp.TOTP(secret).now()},
                    headers={"X-CSRF-Token": csrf})
        # now enrolled AND session verified -> neither gate fires any more
        loc = client.get("/", follow_redirects=False).headers.get("location")
        assert loc not in ("/mfa/setup", "/mfa/challenge")
