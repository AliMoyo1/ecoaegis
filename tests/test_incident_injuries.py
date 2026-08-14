"""B5: incident intake depth (injuries, LTIFR, statutory autofill).

Data-service level tests use the plain `db` fixture (org 1, the seeded
"Test Org"), matching test_incidents.py's convention. The org-isolation
tests go through the real HTTP route with the `client` fixture from
conftest_http.py, since that is where the actual tenant boundary lives
(review lesson from PR #6: a data-service test alone would miss a route-level
capability/org-guard regression).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sheplatform.core.auth import hash_password
from sheplatform.modules.incidents import data_service


def _mk_user(db, role, email, org_id=1):
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "T", "U", role, org_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
    return dict(row)


def _mk_incident(db, user_id, severity="high", incident_type="accident", org_id=None):
    now = datetime.now(timezone.utc).isoformat()
    return data_service.create_incident(
        db, title="Test incident", description="Worker fell from a ladder",
        severity=severity, incident_type=incident_type, occurred_at=now,
        reported_by=user_id, org_id=org_id)


class TestCreateIncidentDepthFields:
    def test_intake_depth_fields_persist(self, db):
        officer = _mk_user(db, "she_officer", "id1@test.com")
        inc = data_service.create_incident(
            db, title="Ladder fall", description="d", severity="high",
            incident_type="accident", occurred_at=datetime.now(timezone.utc).isoformat(),
            reported_by=officer["id"], immediate_actions="Area cordoned off",
            estimated_cost=1500.50, witnesses=[{"name": "Jane Moyo"}])
        assert inc["immediate_actions"] == "Area cordoned off"
        assert float(inc["estimated_cost"]) == 1500.50
        import json
        assert json.loads(inc["witnesses"]) == [{"name": "Jane Moyo"}]

    def test_fields_default_sanely_when_omitted(self, db):
        officer = _mk_user(db, "she_officer", "id2@test.com")
        inc = _mk_incident(db, officer["id"])
        assert inc["immediate_actions"] is None
        assert inc["estimated_cost"] is None
        assert inc["witnesses"] == "[]"


class TestAddInjury:
    def test_add_injury_creates_row_and_timeline_entry(self, db):
        officer = _mk_user(db, "she_officer", "id3@test.com")
        inc = _mk_incident(db, officer["id"])
        result = data_service.add_injury(
            db, inc["id"], injured_name="John Banda", injured_type="employee",
            body_part="left ankle", injury_type="sprain", lost_time_days=3,
            medical_treatment="First aid on site", created_by=officer["id"])
        assert result["ok"] is True
        assert result["injury"]["injured_name"] == "John Banda"
        assert result["injury"]["lost_time_days"] == 3
        # org_id denormalised from the parent incident
        assert result["injury"]["org_id"] == inc["org_id"]

        timeline = data_service.get_timeline(db, inc["id"])
        assert any(e["event_type"] == "injury" and "John Banda" in e["event_text"] for e in timeline)

    def test_add_injury_unknown_incident_rejected(self, db):
        result = data_service.add_injury(db, 999999, injured_name="Ghost")
        assert result["ok"] is False

    def test_list_injuries_multiple_per_incident(self, db):
        officer = _mk_user(db, "she_officer", "id4@test.com")
        inc = _mk_incident(db, officer["id"])
        data_service.add_injury(db, inc["id"], injured_name="A", body_part="hand")
        data_service.add_injury(db, inc["id"], injured_name="B", body_part="foot")
        injuries = data_service.list_injuries(db, inc["id"])
        assert len(injuries) == 2
        assert {i["injured_name"] for i in injuries} == {"A", "B"}


class TestLtifr:
    def test_no_org_returns_zeroed_stats(self, db):
        stats = data_service.get_ltifr_stats(db, None)
        assert stats["lost_time_injuries"] == 0
        assert stats["ltifr"] is None

    def test_counts_only_lost_time_injuries(self, db):
        officer = _mk_user(db, "she_officer", "id5@test.com")
        inc = _mk_incident(db, officer["id"], org_id=1)
        data_service.add_injury(db, inc["id"], injured_name="A", lost_time_days=2, org_id=1)
        data_service.add_injury(db, inc["id"], injured_name="B", lost_time_days=0, org_id=1)  # first aid only, no LTI
        stats = data_service.get_ltifr_stats(db, 1)
        assert stats["lost_time_injuries"] == 1
        assert stats["total_lost_days"] == 2

    def test_ltifr_none_when_hours_not_configured(self, db):
        officer = _mk_user(db, "she_officer", "id6@test.com")
        inc = _mk_incident(db, officer["id"], org_id=1)
        data_service.add_injury(db, inc["id"], injured_name="A", lost_time_days=1, org_id=1)
        stats = data_service.get_ltifr_stats(db, 1)
        assert stats["ltifr"] is None
        assert stats["hours_worked"] is None
        assert stats["lost_time_injuries"] == 1  # real counts still returned, never a fabricated rate

    def test_ltifr_iso_million_hour_formula(self, db):
        """Regression: LTIFR = (LTI x 1,000,000) / hours worked (ISO 45001 /
        international standard). NOT the US OSHA 200,000-hour base.
        """
        officer = _mk_user(db, "she_officer", "id7@test.com")
        inc = _mk_incident(db, officer["id"], org_id=1)
        data_service.add_injury(db, inc["id"], injured_name="A", lost_time_days=1, org_id=1)
        data_service.add_injury(db, inc["id"], injured_name="B", lost_time_days=5, org_id=1)
        data_service.set_exposure_hours(db, 1, 500000)
        stats = data_service.get_ltifr_stats(db, 1)
        # 2 lost-time injuries x 1,000,000 / 500,000 hours = 4.0
        assert stats["ltifr"] == 4.0
        assert stats["lost_time_injuries"] == 2
        assert stats["total_lost_days"] == 6

    def test_period_filtering_excludes_out_of_range_injuries(self, db):
        officer = _mk_user(db, "she_officer", "id8@test.com")
        inc = _mk_incident(db, officer["id"], org_id=1)
        data_service.add_injury(db, inc["id"], injured_name="A", lost_time_days=1, org_id=1)
        far_future_start = (datetime.now(timezone.utc) + timedelta(days=400)).isoformat()
        far_future_end = (datetime.now(timezone.utc) + timedelta(days=430)).isoformat()
        stats = data_service.get_ltifr_stats(db, 1, far_future_start, far_future_end)
        assert stats["lost_time_injuries"] == 0


class TestExposureHours:
    def test_set_and_read_back(self, db):
        result = data_service.set_exposure_hours(db, 1, 250000)
        assert result["ok"] is True
        stats = data_service.get_ltifr_stats(db, 1)
        assert stats["hours_worked"] == 250000

    def test_unknown_org_rejected(self, db):
        result = data_service.set_exposure_hours(db, 999999, 100000)
        assert result["ok"] is False


class TestStatutoryInjurySummary:
    def test_nssa_template_pulls_real_injury_summary(self, db):
        from sheplatform.modules.statutory_reporting import data_service as sr_service
        officer = _mk_user(db, "she_manager", "id9@test.com")
        inc = data_service.create_incident(
            db, title="Critical fall", description="d", severity="critical",
            incident_type="accident",
            occurred_at=datetime.now(timezone.utc).isoformat(),
            reported_by=officer["id"], org_id=1)
        data_service.add_injury(
            db, inc["id"], injured_name="Tendai Moyo", body_part="right wrist",
            lost_time_days=4, org_id=1)

        sr_service.seed_templates(db)
        db.commit()
        report = sr_service.create_report(
            db, template_key="nssa_critical_incident",
            period_start=inc["occurred_at"][:10], period_end=inc["occurred_at"][:10],
            created_by=officer["id"], org_id=1, incident_id=inc["id"])
        assert report["ok"] is True
        summary = report["data"]["injured_persons"]
        assert "Tendai Moyo" in summary
        assert "right wrist" in summary
        assert "4 lost-time day" in summary

    def test_no_injuries_gives_honest_message(self, db):
        from sheplatform.modules.statutory_reporting import data_service as sr_service
        officer = _mk_user(db, "she_manager", "id10@test.com")
        inc = data_service.create_incident(
            db, title="Near miss, no injury", description="d", severity="critical",
            incident_type="accident",
            occurred_at=datetime.now(timezone.utc).isoformat(),
            reported_by=officer["id"], org_id=1)
        sr_service.seed_templates(db)
        db.commit()
        report = sr_service.create_report(
            db, template_key="nssa_critical_incident",
            period_start=inc["occurred_at"][:10], period_end=inc["occurred_at"][:10],
            created_by=officer["id"], org_id=1, incident_id=inc["id"])
        assert report["data"]["injured_persons"] == "No injuries recorded"


class TestOfflineSyncInjury:
    def test_idempotent_replay_does_not_duplicate_injury(self, db):
        """Regression: the offline sync handler previously would have added the
        injury again on a retried/duplicate sync of the same queued item, even
        though create_incident() correctly deduped the incident itself.
        """
        from sheplatform.modules.offline.routes import _apply_item
        officer = _mk_user(db, "she_officer", "id11@test.com")
        item = {
            "type": "incident",
            "idempotencyKey": "offline-key-1",
            "data": {
                "title": "Offline report", "description": "d", "severity": "high",
                "incident_type": "accident",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "idempotency_key": "offline-key-1",
                "injury": {"injured_name": "Field Tech", "body_part": "hand", "lost_time_days": 2},
            },
        }
        r1 = _apply_item(db, dict(item, data=dict(item["data"], injury=dict(item["data"]["injury"]))),
                         officer["id"], 1)
        assert r1["ok"] is True
        assert r1["idempotent"] is False
        assert "injury" in r1

        # Simulate a retried sync of the exact same item (e.g. the client
        # never got the first response and re-sent it).
        r2 = _apply_item(db, dict(item, data=dict(item["data"], injury=dict(item["data"]["injury"]))),
                         officer["id"], 1)
        assert r2["ok"] is True
        assert r2["idempotent"] is True
        assert "injury" not in r2  # second attempt must NOT add a duplicate injury

        injuries = data_service.list_injuries(db, r1["id"])
        assert len(injuries) == 1


class TestInjuryHttpAndOrgIsolation:
    def _login(self, client, email):
        client.post("/login", data={"email": email, "password": "Test1234!"})
        return client.cookies.get("she_csrf", "")

    def test_add_injury_via_http_and_appears_in_detail(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            officer = _mk_user(db, "she_officer", "http1@test.com")
            inc = _mk_incident(db, officer["id"], org_id=1)
        finally:
            db.close()
        csrf = self._login(client, "http1@test.com")
        resp = client.post(
            f"/incidents/api/{inc['id']}/injuries",
            data={"injured_name": "Http Test", "body_part": "shoulder", "lost_time_days": "1"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 201, resp.text

        detail = client.get(f"/incidents/api/{inc['id']}", headers={"X-CSRF-Token": csrf})
        assert any(i["injured_name"] == "Http Test" for i in detail.json()["incident"]["injuries"])

    def test_cannot_add_injury_to_another_orgs_incident(self, client):
        """Regression: the injuries route must apply the same org guard as
        api_detail. Verified by revert-verify (see PR description).
        """
        from sheplatform.database import get_db
        db = get_db()
        try:
            db.execute("INSERT INTO organisations (name, slug) VALUES ('Other Org', 'other-org')")
            db.commit()
            other_org = db.execute("SELECT id FROM organisations WHERE slug = 'other-org'").fetchone()["id"]
            other_officer = _mk_user(db, "she_officer", "http2-other@test.com", org_id=other_org)
            other_incident = _mk_incident(db, other_officer["id"], org_id=other_org)
            _mk_user(db, "she_officer", "http2@test.com", org_id=1)  # caller's own org
        finally:
            db.close()
        csrf = self._login(client, "http2@test.com")
        resp = client.post(
            f"/incidents/api/{other_incident['id']}/injuries",
            data={"injured_name": "Should not be allowed"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 404

    def test_exposure_hours_requires_manager_role(self, client):
        _mk_user_via_db = None
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "employee", "http3@test.com")
        finally:
            db.close()
        csrf = self._login(client, "http3@test.com")
        resp = client.post(
            "/incidents/api/settings/exposure-hours",
            data={"annual_exposure_hours": "200000"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 403

    def test_exposure_hours_manager_can_set(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "she_manager", "http4@test.com")
        finally:
            db.close()
        csrf = self._login(client, "http4@test.com")
        resp = client.post(
            "/incidents/api/settings/exposure-hours",
            data={"annual_exposure_hours": "300000"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        get_resp = client.get("/incidents/api/settings/exposure-hours", headers={"X-CSRF-Token": csrf})
        assert get_resp.json()["hours_worked"] == 300000
