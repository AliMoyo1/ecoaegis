"""Security middleware: auth guards, CSRF, security headers (guide 5.5)."""
from __future__ import annotations

import functools
import hmac
import secrets
import urllib.parse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from sheplatform.core.auth import get_session_user
from sheplatform.core.rbac import has_capability
from sheplatform.database import get_db

SESSION_COOKIE = "she_session"
CSRF_COOKIE = "she_csrf"

# module -> capability prefix used to compute sidebar visibility flags
_SIDEBAR_MODULES = {
    "incidents": "incidents",
    "risks": "risk_register",
    "vendors": "vendors",
    "permits": "permits",
    "grievances": "grievances",
    "eia": "eia",
    "emergency": "emergency",
    "training": "training",
    "reports": "reports",
    "comms": "comms",
    "workplan": "workplan",
    "esg": "esg",
    "stakeholders": "stakeholder",
    "observations": "observations",
    "documents": "documents",
}


def with_nav_flags(user: dict) -> dict:
    """Attach can_* flags so templates can render the role-aware sidebar."""
    if not user:
        return user
    for flag, module in _SIDEBAR_MODULES.items():
        user[f"can_{flag}"] = has_capability(user, f"module.{module}.access")
    user["can_admin"] = has_capability(user, "admin.users.manage")
    user["can_documents_manage"] = has_capability(user, "documents.manage")
    return user


MFA_ALLOWED_PATHS = ("/mfa/challenge", "/mfa/api/verify", "/logout", "/static")


def mfa_challenge_required(request: Request, user: dict) -> bool:
    """True when the user has MFA enabled but this session is not yet verified."""
    if not user or not user.get("mfa_enabled"):
        return False
    if user.get("mfa_verified"):
        return False
    path = request.url.path
    return not any(path.startswith(p) for p in MFA_ALLOWED_PATHS)


def get_current_user(request: Request) -> dict | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    db = get_db()
    try:
        return get_session_user(db, raw)
    finally:
        db.close()


def _attach_csrf(user: dict, request: Request) -> dict:
    user["csrf_token"] = request.cookies.get(CSRF_COOKIE, "")
    return user


def require_auth(func):
    """Redirect to /login if no valid session (guide 5.5). MFA challenge if enabled."""
    @functools.wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        user = get_current_user(request)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        if mfa_challenge_required(request, user):
            return RedirectResponse(url="/mfa/challenge", status_code=303)
        request.state.user = _attach_csrf(with_nav_flags(user), request)
        return await func(request, *args, **kwargs)
    return wrapper


def require_capability(capability: str):
    """403 if the user lacks the capability (guide 5.5). MFA challenge if enabled."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            user = get_current_user(request)
            if user is None:
                return RedirectResponse(url="/login", status_code=303)
            if mfa_challenge_required(request, user):
                return RedirectResponse(url="/mfa/challenge", status_code=303)
            if not has_capability(user, capability):
                raise HTTPException(status_code=403, detail="Forbidden")
            request.state.user = _attach_csrf(with_nav_flags(user), request)
            return await func(request, *args, **kwargs)
        return wrapper
    return decorator


def require_module(module_name: str):
    return require_capability(f"module.{module_name}.access")


def validate_csrf(request: Request):
    """Constant-time compare of the CSRF token cookie vs form/header token."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    header_token = request.headers.get("X-CSRF-Token", "")
    form_token = ""
    if request.method == "POST":
        try:
            form = request._form if hasattr(request, "_form") else None
        except Exception:
            form = None
    if not header_token and cookie_token:
        # fall back to form field
        body = getattr(request, "state", None)
        body = getattr(body, "csrf_token", "") if body else ""
        form_token = body
    provided = header_token or form_token
    if not provided or not hmac.compare_digest(provided, cookie_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


async def security_headers_middleware(request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def make_csrf_token() -> str:
    return secrets.token_urlsafe(32)


# Paths exempt from CSRF: login (no session yet), static assets, MFA challenge
# (challenge runs before the session is fully usable, token is sent via JS).
CSRF_EXEMPT_PREFIXES = ("/static", "/login", "/mfa/challenge", "/mfa/api/verify", "/channels/whatsapp", "/channels/twilio")


async def csrf_middleware(request, call_next):
    """Enforce the double-submit CSRF pattern on all state-changing requests.

    Audit fix: validate_csrf existed but was never called by any route.
    Accepts the token via X-CSRF-Token header (fetch) or csrf_token form field
    (server-rendered forms). The request body is restored after inspection so
    downstream handlers still see it.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return await call_next(request)
    path = request.url.path
    if any(path.startswith(p) for p in CSRF_EXEMPT_PREFIXES):
        return await call_next(request)
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    header_token = request.headers.get("X-CSRF-Token", "")
    provided = header_token
    if not provided:
        # server-rendered form: read the body, extract csrf_token, restore it
        body = await request.body()
        request._body = body
        try:
            parsed = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
            provided = parsed.get("csrf_token", [""])[0]
        except Exception:
            provided = ""
    if not cookie_token or not provided or not hmac.compare_digest(provided, cookie_token):
        return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
    return await call_next(request)
