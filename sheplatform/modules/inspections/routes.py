"""Inspections routes."""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.inspections import data_service
from sheplatform.modules.map.site_relationship_service import list_active_sites
from sheplatform.templating import templates

router = APIRouter(prefix="/inspections", tags=["inspections"])


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.inspections.access")
async def inspections_shell(request: Request):
    db = get_db()
    try:
        sites = list_active_sites(db, request.state.user.get("org_id"))
        return templates.TemplateResponse(
            request, "inspections/templates/index.html",
            {"user": request.state.user, "sites": sites},
        )
    finally:
        db.close()


@router.get("/api/checklist")
@require_auth
@require_capability("module.inspections.access")
async def api_checklist(request: Request, inspection_type: str = "safety"):
    return JSONResponse({"items": data_service.get_checklist(inspection_type)})


@router.get("/api/list")
@require_auth
@require_capability("module.inspections.access")
async def api_list(request: Request, status: str = ""):
    db = get_db()
    try:
        items = data_service.list_inspections(db, status=status or None,
                                              org_id=request.state.user.get("org_id"))
        return JSONResponse({"inspections": items})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
@require_capability("inspections.schedule")
async def api_create(request: Request, title: str = Form(...),
                     inspection_type: str = Form("safety"),
                     site_location: str = Form(""),
                     site_id: int | None = Form(None),
                     scheduled_date: str = Form(""),
                     inspector_id: int = Form(0)):
    db = get_db()
    try:
        insp = data_service.schedule_inspection(
            db, title=title, inspection_type=inspection_type, site_location=site_location,
            site_id=site_id, scheduled_date=scheduled_date, inspector_id=inspector_id,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "inspection": insp})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()


@router.post("/api/{inspection_id}/start")
@require_auth
@require_capability("module.inspections.access")
async def api_start(request: Request, inspection_id: int):
    db = get_db()
    try:
        insp = data_service.start_inspection(db, inspection_id, request.state.user["id"])
        return JSONResponse({"ok": True, "inspection": insp})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()


@router.post("/api/{inspection_id}/complete")
@require_auth
@require_capability("inspections.complete")
async def api_complete(request: Request, inspection_id: int, findings: str = Form(""),
                       results_json: str = Form("[]")):
    db = get_db()
    try:
        results = json.loads(results_json)
        if not isinstance(results, list):
            raise ValueError("results must be a list")
        out = data_service.complete_inspection(
            db, inspection_id, request.state.user["id"], findings, results,
            org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, **out})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()
