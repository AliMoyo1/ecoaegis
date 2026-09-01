"""Authenticated User Management workspace regression tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(db):
    from sheplatform.core.auth import hash_password
    from sheplatform.main import app

    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            "admin-ui@test.com",
            hash_password("Test1234!"),
            "Workspace",
            "Admin",
            "super_admin",
            1,
        ),
    )
    db.commit()

    client = TestClient(app)
    login = client.post(
        "/login",
        data={"email": "admin-ui@test.com", "password": "Test1234!"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    yield client
    client.close()


def test_user_management_keeps_authenticated_shell_and_csrf(admin_client):
    response = admin_client.get("/admin/users")

    assert response.status_code == 200
    html = response.text
    assert '<aside class="sidebar"' in html
    assert 'data-sidebar-group="settings"' in html
    assert 'data-nav="/admin/users"' in html
    assert "Workspace Admin" in html
    assert 'data-input-tray-open="create-user-tray"' in html
    assert 'id="user-search"' in html

    create_form = html.split('action="/admin/users/create"', 1)[1].split("</form>", 1)[0]
    assert 'name="csrf_token"' in create_form
    assert 'value=""' not in create_form


def test_create_user_form_redirects_back_to_directory(admin_client):
    token = admin_client.cookies.get("she_csrf")
    assert token

    response = admin_client.post(
        "/admin/users/create",
        data={
            "csrf_token": token,
            "email": "new-user@test.com",
            "first_name": "New",
            "last_name": "User",
            "phone": "",
            "role_key": "she_officer",
            "password": "Another123!",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users"


def test_user_directory_is_scoped_to_signed_in_organisation(admin_client, db):
    from sheplatform.core.auth import hash_password

    db.execute(
        "INSERT INTO organisations (name, slug) VALUES (%s, %s)",
        ("Other Organisation", "other-organisation"),
    )
    other_org = db.execute(
        "SELECT id FROM organisations WHERE slug = %s",
        ("other-organisation",),
    ).fetchone()
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            "outside-tenant@test.com",
            hash_password("Test1234!"),
            "Outside",
            "Tenant",
            "she_officer",
            other_org["id"],
        ),
    )
    db.commit()

    page = admin_client.get("/admin/users")
    api = admin_client.get("/admin/api/users")

    assert page.status_code == 200
    assert "outside-tenant@test.com" not in page.text
    assert api.status_code == 200
    assert all(user["email"] != "outside-tenant@test.com" for user in api.json()["users"])
