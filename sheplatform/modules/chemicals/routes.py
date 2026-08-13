"""Chemicals / SDS routes."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.chemicals import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/chemicals", tags=["chemicals"])


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.chemicals.access")
async def chemicals_shell(request: Request):
    return templates.TemplateResponse(request, "chemicals/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/list")
@require_auth
@require_capability("module.chemicals.access")
async def api_list(request: Request, hazard_class: str = "", site_id: int = 0):
    db = get_db()
    try:
        items = data_service.list_chemicals(
            db, hazard_class=hazard_class or None, site_id=site_id or None,
            org_id=request.state.user.get("org_id"))
        return JSONResponse({"chemicals": items})
    finally:
        db.close()


@router.get("/api/summary")
@require_auth
@require_capability("module.chemicals.access")
async def api_summary(request: Request):
    db = get_db()
    try:
        return JSONResponse(data_service.hazard_summary(db, request.state.user.get("org_id")))
    finally:
        db.close()


@router.get("/api/sites")
@require_auth
@require_capability("module.chemicals.access")
async def api_sites(request: Request):
    db = get_db()
    try:
        rows = db.execute("SELECT id, site_name, city FROM sites WHERE status = 'active' "
                          "ORDER BY site_name").fetchall()
        return JSONResponse({"sites": [dict(r) for r in rows]})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
@require_capability("chemicals.manage")
async def api_create(request: Request, name: str = Form(...), cas_number: str = Form(""),
                     supplier: str = Form(""), hazard_class: str = Form(""),
                     pictogram: str = Form(""), sds_path: str = Form(""),
                     quantity_units: str = Form(""), storage_location: str = Form(""),
                     site_id: int = Form(0)):
    db = get_db()
    try:
        chem = data_service.create_chemical(
            db, name=name, cas_number=cas_number, supplier=supplier,
            hazard_class=hazard_class, pictogram=pictogram, sds_path=sds_path,
            quantity_units=quantity_units, storage_location=storage_location,
            site_id=site_id or None, created_by=request.state.user["id"],
            org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "chemical": chem})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()
