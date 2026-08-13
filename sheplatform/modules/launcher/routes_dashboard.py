"""Launcher module: dashboard (guide 7 dashboard KPIs, analytics upgrade)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from sheplatform.core.middleware import require_auth
from sheplatform.core.rbac import has_capability
from sheplatform.database import get_db
from sheplatform.modules.launcher.dashboard_service import dashboard_stats
from sheplatform.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
@require_auth
async def dashboard(request: Request):
    # Employees lack dashboard access; land them on incident reporting instead
    if not has_capability(request.state.user, "module.dashboard.access"):
        return RedirectResponse(url="/incidents", status_code=303)
    db = get_db()
    try:
        stats = dashboard_stats(db, request.state.user["id"])
        return templates.TemplateResponse(request, "dashboard.html",
                                          {"stats": stats, "user": request.state.user})
    finally:
        db.close()


@router.get("/api/dashboard/stats")
@require_auth
async def dashboard_stats_api(request: Request):
    db = get_db()
    try:
        return JSONResponse(dashboard_stats(db, request.state.user["id"]))
    finally:
        db.close()
