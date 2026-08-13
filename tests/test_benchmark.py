"""Site benchmarking tests."""
from __future__ import annotations


def _mk_user(db, role, email):
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "F", "L", role, 1),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _mk_site(db, code, name, city):
    db.execute("INSERT INTO sites (site_code, site_name, city) VALUES (%s, %s, %s)",
               (code, name, city))
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
                reported_by=officer["id"], location=busy["site_name"])
        create_incident(
            db, title="Quiet incident", description="x", severity="low",
            incident_type="accident", occurred_at="2026-08-01T10:00:00+00:00",
            reported_by=officer["id"], location=quiet["site_name"])

        from sheplatform.modules.benchmark.data_service import site_benchmark
        rows = site_benchmark(db)

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
                reported_by=officer["id"], location=hot["site_name"])

        from sheplatform.modules.benchmark.data_service import site_benchmark, benchmark_summary
        rows = site_benchmark(db)
        hot_row = next(r for r in rows if r["site_code"] == "HOT")
        assert hot_row["band"] == "Red"  # 4 incidents x 3 = 12 >= 6

        summary = benchmark_summary(db)
        assert summary["red_sites"] >= 1
        assert summary["total_sites"] >= 1

    def test_empty_db_is_safe(self, db):
        from sheplatform.modules.benchmark.data_service import site_benchmark
        rows = site_benchmark(db)  # no sites - must not crash
        assert isinstance(rows, list)
