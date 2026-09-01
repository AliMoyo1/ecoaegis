"""Real-PostgreSQL backend regression tests.

The rest of the suite runs on SQLite (conftest forces DATABASE_URL=""), so the
psycopg2 path (_PgConn) was never exercised until these tests existed - which is
exactly how the missing cursor_factory shipped undetected. These tests only run
when TEST_DATABASE_URL points at a disposable PostgreSQL instance, and are
skipped otherwise, so the default SQLite-only run is unchanged.

Run locally against a throwaway container:
    docker run -d -e POSTGRES_PASSWORD=x -e POSTGRES_DB=y -p 55432:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql://postgres:x@127.0.0.1:55432/y \
        .venv/Scripts/python.exe -m pytest tests/test_postgres_backend.py -v
"""
from __future__ import annotations

import os

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to a disposable PostgreSQL to run the pg backend tests",
)


@pytest.fixture
def pg(monkeypatch):
    """A get_db() connection wired to the real PostgreSQL backend.

    Disposes the thread-local pool afterwards so a live pg pool never leaks
    into the SQLite-based tests that share the process.
    """
    import sheplatform.database as database

    monkeypatch.setattr("sheplatform.config.settings.DATABASE_URL", TEST_DATABASE_URL)
    assert database.settings.is_postgres()
    database.init_db()  # idempotent (CREATE TABLE IF NOT EXISTS); proves DDL builds on pg
    conn = database.get_db()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()
        pool = getattr(database._local, "pg_pool", None)
        if pool is not None:
            pool.closeall()
            database._local.pg_pool = None


def test_string_key_access(pg):
    assert pg.execute("SELECT 7 AS answer").fetchone()["answer"] == 7


def test_literal_percent_without_parameters(pg):
    row = pg.execute(
        "SELECT 'map.provider.warning' LIKE 'map.provider.%' AS matched"
    ).fetchone()
    assert row["matched"] is True


def test_integer_index_access(pg):
    # ~19 call sites read COUNT(*) via fetchone()[0]; this is the pattern
    # RealDictCursor would have broken and DictCursor preserves.
    assert pg.execute("SELECT COUNT(*) FROM organisations").fetchone()[0] >= 0


def test_dict_of_row(pg):
    row = pg.execute("SELECT 1 AS a, 'x' AS b").fetchone()
    assert dict(row) == {"a": 1, "b": "x"}


def test_rowcount_is_reported(pg):
    # coordinate_import_service.commit_coordinate_import relies on cursor.rowcount
    # to make its atomic-update race check work (PR #16).
    pg.execute("INSERT INTO organisations (name, slug) VALUES ('rc','rc-test') "
               "ON CONFLICT (slug) DO NOTHING")
    cur = pg.execute("UPDATE organisations SET name = name WHERE slug = %s", ("rc-test",))
    assert cur.rowcount == 1
    pg.rollback()


def test_fetchone_returns_none_on_miss(pg):
    assert pg.execute("SELECT id FROM organisations WHERE slug = %s",
                      ("definitely-absent",)).fetchone() is None


def test_fetchall_rows_are_dict_accessible(pg):
    pg.execute("INSERT INTO organisations (name, slug) VALUES ('it','iter-test') "
               "ON CONFLICT (slug) DO NOTHING")
    pg.commit()
    slugs = [r["slug"] for r in pg.execute("SELECT slug FROM organisations").fetchall()]
    assert "iter-test" in slugs


# --- portable SQL idioms that replaced SQLite-only constructs (layer 2) ---

def test_insert_returning_id(pg):
    # Replaces last_insert_rowid() (notifications, attachments, esg uploads).
    new_id = pg.execute(
        "INSERT INTO organisations (name, slug) VALUES ('ret','returning-test') "
        "RETURNING id").fetchone()["id"]
    pg.commit()
    assert isinstance(new_id, int) and new_id > 0


def test_boolean_true_false_round_trip(pg):
    # Replaces `col = 1` / `col = 0` (breaks on PG as boolean = integer).
    pg.execute("INSERT INTO organisations (name, slug) VALUES ('b','bool-org') "
               "ON CONFLICT (slug) DO NOTHING")
    org = pg.execute("SELECT id FROM organisations WHERE slug = 'bool-org'").fetchone()["id"]
    pg.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id, is_active) "
        "VALUES ('boolflag@t.com','x','B','F','she_officer',%s, TRUE)", (org,))
    pg.execute("UPDATE users SET is_active = FALSE WHERE email = 'boolflag@t.com'")
    assert pg.execute("SELECT COUNT(*) FROM users WHERE email = 'boolflag@t.com' "
                      "AND is_active = FALSE").fetchone()[0] == 1
    assert pg.execute("SELECT COUNT(*) FROM users WHERE email = 'boolflag@t.com' "
                      "AND is_active = TRUE").fetchone()[0] == 0
    pg.rollback()


def test_on_conflict_do_nothing_is_idempotent(pg):
    # Replaces INSERT OR IGNORE (documents ack, seeds, conftest).
    pg.execute("INSERT INTO organisations (name, slug) VALUES ('c1','conflict-test') "
               "ON CONFLICT (slug) DO NOTHING")
    pg.execute("INSERT INTO organisations (name, slug) VALUES ('c2','conflict-test') "
               "ON CONFLICT (slug) DO NOTHING")
    pg.commit()
    assert pg.execute("SELECT COUNT(*) FROM organisations WHERE slug = 'conflict-test'"
                      ).fetchone()[0] == 1


def test_rowcount_on_delete(pg):
    # Replaces SELECT changes() (esg delete_mapping).
    pg.execute("INSERT INTO organisations (name, slug) VALUES ('d','delete-test') "
               "ON CONFLICT (slug) DO NOTHING")
    cur = pg.execute("DELETE FROM organisations WHERE slug = 'delete-test'")
    assert cur.rowcount == 1
    pg.rollback()
