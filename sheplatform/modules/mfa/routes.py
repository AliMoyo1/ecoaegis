"""MFA routes (audit fix): enrollment + login challenge + settings."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from sheplatform.core import auth, mfa
from sheplatform.core.middleware import (
    SESSION_COOKIE, get_current_user, require_auth)
from sheplatform.database import get_db
from sheplatform.templating import templates

router = APIRouter(prefix="/mfa", tags=["mfa"])


@router.get("/challenge", response_class=HTMLResponse)
async def challenge_page(request: Request):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request, "mfa/templates/challenge.html", {"user": user})


@router.post("/api/verify")
async def api_verify(request: Request, code: str = Form(...)):
    user = get_current_user(request)
    if user is None:
        return JSONResponse({"ok": False, "message": "not authenticated"}, status_code=401)
    db = get_db()
    try:
        result = mfa.verify(db, user["id"], code)
        if not result["ok"]:
            return JSONResponse(result, status_code=400)
        raw = request.cookies.get(SESSION_COOKIE)
        mfa.verify_session(db, user["id"], raw)
        return JSONResponse({"ok": True, "redirect": "/"})
    finally:
        db.close()


@router.get("/setup", response_class=HTMLResponse)
@require_auth
async def setup_page(request: Request):
    db = get_db()
    try:
        status = mfa.user_mfa_status(db, request.state.user["id"])
        return templates.TemplateResponse(request, "mfa/templates/setup.html",
                                          {"user": request.state.user, "mfa": status})
    finally:
        db.close()


@router.post("/api/enroll")
@require_auth
async def api_enroll(request: Request):
    db = get_db()
    try:
        user = request.state.user
        out = mfa.enroll(db, user["id"], user["email"])
        out["qr_svg"] = mfa.qr_svg(out["uri"])
        return JSONResponse({"ok": True, **out})
    finally:
        db.close()


@router.post("/api/confirm")
@require_auth
async def api_confirm(request: Request, code: str = Form(...)):
    db = get_db()
    try:
        result = mfa.confirm_enroll(db, request.state.user["id"], code)
        if not result["ok"]:
            return JSONResponse(result, status_code=400)
        # The valid code just proved possession, so mark THIS session verified
        # too - otherwise a forced-enrolment user (SEC-SHE-001) is bounced
        # straight to /mfa/challenge to re-enter a code they just entered.
        raw = request.cookies.get(SESSION_COOKIE)
        if raw:
            mfa.verify_session(db, request.state.user["id"], raw)
        return JSONResponse(result)
    finally:
        db.close()


@router.post("/api/disable")
@require_auth
async def api_disable(request: Request, code: str = Form(...)):
    """Disable MFA - requires a valid TOTP code (can't disable without proof)."""
    db = get_db()
    try:
        result = mfa.verify(db, request.state.user["id"], code)
        if not result["ok"]:
            return JSONResponse({"ok": False, "message": "invalid code"}, status_code=400)
        db.execute("UPDATE users SET mfa_enabled = FALSE, mfa_secret = NULL WHERE id = %s",
                   (request.state.user["id"],))
        db.execute("DELETE FROM mfa_backup_codes WHERE user_id = %s",
                   (request.state.user["id"],))
        db.commit()
        return JSONResponse({"ok": True, "message": "MFA disabled"})
    finally:
        db.close()
