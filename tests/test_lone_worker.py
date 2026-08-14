"""C2: lone worker / man-down (check-in lifecycle + missed-checkin escalation).

Data-service and scheduler tests use the plain `db` fixture (org 1, the
seeded "Test Org"). Org-isolation and cross-worker-ownership tests go
through the real HTTP route with the `client` fixture, matching the
established convention (a data-service-only test would miss a route-level
capability/ownership regression).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sheplatform.core.auth import hash_password
from sheplatform.modules.lone_worker import data_service
from sheplatform.modules.lone_worker import scheduler as lw_scheduler


def _mk_user(db, role, email, org_id=1):
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "T", "U", role, org_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
    return dict(row)


def _mk_session(db, worker_id, org_id=1, duration=60, **kwargs):
    return data_service.start_checkin(
        db, worker_id=worker_id, expected_duration_minutes=duration, org_id=org_id, **kwargs)


def _force_past_deadline(db, session_id):
    """Backdate a session's deadline so the scheduler treats it as lapsed."""
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    db.execute(
        "UPDATE lone_worker_checkins SET expected_checkin_at = %s WHERE id = %s",
        (past, session_id))
    db.commit()


class TestCheckinLifecycle:
    def test_start_checkin_computes_deadline(self, db):
        worker = _mk_user(db, "employee", "lw1@test.com")
        session = _mk_session(db, worker["id"], duration=90,
                              location="Bulawayo Tower 1",
                              nominated_contact_name="Jane Moyo",
                              nominated_contact_phone="+263771234567")
        assert session["session_ref"].startswith("LWC-")
        assert session["status"] == "active"
        started = datetime.fromisoformat(session["started_at"])
        deadline = datetime.fromisoformat(session["expected_checkin_at"])
        assert 89 <= (deadline - started).total_seconds() / 60 <= 91

    def test_check_in_marks_safe(self, db):
        worker = _mk_user(db, "employee", "lw2@test.com")
        session = _mk_session(db, worker["id"])
        result = data_service.check_in(db, session["id"], worker["id"])
        assert result["ok"] is True
        assert result["session"]["status"] == "checked_in"
        assert result["session"]["last_checkin_at"] is not None

    def test_check_in_rejects_wrong_worker(self, db):
        worker = _mk_user(db, "employee", "lw3@test.com")
        other = _mk_user(db, "employee", "lw3b@test.com")
        session = _mk_session(db, worker["id"])
        result = data_service.check_in(db, session["id"], other["id"])
        assert result["ok"] is False

    def test_cannot_check_in_twice(self, db):
        worker = _mk_user(db, "employee", "lw4@test.com")
        session = _mk_session(db, worker["id"])
        data_service.check_in(db, session["id"], worker["id"])
        second = data_service.check_in(db, session["id"], worker["id"])
        assert second["ok"] is False

    def test_extend_pushes_deadline_forward(self, db):
        worker = _mk_user(db, "employee", "lw5@test.com")
        session = _mk_session(db, worker["id"], duration=60)
        original_deadline = datetime.fromisoformat(session["expected_checkin_at"])
        result = data_service.extend_checkin(db, session["id"], worker["id"], 30)
        assert result["ok"] is True
        new_deadline = datetime.fromisoformat(result["session"]["expected_checkin_at"])
        assert (new_deadline - original_deadline).total_seconds() / 60 == 30
        assert result["session"]["status"] == "active"

    def test_extend_rejects_non_positive_minutes(self, db):
        worker = _mk_user(db, "employee", "lw6@test.com")
        session = _mk_session(db, worker["id"])
        result = data_service.extend_checkin(db, session["id"], worker["id"], 0)
        assert result["ok"] is False

    def test_cancel_marks_cancelled(self, db):
        worker = _mk_user(db, "employee", "lw7@test.com")
        session = _mk_session(db, worker["id"])
        result = data_service.cancel_checkin(db, session["id"], worker["id"])
        assert result["ok"] is True
        assert result["session"]["status"] == "cancelled"


class TestListActiveCheckins:
    def test_fails_closed_without_org(self, db):
        worker = _mk_user(db, "employee", "lw8@test.com")
        _mk_session(db, worker["id"])
        assert data_service.list_active_checkins(db, None) == []

    def test_only_lists_active(self, db):
        worker = _mk_user(db, "employee", "lw9@test.com")
        s1 = _mk_session(db, worker["id"])
        s2 = _mk_session(db, worker["id"])
        data_service.check_in(db, s2["id"], worker["id"])
        sessions = data_service.list_active_checkins(db, 1)
        assert [s["id"] for s in sessions] == [s1["id"]]


class TestEscalationScheduler:
    def test_lapsed_session_escalates_with_notification_and_sms(self, db, monkeypatch):
        sms_calls = []
        monkeypatch.setattr(lw_scheduler, "send_sms",
                            lambda phone, body: sms_calls.append((phone, body)) or {"ok": True})
        _mk_user(db, "she_manager", "lw10-mgr@test.com")
        worker = _mk_user(db, "employee", "lw10@test.com")
        session = _mk_session(db, worker["id"], location="Mutare Branch",
                              nominated_contact_phone="+263771111111")
        _force_past_deadline(db, session["id"])

        alerts = lw_scheduler.check_lapsed_checkins(db)

        assert len(alerts) == 1
        updated = data_service.get_checkin(db, session["id"])
        assert updated["status"] == "escalated"
        assert updated["escalated_at"] is not None

        notif = db.execute(
            "SELECT * FROM notifications WHERE title LIKE %s", ("%missed%",)).fetchone()
        assert notif is not None
        assert "Mutare Branch" in notif["body"]

        assert len(sms_calls) == 1
        assert sms_calls[0][0] == "+263771111111"

    def test_no_nominated_contact_skips_sms_but_still_escalates(self, db, monkeypatch):
        sms_calls = []
        monkeypatch.setattr(lw_scheduler, "send_sms",
                            lambda phone, body: sms_calls.append(phone) or {"ok": True})
        _mk_user(db, "she_manager", "lw11-mgr@test.com")
        worker = _mk_user(db, "employee", "lw11@test.com")
        session = _mk_session(db, worker["id"])
        _force_past_deadline(db, session["id"])

        lw_scheduler.check_lapsed_checkins(db)

        assert data_service.get_checkin(db, session["id"])["status"] == "escalated"
        assert sms_calls == []

    def test_active_session_within_deadline_is_untouched(self, db):
        worker = _mk_user(db, "employee", "lw12@test.com")
        session = _mk_session(db, worker["id"], duration=60)
        alerts = lw_scheduler.check_lapsed_checkins(db)
        assert alerts == []
        assert data_service.get_checkin(db, session["id"])["status"] == "active"


class TestLoneWorkerHttp:
    def _login(self, client, email):
        client.post("/login", data={"email": email, "password": "Test1234!"})
        return client.cookies.get("she_csrf", "")

    def test_start_and_checkin_via_http(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "employee", "http1@test.com")
        finally:
            db.close()
        csrf = self._login(client, "http1@test.com")
        resp = client.post(
            "/lone-worker/api/start",
            data={"expected_duration_minutes": "45", "location": "Harare HQ"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 201, resp.text
        session_id = resp.json()["session"]["id"]

        checkin_resp = client.post(f"/lone-worker/api/{session_id}/checkin",
                                   headers={"X-CSRF-Token": csrf})
        assert checkin_resp.status_code == 200
        assert checkin_resp.json()["session"]["status"] == "checked_in"

    def test_cannot_check_in_another_workers_session(self, client):
        """Regression: ownership must be enforced at the route, not assumed
        from org membership alone - two workers in the same org must not be
        able to close each other's sessions.
        """
        from sheplatform.database import get_db
        db = get_db()
        try:
            owner = _mk_user(db, "employee", "http2-owner@test.com", org_id=1)
            _mk_user(db, "employee", "http2@test.com", org_id=1)
            session = _mk_session(db, owner["id"])
        finally:
            db.close()
        csrf = self._login(client, "http2@test.com")
        resp = client.post(f"/lone-worker/api/{session['id']}/checkin",
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 404

    def test_list_endpoint_org_isolation(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            db.execute("INSERT INTO organisations (name, slug) VALUES ('Other Org', 'other-org-lw')")
            db.commit()
            other_org = db.execute(
                "SELECT id FROM organisations WHERE slug = 'other-org-lw'").fetchone()["id"]

            me = _mk_user(db, "employee", "http3@test.com", org_id=1)
            _mk_session(db, me["id"], org_id=1)

            other = _mk_user(db, "employee", "http3-other@test.com", org_id=other_org)
            _mk_session(db, other["id"], org_id=other_org)
        finally:
            db.close()
        csrf = self._login(client, "http3@test.com")
        resp = client.get("/lone-worker/api/list", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["worker_id"] == me["id"]

    def test_man_down_escalates_immediately(self, client, monkeypatch):
        from sheplatform.modules.lone_worker import scheduler as scheduler_mod
        monkeypatch.setattr(scheduler_mod, "send_sms", lambda phone, body: {"ok": True})
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "she_manager", "http4-mgr@test.com", org_id=1)
            worker = _mk_user(db, "employee", "http4@test.com", org_id=1)
            session = _mk_session(db, worker["id"], duration=120)  # nowhere near its deadline
        finally:
            db.close()
        csrf = self._login(client, "http4@test.com")
        resp = client.post(f"/lone-worker/api/{session['id']}/man-down",
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        assert resp.json()["escalated"] is True

        db2 = get_db()
        try:
            updated = data_service.get_checkin(db2, session["id"])
            assert updated["status"] == "escalated"
        finally:
            db2.close()

    def test_man_down_rejects_another_workers_session(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            owner = _mk_user(db, "employee", "http5-owner@test.com", org_id=1)
            _mk_user(db, "employee", "http5@test.com", org_id=1)
            session = _mk_session(db, owner["id"])
        finally:
            db.close()
        csrf = self._login(client, "http5@test.com")
        resp = client.post(f"/lone-worker/api/{session['id']}/man-down",
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 404
