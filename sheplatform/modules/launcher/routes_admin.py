"""Launcher module: admin routes (user management, guide 7)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

from sheplatform.core import auth
from sheplatform.core.audit import log_audit, verify_audit_chain
from sheplatform.core.middleware import require_capability
from sheplatform.core.retention import retention_report, set_retention_policy
from sheplatform.core.rbac import CAPABILITIES, ROLES
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


# ---- Increment A: user lifecycle + per-user audit + role preview ----

def _target_user(db, user_id: int, actor_org_id: int | None):
    """Fetch a user only within the actor's org (tenant isolation, fail closed)."""
    if not actor_org_id:
        return None
    row = db.execute("SELECT * FROM users WHERE id = %s AND org_id = %s",
                     (user_id, actor_org_id)).fetchone()
    return dict(row) if row else None


def _active_super_admins(db) -> int:
    row = db.execute(
        "SELECT COUNT(*) AS c FROM users WHERE role_key = 'super_admin' AND is_active = TRUE"
    ).fetchone()
    return int(row["c"])


@router.post("/users/{user_id}/deactivate")
@require_capability("admin.users.manage")
async def user_deactivate(request: Request, user_id: int):
    db = get_db()
    try:
        actor = request.state.user
        target = _target_user(db, user_id, actor.get("org_id"))
        if target is None:
            return JSONResponse({"ok": False, "message": "user not found"}, status_code=404)
        if user_id == actor["id"]:
            return JSONResponse({"ok": False, "message": "you cannot deactivate your own account"},
                                status_code=400)
        if target["role_key"] == "super_admin" and _active_super_admins(db) <= 1:
            return JSONResponse({"ok": False, "message": "cannot deactivate the last active super admin"},
                                status_code=400)
        db.execute("UPDATE users SET is_active = FALSE, updated_at = %s WHERE id = %s AND org_id = %s",
                   (_now(), user_id, actor.get("org_id")))
        db.commit()
        revoked = auth.revoke_user_sessions(db, user_id)  # kill live sessions immediately
        log_audit(db, actor["id"], actor.get("org_id"), "user.deactivate", "users", user_id,
                  old_value={"is_active": True}, new_value={"is_active": False, "sessions_revoked": revoked})
        return JSONResponse({"ok": True, "sessions_revoked": revoked})
    finally:
        db.close()


@router.post("/users/{user_id}/reactivate")
@require_capability("admin.users.manage")
async def user_reactivate(request: Request, user_id: int):
    db = get_db()
    try:
        actor = request.state.user
        target = _target_user(db, user_id, actor.get("org_id"))
        if target is None:
            return JSONResponse({"ok": False, "message": "user not found"}, status_code=404)
        db.execute("UPDATE users SET is_active = TRUE, updated_at = %s WHERE id = %s AND org_id = %s",
                   (_now(), user_id, actor.get("org_id")))
        db.commit()
        log_audit(db, actor["id"], actor.get("org_id"), "user.reactivate", "users", user_id,
                  old_value={"is_active": False}, new_value={"is_active": True})
        return JSONResponse({"ok": True})
    finally:
        db.close()


@router.post("/users/{user_id}/role")
@require_capability("admin.users.manage")
async def user_set_role(request: Request, user_id: int, role_key: str = Form(...)):
    db = get_db()
    try:
        actor = request.state.user
        if role_key not in ROLES:
            return JSONResponse({"ok": False, "message": "unknown role"}, status_code=400)
        target = _target_user(db, user_id, actor.get("org_id"))
        if target is None:
            return JSONResponse({"ok": False, "message": "user not found"}, status_code=404)
        if (target["role_key"] == "super_admin" and role_key != "super_admin"
                and _active_super_admins(db) <= 1):
            return JSONResponse({"ok": False, "message": "cannot demote the last active super admin"},
                                status_code=400)
        old_role = target["role_key"]
        db.execute("UPDATE users SET role_key = %s, updated_at = %s WHERE id = %s AND org_id = %s",
                   (role_key, _now(), user_id, actor.get("org_id")))
        db.commit()
        # role_key is read live from users on every request (get_session_user), so
        # the change takes effect immediately without revoking sessions.
        log_audit(db, actor["id"], actor.get("org_id"), "user.role_change", "users", user_id,
                  old_value={"role_key": old_role}, new_value={"role_key": role_key})
        return JSONResponse({"ok": True, "role_key": role_key})
    finally:
        db.close()


@router.get("/users/{user_id}/audit")
@require_capability("admin.users.manage")
async def user_audit_history(request: Request, user_id: int, limit: int = 100):
    """Per-user audit history: what the user did + changes made to their account."""
    db = get_db()
    try:
        actor = request.state.user
        target = _target_user(db, user_id, actor.get("org_id"))
        if target is None:
            return JSONResponse({"ok": False, "message": "user not found"}, status_code=404)
        rows = db.execute(
            "SELECT id, user_id, action, entity_type, entity_id, created_at "
            "FROM audit_log WHERE org_id = %s AND (user_id = %s OR "
            "(entity_type = 'users' AND entity_id = %s)) ORDER BY id DESC LIMIT %s",
            (actor.get("org_id"), user_id, user_id, min(max(limit, 1), 500))).fetchall()
        return JSONResponse({"ok": True, "history": [dict(r) for r in rows]})
    finally:
        db.close()


@router.get("/api/roles/{role_key}/preview")
@require_capability("admin.users.manage")
async def role_preview(request: Request, role_key: str):
    """Read-only preview of the capabilities a role grants (from rbac.CAPABILITIES)."""
    if role_key not in ROLES:
        return JSONResponse({"ok": False, "message": "unknown role"}, status_code=404)
    caps = sorted(cap for cap, roles in CAPABILITIES.items() if role_key in roles)
    return JSONResponse({"ok": True, "role_key": role_key, "label": ROLES[role_key],
                         "capabilities": caps})


# ---- Increment B: admin session/device oversight (org-scoped) ----

@router.get("/api/users/{user_id}/sessions")
@require_capability("admin.users.manage")
async def user_sessions(request: Request, user_id: int):
    db = get_db()
    try:
        if _target_user(db, user_id, request.state.user.get("org_id")) is None:
            return JSONResponse({"ok": False, "message": "user not found"}, status_code=404)
        return JSONResponse({"ok": True, "sessions": auth.list_sessions(db, user_id)})
    finally:
        db.close()


@router.delete("/api/users/{user_id}/sessions/{session_id}")
@require_capability("admin.users.manage")
async def user_revoke_session(request: Request, user_id: int, session_id: int):
    db = get_db()
    try:
        actor = request.state.user
        if _target_user(db, user_id, actor.get("org_id")) is None:
            return JSONResponse({"ok": False, "message": "user not found"}, status_code=404)
        ok = auth.revoke_session(db, session_id, user_id)  # scoped to that user
        if ok:
            log_audit(db, actor["id"], actor.get("org_id"), "user.session_revoke", "users", user_id,
                      new_value={"session_id": session_id})
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)
    finally:
        db.close()


@router.post("/users/{user_id}/sessions/revoke-all")
@require_capability("admin.users.manage")
async def user_revoke_all_sessions(request: Request, user_id: int):
    """Force sign-out of all a user's sessions without deactivating them
    (e.g. suspected compromise)."""
    db = get_db()
    try:
        actor = request.state.user
        if _target_user(db, user_id, actor.get("org_id")) is None:
            return JSONResponse({"ok": False, "message": "user not found"}, status_code=404)
        n = auth.revoke_user_sessions(db, user_id)
        log_audit(db, actor["id"], actor.get("org_id"), "user.sessions_revoke_all", "users", user_id,
                  new_value={"sessions_revoked": n})
        return JSONResponse({"ok": True, "revoked": n})
    finally:
        db.close()


@router.post("/users/{user_id}/reset-password")
@require_capability("admin.users.manage")
async def user_reset_password(request: Request, user_id: int):
    """Admin-triggered reset: issue a single-use token link, force a change,
    revoke live sessions, and email the link (console provider in dev)."""
    from sheplatform.config import settings
    from sheplatform.core.notifications import queue_email
    db = get_db()
    try:
        actor = request.state.user
        target = _target_user(db, user_id, actor.get("org_id"))
        if target is None:
            return JSONResponse({"ok": False, "message": "user not found"}, status_code=404)
        raw = auth.issue_auth_token(db, user_id, "reset", auth.RESET_TOKEN_TTL_HOURS)
        db.execute("UPDATE users SET must_change_password = TRUE, updated_at = %s "
                   "WHERE id = %s AND org_id = %s", (_now(), user_id, actor.get("org_id")))
        db.commit()
        revoked = auth.revoke_user_sessions(db, user_id)
        link = f"/auth/reset?token={raw}"
        queue_email(db, target["email"], "EcoAegis password reset",
                    f"A password reset was requested. Set a new password here: {link} "
                    f"(link expires in {auth.RESET_TOKEN_TTL_HOURS} hour(s)).")
        log_audit(db, actor["id"], actor.get("org_id"), "user.password_reset", "users", user_id,
                  new_value={"sessions_revoked": revoked})
        resp = {"ok": True, "sessions_revoked": revoked}
        if settings.DEBUG:
            resp["reset_link"] = link  # dev convenience only; never exposed in prod
        return JSONResponse(resp)
    finally:
        db.close()
