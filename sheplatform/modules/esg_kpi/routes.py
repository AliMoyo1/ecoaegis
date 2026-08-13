"""ESG KPI routes (guide 19)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.esg_kpi import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/esg")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.esg.access")
async def esg_shell(request: Request):
    return templates.TemplateResponse(request, "esg_kpi/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/kpis")
@require_auth
@require_capability("module.esg.access")
async def api_kpis(request: Request, category: str = ""):
    db = get_db()
    try:
        return JSONResponse({"kpis": data_service.list_kpis(db, category=category or None)})
    finally:
        db.close()


@router.post("/api/seed")
@require_auth
@require_capability("module.esg.access")
async def api_seed(request: Request):
    db = get_db()
    try:
        n = data_service.seed_kpis(db, org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "seeded": n})
    finally:
        db.close()


@router.post("/api/entries")
@require_auth
@require_capability("module.esg.access")
async def api_entry(request: Request,
                    kpi_id: int = Form(...),
                    period: str = Form(...),
                    actual_value: float = Form(...),
                    target_value: float | None = Form(None),
                    notes: str = Form("")):
    db = get_db()
    try:
        result = data_service.record_kpi_entry(
            db, kpi_id=kpi_id, period=period, actual_value=actual_value,
            target_value=target_value, notes=notes,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        if not result["ok"]:
            return JSONResponse(result, status_code=404)
        return JSONResponse(result, status_code=201)
    finally:
        db.close()


@router.get("/api/entries")
@require_auth
@require_capability("module.esg.access")
async def api_entries(request: Request, kpi_id: int | None = None, period: str = ""):
    db = get_db()
    try:
        return JSONResponse({"entries": data_service.list_entries(db, kpi_id=kpi_id,
                                                                  period=period or None)})
    finally:
        db.close()


@router.get("/api/summary")
@require_auth
@require_capability("module.esg.access")
async def api_summary(request: Request):
    db = get_db()
    try:
        return JSONResponse(data_service.dashboard_summary(db))
    finally:
        db.close()
