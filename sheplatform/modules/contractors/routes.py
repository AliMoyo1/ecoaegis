"""Contractor readiness routes."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.contractors import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/contractors", tags=["contractors"])


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.contractors.access")
async def contractors_shell(request: Request):
    return templates.TemplateResponse(request, "contractors/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/vendors")
@require_auth
@require_capability("module.contractors.access")
async def api_vendors(request: Request):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT v.id, v.vendor_ref, v.company_name, v.insurance_expiry, v.status, "
            "v.ptw_eligible, v.certification_status FROM vendors v "
            "WHERE v.org_id = %s OR %s IS NULL ORDER BY v.company_name",
            (request.state.user.get("org_id"), request.state.user.get("org_id"))).fetchall()
        return JSONResponse({"vendors": [dict(r) for r in rows]})
    finally:
        db.close()


@router.get("/api/sites")
@require_auth
@require_capability("module.contractors.access")
async def api_sites(request: Request):
    db = get_db()
    try:
        rows = db.execute("SELECT id, site_code, site_name, city FROM sites "
                          "WHERE status = 'active' ORDER BY site_name").fetchall()
        return JSONResponse({"sites": [dict(r) for r in rows]})
    finally:
        db.close()


@router.get("/api/readiness")
@require_auth
@require_capability("module.contractors.access")
async def api_readiness(request: Request, vendor_id: int, site_id: int = 0):
    db = get_db()
    try:
        status = data_service.site_readiness(db, vendor_id, site_id or None)
        return JSONResponse(status)
    finally:
        db.close()


@router.get("/api/inductions")
@require_auth
@require_capability("module.contractors.access")
async def api_inductions(request: Request, vendor_id: int = 0):
    db = get_db()
    try:
        items = data_service.list_inductions(db, vendor_id or None)
        return JSONResponse({"inductions": items})
    finally:
        db.close()


@router.post("/api/induction")
@require_auth
@require_capability("contractors.manage")
async def api_induction(request: Request, vendor_id: int = Form(...),
                        site_id: int = Form(...), induction_type: str = Form("site_specific"),
                        valid_until: str = Form(""), notes: str = Form("")):
    db = get_db()
    try:
        ind = data_service.record_induction(
            db, vendor_id=vendor_id, site_id=site_id, induction_type=induction_type,
            valid_until=valid_until, trainer_id=request.state.user["id"], notes=notes)
        return JSONResponse({"ok": True, "induction": ind})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()
