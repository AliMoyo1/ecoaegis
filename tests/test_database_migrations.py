"""Column-migration coverage (database.py's *_COLUMNS lists + init_db()).

`CREATE TABLE IF NOT EXISTS` only creates a table on a database that does not
already have it; it is a no-op, columns and all, on a database where the
table already exists from a previous deploy. Adding a column to an existing
table's CREATE TABLE string alone therefore never reaches an already-running
deployment; it needs a matching ALTER TABLE entry, which is what the
`_COLUMNS` lists + PRAGMA-checked branch in init_db() are for.

This exact gap was found while building C1 (the geographic map): B5 (PR #7)
added three columns to `incidents` this way and never added the matching
migration entries, verified against this repo's own pre-B5 local dev
sheplatform.db (had none of the three columns even after upgrading to a
build that assumes they exist). Fixed alongside C1's own sites.latitude/
longitude, which was about to repeat the same gap. Nothing exercised this
migration path before, this test exists so it stays exercised.
"""
from __future__ import annotations

import os
import re
import sqlite3

import pytest

# This module tests the SQLite retrofit path specifically: it builds a real
# SQLite file, DROP COLUMNs to simulate an old deploy, and inspects results
# with PRAGMA. None of that applies to PostgreSQL (whose ALTER TABLE ADD
# COLUMN IF NOT EXISTS branch runs directly), so skip the module under PG.
pytestmark = pytest.mark.skipif(
    bool(os.getenv("TEST_DATABASE_URL")),
    reason="SQLite-specific migration mechanics (sqlite3.connect + PRAGMA + DROP COLUMN)")


SITE_RELATION_TABLES = ("permits", "inspections", "eia_projects", "emergency_events")


def _rebuild_without_site_id(con: sqlite3.Connection, table: str) -> None:
    """Recreate an empty current table in its pre-Phase-2 shape."""
    create_sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()[0]
    old_sql = re.sub(
        r"\n\s*site_id\s+INTEGER\s+REFERENCES\s+sites\(id\),?",
        "",
        create_sql,
        flags=re.IGNORECASE,
    )
    assert old_sql != create_sql
    con.execute(f'DROP TABLE "{table}"')
    con.execute(old_sql)


@pytest.fixture
def old_shaped_db(tmp_path, monkeypatch):
    """A SQLite file with the full current schema, then the B5/C1 columns
    dropped back off sites/incidents - i.e. exactly what a database that
    predates those two features looks like. Built from the real schema
    (not a hand-rolled stub) so every other column/index/FK those tables'
    CREATE INDEX statements depend on is genuinely present.
    """
    db_path = str(tmp_path / "old.db")
    monkeypatch.setattr("sheplatform.config.settings.DATABASE_URL", "")
    monkeypatch.setattr("sheplatform.config.settings.DB_PATH", db_path)

    from sheplatform.database import init_db
    init_db()

    con = sqlite3.connect(db_path)
    for index in (
        "idx_sites_org_lat_lng", "idx_incidents_org_lat_lng", "idx_incidents_org_site",
    ):
        con.execute(f'DROP INDEX IF EXISTS "{index}"')
    for column in ("immediate_actions", "estimated_cost", "witnesses"):
        con.execute(f"ALTER TABLE incidents DROP COLUMN {column}")
    for column in (
        "latitude", "longitude", "coordinate_source", "coordinate_accuracy_m",
        "coordinates_updated_at", "coordinates_updated_by", "geocode_provider",
        "geocode_place_id",
    ):
        con.execute(f"ALTER TABLE sites DROP COLUMN {column}")
    con.commit()
    con.execute("PRAGMA foreign_keys=OFF")
    for table in SITE_RELATION_TABLES:
        _rebuild_without_site_id(con, table)
    con.commit()
    con.execute("PRAGMA foreign_keys=ON")
    con.close()
    return db_path


def test_init_db_retrofits_missing_columns_on_an_existing_database(old_shaped_db):
    from sheplatform.database import init_db

    init_db()

    con = sqlite3.connect(old_shaped_db)
    site_cols = {r[1] for r in con.execute("PRAGMA table_info(sites)")}
    incident_cols = {r[1] for r in con.execute("PRAGMA table_info(incidents)")}
    con.close()

    assert {
        "latitude", "longitude", "coordinate_source", "coordinate_accuracy_m",
        "coordinates_updated_at", "coordinates_updated_by", "geocode_provider",
        "geocode_place_id",
    } <= site_cols
    assert {"immediate_actions", "estimated_cost", "witnesses"} <= incident_cols


@pytest.mark.parametrize("table", SITE_RELATION_TABLES)
def test_fresh_schema_has_site_relationship_foreign_keys(db, table):
    columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    foreign_keys = db.execute(f"PRAGMA foreign_key_list({table})").fetchall()

    assert "site_id" in columns
    assert any(row[2] == "sites" and row[3] == "site_id" and row[4] == "id"
               for row in foreign_keys)


def test_init_db_retrofits_site_relationships_on_existing_tables(old_shaped_db):
    from sheplatform.database import init_db

    init_db()

    con = sqlite3.connect(old_shaped_db)
    for table in SITE_RELATION_TABLES:
        columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        foreign_keys = con.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        assert "site_id" in columns
        assert any(row[2] == "sites" and row[3] == "site_id" and row[4] == "id"
                   for row in foreign_keys)
    con.close()


def test_init_db_is_idempotent_on_a_database_that_already_has_the_columns(old_shaped_db):
    """Running init_db() twice (every app restart) must not raise a
    duplicate-column error on either the fresh-CREATE-TABLE path or the
    ALTER-TABLE retrofit path."""
    from sheplatform.database import init_db

    init_db()
    init_db()  # must not raise


def test_init_db_creates_geospatial_indexes_after_retrofit(old_shaped_db):
    from sheplatform.database import init_db

    init_db()
    con = sqlite3.connect(old_shaped_db)
    indexes = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'")}
    con.close()
    assert {
        "idx_sites_org_lat_lng", "idx_incidents_org_lat_lng", "idx_incidents_org_site",
        "idx_permits_org_site", "idx_inspections_org_site", "idx_eia_projects_org_site",
        "idx_emergency_events_org_site", "idx_assets_org_site", "idx_observations_org_site",
    } <= indexes


def test_init_db_adds_map_measurement_and_import_tables_to_existing_database(old_shaped_db):
    from sheplatform.database import init_db

    con = sqlite3.connect(old_shaped_db)
    con.execute("DROP TABLE site_coordinate_import_rows")
    con.execute("DROP TABLE site_coordinate_imports")
    con.execute("DROP TABLE site_resolution_decisions")
    con.execute("DROP TABLE map_usage_metrics")
    con.execute("DROP TABLE map_provider_admissions")
    con.execute("DROP TABLE map_provider_monthly_usage")
    con.commit()
    con.close()

    init_db()

    con = sqlite3.connect(old_shaped_db)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    con.close()
    assert {"map_usage_metrics", "site_coordinate_imports",
            "site_coordinate_import_rows", "site_resolution_decisions",
            "map_provider_monthly_usage", "map_provider_admissions"} <= tables
