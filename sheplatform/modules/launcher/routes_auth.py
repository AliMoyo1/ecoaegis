"""Launcher module: auth routes (guide 7)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from sheplatform.core import auth
from sheplatform.core.middleware import SESSION_COOKIE, get_current_user, require_auth
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
        user = auth.get_user_by_email(db, email)
        if user is None or not auth.verify_password(password, user["password_hash"]):
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
