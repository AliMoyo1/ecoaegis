"""Launcher module: admin routes (user management, guide 7)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core import auth
from sheplatform.core.audit import log_audit
from sheplatform.core.middleware import require_capability
from sheplatform.core.rbac import ROLES
from sheplatform.database import get_db
from sheplatform.templating import templates

router = APIRouter(prefix="/admin")


@router.get("/api/users")
@require_capability("admin.users.manage")
async def users_api(request: Request):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, email, first_name, last_name, role_key FROM users "
            "WHERE is_active = TRUE ORDER BY first_name").fetchall()
        return JSONResponse({"users": [dict(r) for r in rows]})
    finally:
        db.close()


@router.get("/users", response_class=HTMLResponse)
@require_capability("admin.users.manage")
async def users_list(request: Request):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, email, first_name, last_name, role_key, org_id, is_active, last_login "
            "FROM users ORDER BY id"
        ).fetchall()
        return templates.TemplateResponse(request, "users.html",
                                          {"users": [dict(r) for r in rows], "roles": ROLES})
    finally:
        db.close()


@router.post("/users/create")
@require_capability("admin.users.manage")
async def user_create(request: Request,
                      email: str = Form(...),
                      first_name: str = Form(...),
                      last_name: str = Form(...),
                      password: str = Form(...),
                      role_key: str = Form(...),
                      phone: str = Form("")):
    db = get_db()
    try:
        hashed = auth.hash_password(password)
        db.execute(
            "INSERT INTO users (email, password_hash, first_name, last_name, phone, role_key) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (email, hashed, first_name, last_name, phone, role_key),
        )
        db.commit()
        log_audit(db, request.state.user["id"], None, "user.create", "users",
                  new_value={"email": email, "role_key": role_key})
        return RedirectResponse(url="/admin/users", status_code=303)
    finally:
        db.close()
