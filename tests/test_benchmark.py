"""Site benchmarking tests."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _mk_user(db, role, email, org_id=1):
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "F", "L", role, org_id),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _mk_site(db, code, name, city, org_id=1):
    db.execute(
        "INSERT INTO sites (site_code, site_name, city, org_id) VALUES (%s, %s, %s, %s)",
        (code, name, city, org_id))
    db.commit()
    return dict(db.execute("SELECT * FROM sites WHERE site_code = %s", (code,)).fetchone())


class TestBenchmark:
    def test_ranks_sites_by_activity(self, db):
        officer = _mk_user(db, "she_officer", "bm1@test.com")
        busy = _mk_site(db, "BUSY", "Busy Branch", "Harare")
        quiet = _mk_site(db, "QUIET", "Quiet Branch", "Mutare")

        from sheplatform.modules.incidents.data_service import create_incident
        for i in range(3):
            create_incident(
                db, title=f"Incident {i}", description="x", severity="low",
                incident_type="accident", occurred_at="2026-08-01T10:00:00+00:00",
                reported_by=officer["id"], location=busy["site_name"], org_id=1)
        create_incident(
            db, title="Quiet incident", description="x", severity="low",
            incident_type="accident", occurred_at="2026-08-01T10:00:00+00:00",
            reported_by=officer["id"], location=quiet["site_name"], org_id=1)

        from sheplatform.modules.benchmark.data_service import site_benchmark
        rows = site_benchmark(db, org_id=1)

        busy_row = next(r for r in rows if r["site_code"] == "BUSY")
        quiet_row = next(r for r in rows if r["site_code"] == "QUIET")
        assert busy_row["incidents"] == 3
        assert quiet_row["incidents"] == 1
        assert busy_row["rank"] < quiet_row["rank"]  # busier = worse = lower rank number

    def test_band_assignment(self, db):
        officer = _mk_user(db, "she_officer", "bm2@test.com")
        hot = _mk_site(db, "HOT", "Hot Site", "Bulawayo")

        from sheplatform.modules.incidents.data_service import create_incident
        for i in range(4):
            create_incident(
                db, title=f"Hot {i}", description="x", severity="high",
                incident_type="accident", occurred_at="2026-08-01T10:00:00+00:00",
                reported_by=officer["id"], location=hot["site_name"], org_id=1)

        from sheplatform.modules.benchmark.data_service import site_benchmark, benchmark_summary
        rows = site_benchmark(db, org_id=1)
        hot_row = next(r for r in rows if r["site_code"] == "HOT")
        assert hot_row["band"] == "Red"  # 4 incidents x 3 = 12 >= 6

        summary = benchmark_summary(db, org_id=1)
        assert summary["red_sites"] >= 1
        assert summary["total_sites"] >= 1

    def test_empty_db_is_safe(self, db):
        from sheplatform.modules.benchmark.data_service import site_benchmark
        rows = site_benchmark(db)  # no org_id -> fail closed, must not crash
        assert isinstance(rows, list)

    def test_no_org_id_returns_empty_not_all_sites(self, db):
        """Regression: site_benchmark(org_id=None) must fail closed, not leak every
        tenant's sites (was: sites query had no org_id filter at all)."""
        _mk_site(db, "LEAK", "Leaky Site", "Harare", org_id=1)
        from sheplatform.modules.benchmark.data_service import site_benchmark, benchmark_summary
        assert site_benchmark(db, org_id=None) == []
        assert benchmark_summary(db, org_id=None)["sites"] == []


class TestBenchmarkOrgIsolationHTTP:
    """Regression for the cross-tenant leak found while auditing FROM sites queries for the
    C1 map feature: site_benchmark() accepted org_id but never applied it (not on the sites
    query, not on any of the per-site incident/observation/inspection/corrective_action/
    chemical count queries), and api_summary() never even extracted org_id from the request
    to pass it through. A test that calls data_service functions directly would not have
    caught the route-level break, so this goes through the real HTTP route + capability gate.
    """

    def _login(self, client, email) -> str:
        resp = client.post("/login", data={"email": email, "password": "Test1234!"})
        assert resp.status_code in (200, 303), f"login failed for {email}: {resp.status_code}"
        return client.cookies.get("she_csrf", "")

    def test_org_1_never_sees_org_2_sites_or_counts(self, db):
        db.execute(
            "INSERT INTO organisations (id, name, slug) VALUES (2, 'Org 2', 'org-2') ON CONFLICT DO NOTHING")
        db.commit()

        user_a = _mk_user(db, "she_officer", "http-bm-a@test.com", org_id=1)
        user_b = _mk_user(db, "she_officer", "http-bm-b@test.com", org_id=2)

        site_a = _mk_site(db, "ORGA-SITE", "Org A Headquarters", "Harare", org_id=1)
        site_b = _mk_site(db, "ORGB-SITE", "Org B Depot", "Bulawayo", org_id=2)

        from sheplatform.modules.incidents.data_service import create_incident
        create_incident(
            db, title="A incident", description="x", severity="high",
            incident_type="accident", occurred_at="2026-08-01T10:00:00+00:00",
            reported_by=user_a["id"], location=site_a["site_name"], org_id=1)
        for i in range(2):
            create_incident(
                db, title=f"B incident {i}", description="x", severity="high",
                incident_type="accident", occurred_at="2026-08-01T10:00:00+00:00",
                reported_by=user_b["id"], location=site_b["site_name"], org_id=2)

        from sheplatform.main import app

        client_a = TestClient(app)
        self._login(client_a, "http-bm-a@test.com")
        resp_a = client_a.get("/benchmark/api/summary")
        assert resp_a.status_code == 200
        data_a = resp_a.json()

        site_codes_a = {s["site_code"] for s in data_a["sites"]}
        assert site_codes_a == {"ORGA-SITE"}
        assert "ORGB-SITE" not in site_codes_a
        assert not any("Org B" in s["site_name"] for s in data_a["sites"])
        assert not any(s["city"] == "Bulawayo" for s in data_a["sites"])
        assert data_a["total_sites"] == 1

        org_a_row = data_a["sites"][0]
        assert org_a_row["incidents"] == 1  # must not include org B's 2 incidents

        # symmetric check: org B must not see org A's data either
        client_b = TestClient(app)
        self._login(client_b, "http-bm-b@test.com")
        resp_b = client_b.get("/benchmark/api/summary")
        assert resp_b.status_code == 200
        data_b = resp_b.json()

        site_codes_b = {s["site_code"] for s in data_b["sites"]}
        assert site_codes_b == {"ORGB-SITE"}
        assert "ORGA-SITE" not in site_codes_b
        assert data_b["total_sites"] == 1
        assert data_b["sites"][0]["incidents"] == 2
