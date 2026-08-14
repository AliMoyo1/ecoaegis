"""Regression: get_capa_default_assignee must be org-scoped (review finding #4).

Previously it selected the first she_officer in the whole users table, ignoring
its org_id argument, so a corrective action could be auto-assigned to an officer
in a different organisation.
"""
from __future__ import annotations

from sheplatform.core.auth import hash_password
from sheplatform.core.rbac import get_capa_default_assignee


def _mk_org(db, slug):
    db.execute("INSERT INTO organisations (name, slug) VALUES (%s, %s)", (slug, slug))
    db.commit()
    return db.execute("SELECT id FROM organisations WHERE slug = %s", (slug,)).fetchone()["id"]


def _mk_officer(db, email, org_id):
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id, is_active) "
        "VALUES (%s, %s, %s, %s, 'she_officer', %s, TRUE)",
        (email, hash_password("Test1234!"), "O", "F", org_id))
    db.commit()
    return db.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()["id"]


def test_assignee_is_scoped_to_the_given_org(db):
    org_a = _mk_org(db, "org-a")
    org_b = _mk_org(db, "org-b")
    officer_a = _mk_officer(db, "officer_a@test.com", org_a)
    officer_b = _mk_officer(db, "officer_b@test.com", org_b)

    # org B must get B's officer, never A's (even though A was inserted first).
    assert get_capa_default_assignee(db, org_b) == officer_b
    assert get_capa_default_assignee(db, org_a) == officer_a


def test_assignee_none_when_org_has_no_officer(db):
    _mk_org(db, "org-empty")
    org_empty = db.execute("SELECT id FROM organisations WHERE slug = 'org-empty'").fetchone()["id"]
    # An officer exists, but in a different org.
    other = _mk_org(db, "org-other")
    _mk_officer(db, "officer_other@test.com", other)
    assert get_capa_default_assignee(db, org_empty) is None
