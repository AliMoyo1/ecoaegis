"""Launcher module: auth routes (guide 7)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from sheplatform.core import auth
from sheplatform.core.middleware import (
    CSRF_COOKIE, SESSION_COOKIE, get_current_user, make_csrf_token, require_auth)
from sheplatform.core.rbac import ROLES
from sheplatform.database import get_db
from sheplatform.templating import templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html",
                                      {"roles": ROLES})


@router.post("/login")
async def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    db = get_db()
    try:
        ip = request.client.host if request.client else ""
        # Audit S4 fix: 5 failed attempts / 5 min per IP. Checked before touching
        # bcrypt so a locked-out caller can't use timing to probe credentials.
        if auth.is_login_rate_limited(db, ip):
            return templates.TemplateResponse(
                request, "login.html",
                {"roles": ROLES, "error": "Too many login attempts. Try again in a few minutes."},
                status_code=429)
        user = auth.get_user_by_email(db, email)
        if user is None or not auth.verify_password(password, user["password_hash"]):
            auth.record_failed_login(db, ip)
            return templates.TemplateResponse(request, "login.html",
                                              {"roles": ROLES, "error": "Invalid email or password"})
        if not user["is_active"]:
            return templates.TemplateResponse(request, "login.html",
                                              {"roles": ROLES, "error": "Account disabled"})
        raw = auth.create_session(db, user["id"],
                                  ip=request.client.host if request.client else "",
                                  user_agent=request.headers.get("user-agent", ""))
        auth.update_last_login(db, user["id"])
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(SESSION_COOKIE, raw, httponly=True, samesite="strict",
                            max_age=24 * 60 * 60, secure=request.url.scheme == "https")
        # CSRF double-submit cookie (audit fix: CSRF code was dead - never enforced)
        csrf_token = make_csrf_token()
        response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, samesite="strict",
                            max_age=24 * 60 * 60, secure=request.url.scheme == "https")
        return response
    finally:
        db.close()


@router.post("/logout")
@require_auth
async def logout(request: Request):
    raw = request.cookies.get(SESSION_COOKIE)
    db = get_db()
    try:
        auth.destroy_session(db, raw)
    finally:
        db.close()
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ---- Increment B: self-service session / device management (any user) ----

@router.get("/account/sessions")
@require_auth
async def my_sessions(request: Request):
    """List my own active sessions (device = user-agent, IP), flag the current."""
    current = request.state.user["session_id"]
    db = get_db()
    try:
        sessions = auth.list_sessions(db, request.state.user["id"])
        for s in sessions:
            s["is_current"] = (s["id"] == current)
        return JSONResponse({"ok": True, "sessions": sessions})
    finally:
        db.close()


@router.delete("/account/sessions/{session_id}")
@require_auth
async def revoke_my_session(request: Request, session_id: int):
    """Revoke one of my own sessions (scoped to me, so I can't touch others')."""
    db = get_db()
    try:
        ok = auth.revoke_session(db, session_id, request.state.user["id"])
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)
    finally:
        db.close()


@router.post("/account/sessions/revoke-others")
@require_auth
async def revoke_my_other_sessions(request: Request):
    """Sign out everywhere except the current session."""
    db = get_db()
    try:
        n = auth.revoke_other_sessions(db, request.state.user["id"],
                                       request.state.user["session_id"])
        return JSONResponse({"ok": True, "revoked": n})
    finally:
        db.close()


# ---- Increment C: password lifecycle ----

@router.get("/account/change-password", response_class=HTMLResponse)
@require_auth
async def change_password_page(request: Request):
    return templates.TemplateResponse(request, "change_password.html",
                                      {"user": request.state.user})


@router.post("/account/change-password", response_class=HTMLResponse)
@require_auth
async def change_password_submit(request: Request, current_password: str = Form(...),
                                 new_password: str = Form(...),
                                 confirm_password: str = Form(...)):
    db = get_db()
    try:
        uid = request.state.user["id"]
        full = auth.get_user_by_id(db, uid)
        if not auth.verify_password(current_password, full["password_hash"]):
            return templates.TemplateResponse(request, "change_password.html",
                {"user": request.state.user, "error": "Current password is incorrect"},
                status_code=400)
        if new_password != confirm_password:
            return templates.TemplateResponse(request, "change_password.html",
                {"user": request.state.user, "error": "New passwords do not match"},
                status_code=400)
        res = auth.set_password(db, uid, new_password)
        if not res["ok"]:
            return templates.TemplateResponse(request, "change_password.html",
                {"user": request.state.user, "error": res["message"]}, status_code=400)
        return RedirectResponse(url="/", status_code=303)  # must_change flag now cleared
    finally:
        db.close()


@router.get("/auth/reset", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = ""):
    db = get_db()
    try:
        valid = auth.verify_auth_token(db, token, "reset") is not None
        return templates.TemplateResponse(request, "reset_password.html",
                                          {"token": token, "valid": valid})
    finally:
        db.close()


@router.post("/auth/reset", response_class=HTMLResponse)
async def reset_password_submit(request: Request, token: str = Form(...),
                                new_password: str = Form(...),
                                confirm_password: str = Form(...)):
    db = get_db()
    try:
        rec = auth.verify_auth_token(db, token, "reset")
        if rec is None:
            return templates.TemplateResponse(request, "reset_password.html",
                {"token": token, "valid": False,
                 "error": "This reset link is invalid or has expired"}, status_code=400)
        if new_password != confirm_password:
            return templates.TemplateResponse(request, "reset_password.html",
                {"token": token, "valid": True, "error": "Passwords do not match"},
                status_code=400)
        res = auth.set_password(db, rec["user_id"], new_password)
        if not res["ok"]:
            return templates.TemplateResponse(request, "reset_password.html",
                {"token": token, "valid": True, "error": res["message"]}, status_code=400)
        auth.consume_auth_token(db, rec["id"])
        return RedirectResponse(url="/login", status_code=303)
    finally:
        db.close()


# ---- Increment D: invitation acceptance (public, tokenized) ----

@router.get("/auth/accept-invite", response_class=HTMLResponse)
async def accept_invite_page(request: Request, token: str = ""):
    db = get_db()
    try:
        rec = auth.verify_auth_token(db, token, "invite")
        ctx = {"token": token, "valid": rec is not None}
        if rec:
            u = auth.get_user_by_id(db, rec["user_id"])
            ctx["email"] = u["email"] if u else ""
        return templates.TemplateResponse(request, "accept_invite.html", ctx)
    finally:
        db.close()


@router.post("/auth/accept-invite", response_class=HTMLResponse)
async def accept_invite_submit(request: Request, token: str = Form(...),
                               new_password: str = Form(...),
                               confirm_password: str = Form(...)):
    db = get_db()
    try:
        rec = auth.verify_auth_token(db, token, "invite")
        if rec is None:
            return templates.TemplateResponse(request, "accept_invite.html",
                {"token": token, "valid": False,
                 "error": "This invitation is invalid or has expired"}, status_code=400)
        if new_password != confirm_password:
            return templates.TemplateResponse(request, "accept_invite.html",
                {"token": token, "valid": True, "error": "Passwords do not match"},
                status_code=400)
        res = auth.set_password(db, rec["user_id"], new_password)
        if not res["ok"]:
            return templates.TemplateResponse(request, "accept_invite.html",
                {"token": token, "valid": True, "error": res["message"]}, status_code=400)
        auth.activate_user(db, rec["user_id"])  # pending -> active now it has a real password
        auth.consume_auth_token(db, rec["id"])
        return RedirectResponse(url="/login", status_code=303)
    finally:
        db.close()
