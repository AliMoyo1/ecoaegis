"""C3: leading-indicator site scoring (near-miss ratio, overdue CAs,
inspection pass rate) + grounded AI explanation.

Data-service tests use the plain `db` fixture. The AI-explanation path is
tested through the real HTTP route (client fixture) with ask_ai mocked,
matching test_incident_ai_classify.py's established convention - patch()
auto-detects ask_ai is a coroutine function and wraps it in an AsyncMock.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from sheplatform.core.auth import hash_password
from sheplatform.modules.leading_indicators import data_service


def _mk_user(db, role, email, org_id=1):
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "T", "U", role, org_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
    return dict(row)


def _mk_site(db, org_id, code, name):
    db.execute(
        "INSERT INTO sites (site_code, site_name, site_type, status, org_id) "
        "VALUES (%s, %s, 'tower', 'active', %s)", (code, name, org_id))
    db.commit()
    return dict(db.execute("SELECT * FROM sites WHERE site_code = %s", (code,)).fetchone())


def _mk_incident(db, site_name, incident_type, org_id, reporter_id):
    from sheplatform.modules.incidents import data_service as inc_service
    return inc_service.create_incident(
        db, title="t", description="d", severity="low", incident_type=incident_type,
        occurred_at=datetime.now(timezone.utc).isoformat(), location=site_name,
        reported_by=reporter_id, org_id=org_id)


def _mk_overdue_ca(db, incident_id, ref, org_id):
    db.execute(
        "INSERT INTO corrective_actions (action_ref, source_type, source_id, title, status, org_id) "
        "VALUES (%s, 'incident', %s, 'fix it', 'overdue', %s)", (ref, incident_id, org_id))
    db.commit()


def _mk_inspection_with_results(db, site_name, org_id, ref, results):
    db.execute(
        "INSERT INTO inspections (inspection_ref, title, site_location, status, org_id) "
        "VALUES (%s, 't', %s, 'completed', %s)", (ref, site_name, org_id))
    db.commit()
    insp_id = db.execute("SELECT id FROM inspections WHERE inspection_ref = %s", (ref,)).fetchone()["id"]
    for i, result in enumerate(results):
        db.execute(
            "INSERT INTO inspection_results (inspection_id, checklist_item, result) "
            "VALUES (%s, %s, %s)", (insp_id, f"item{i}", result))
    db.commit()


class TestPerSiteScores:
    def test_fails_closed_without_org(self, db):
        _mk_site(db, 1, "S1", "Clean Site")
        assert data_service.per_site_scores(db, None) == []

    def test_worse_site_ranks_above_clean_site(self, db):
        """The guide's own success criterion: a site with many overdue CAs
        and low inspection scores ranks above a clean site."""
        officer = _mk_user(db, "she_officer", "li1@test.com")
        _mk_site(db, 1, "CLEAN", "Clean Site")
        _mk_site(db, 1, "BAD", "Bad Site")

        # Clean site: one accident, no overdue CAs, all-pass inspection
        _mk_incident(db, "Clean Site", "accident", 1, officer["id"])
        _mk_inspection_with_results(db, "Clean Site", 1, "INS-CLEAN", ["pass", "pass", "pass"])

        # Bad site: mostly near-misses, an overdue CA, a failed inspection
        inc = _mk_incident(db, "Bad Site", "near_miss", 1, officer["id"])
        _mk_incident(db, "Bad Site", "near_miss", 1, officer["id"])
        _mk_overdue_ca(db, inc["id"], "CA-BAD-1", 1)
        _mk_inspection_with_results(db, "Bad Site", 1, "INS-BAD", ["pass", "fail", "fail"])

        scores = data_service.per_site_scores(db, 1)
        by_code = {s["site_code"]: s for s in scores}
        assert by_code["BAD"]["score"] > by_code["CLEAN"]["score"]
        assert scores[0]["site_code"] == "BAD"  # ranked worst-first
        assert scores[0]["rank"] == 1

    def test_no_inspections_scores_as_high_risk_not_clean(self, db):
        _mk_site(db, 1, "S2", "No Inspection Site")
        scores = data_service.per_site_scores(db, 1)
        site = next(s for s in scores if s["site_code"] == "S2")
        assert site["inspection_pass_rate"] is None
        # A None pass rate must contribute the worst-case risk component,
        # not be silently treated as a clean/perfect record.
        assert site["score"] >= 20  # inspection_risk=10 * weight 2 = 20 minimum

    def test_org_isolation(self, db):
        db.execute("INSERT INTO organisations (name, slug) VALUES ('Other Org', 'other-li')")
        db.commit()
        other_org = db.execute("SELECT id FROM organisations WHERE slug = 'other-li'").fetchone()["id"]
        _mk_site(db, 1, "MINE", "My Site")
        _mk_site(db, other_org, "THEIRS", "Their Site")
        scores = data_service.per_site_scores(db, 1)
        assert [s["site_code"] for s in scores] == ["MINE"]


class TestExplainSiteHttp:
    def _login(self, client, email):
        client.post("/login", data={"email": email, "password": "Test1234!"})
        return client.cookies.get("she_csrf", "")

    @patch("sheplatform.modules.leading_indicators.data_service.ask_ai",
          return_value="This site scores worse due to a higher near-miss ratio and an overdue corrective action.")
    def test_explain_returns_grounded_text(self, mock_ai, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "she_manager", "li2@test.com")
            site = _mk_site(db, 1, "S3", "Explain Site")
        finally:
            db.close()
        csrf = self._login(client, "li2@test.com")
        resp = client.post(f"/leading-indicators/api/explain/{site['id']}",
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert "near-miss" in data["explanation"]
        mock_ai.assert_called_once()

    def test_explain_unknown_site_404(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            _mk_user(db, "she_manager", "li3@test.com")
        finally:
            db.close()
        csrf = self._login(client, "li3@test.com")
        resp = client.post("/leading-indicators/api/explain/999999", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 404

    def test_sites_to_watch_org_isolation(self, client):
        from sheplatform.database import get_db
        db = get_db()
        try:
            db.execute("INSERT INTO organisations (name, slug) VALUES ('Other Org2', 'other-li-2')")
            db.commit()
            other_org = db.execute("SELECT id FROM organisations WHERE slug = 'other-li-2'").fetchone()["id"]
            _mk_user(db, "she_manager", "li4@test.com", org_id=1)
            _mk_site(db, 1, "S4", "Mine")
            _mk_site(db, other_org, "S5", "Theirs")
        finally:
            db.close()
        csrf = self._login(client, "li4@test.com")
        resp = client.get("/leading-indicators/api/sites-to-watch", headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200
        codes = [s["site_code"] for s in resp.json()["sites"]]
        assert codes == ["S4"]
