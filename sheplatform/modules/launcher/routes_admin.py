"""Launcher module: admin routes (user management, guide 7)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from sheplatform.core import auth
from sheplatform.core.audit import log_audit, verify_audit_chain
from sheplatform.core.middleware import require_capability
from sheplatform.core.retention import retention_report, set_retention_policy
from sheplatform.core.rbac import ROLES
from sheplatform.database import get_db
from sheplatform.templating import templates

router = APIRouter(prefix="/admin")


@router.get("/api/audit/verify")
@require_capability("admin.settings.manage")
async def audit_verify(request: Request):
    """NFR-SHE-004: report audit-log integrity (tamper-evident hash chain).

    super_admin only. Returns {ok, checked, first_break} - first_break names
    the earliest tampered/deleted row when the chain is broken.
    """
    db = get_db()
    try:
        return JSONResponse(verify_audit_chain(db))
    finally:
        db.close()


@router.get("/api/retention")
@require_capability("admin.settings.manage")
async def retention_report_api(request: Request):
    """NFR-SHE-003: per-record-type retention report (super_admin)."""
    db = get_db()
    try:
        return JSONResponse({"retention": retention_report(db)})
    finally:
        db.close()


@router.post("/api/retention")
@require_capability("admin.settings.manage")
async def retention_set_api(request: Request,
                            record_type: str = Form(...),
                            retention_years: int = Form(...),
                            description: str = Form("")):
    """NFR-SHE-003: configure a record type's minimum retention (super_admin)."""
    db = get_db()
    try:
        result = set_retention_policy(
            db, record_type, retention_years,
            updated_by=request.state.user["id"], description=description)
        if not result["ok"]:
            return JSONResponse(result, status_code=400)
        log_audit(db, request.state.user["id"], request.state.user.get("org_id"),
                  "retention.policy.set", "retention_policies", None,
                  new_value={"record_type": record_type, "retention_years": retention_years})
        return JSONResponse(result)
    finally:
        db.close()


@router.get("/api/users")
@require_capability("admin.users.manage")
async def users_api(request: Request):
    db = get_db()
    try:
        org_id = request.state.user.get("org_id")
        if not org_id:
            return JSONResponse({"users": []})
        rows = db.execute(
            "SELECT id, email, first_name, last_name, role_key FROM users "
            "WHERE is_active = TRUE AND org_id = %s ORDER BY first_name",
            (org_id,),
        ).fetchall()
        return JSONResponse({"users": [dict(r) for r in rows]})
    finally:
        db.close()


@router.get("/users", response_class=HTMLResponse)
@require_capability("admin.users.manage")
async def users_list(request: Request):
    db = get_db()
    try:
        org_id = request.state.user.get("org_id")
        rows = db.execute(
            "SELECT id, email, first_name, last_name, role_key, org_id, is_active, "
            "mfa_enabled, last_login "
            "FROM users WHERE org_id = %s ORDER BY id",
            (org_id,),
        ).fetchall() if org_id else []
        users = [dict(row) for row in rows]
        for user in users:
            last_login = user.get("last_login")
            user["last_login_label"] = str(last_login)[:10] if last_login else "Never"
            user["role_label"] = ROLES.get(user.get("role_key"), user.get("role_key", "Unknown"))

        summary = {
            "total": len(users),
            "active": sum(bool(user.get("is_active")) for user in users),
            "privileged": sum(
                user.get("role_key") in {"super_admin", "she_manager", "she_hod"}
                for user in users
            ),
            "mfa_enabled": sum(bool(user.get("mfa_enabled")) for user in users),
        }
        return templates.TemplateResponse(
            request,
            "users.html",
            {
                "user": request.state.user,
                "users": users,
                "roles": ROLES,
                "user_summary": summary,
            },
        )
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
        # tenant-isolation: new users must belong to the creator's organisation
        org_id = request.state.user.get("org_id")
        if not org_id:
            return JSONResponse({"ok": False, "message": "cannot create user without organisation"},
                                status_code=400)
        hashed = auth.hash_password(password)
        db.execute(
            "INSERT INTO users (email, password_hash, first_name, last_name, phone, role_key, org_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (email, hashed, first_name, last_name, phone, role_key, org_id),
        )
        db.commit()
        log_audit(db, request.state.user["id"], org_id, "user.create", "users",
                  new_value={"email": email, "role_key": role_key})
        return RedirectResponse(url="/admin/users", status_code=303)
    finally:
        db.close()
