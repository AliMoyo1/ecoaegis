"""Permit to Work routes (guide 11, Module 5)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.audit import log_audit
from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.permit_to_work import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/permits")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.permits.access")
async def permits_shell(request: Request):
    return templates.TemplateResponse(request, "permit_to_work/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/list")
@require_auth
@require_capability("module.permits.access")
async def api_list(request: Request, status: str = ""):
    db = get_db()
    try:
        items = data_service.list_permits(db, status=status or None,
                                          org_id=request.state.user.get("org_id"))
        # attach pending approval step so the UI can show an Approve button
        for p in items:
            p["pending_step"] = data_service.get_pending_approval_step(db, p["id"])
        return JSONResponse({"permits": items})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
@require_capability("ptw.create")
async def api_create(request: Request,
                     permit_type: str = Form(...),
                     title: str = Form(...),
                     description: str = Form(""),
                     vendor_id: int = Form(...),
                     risk_assessment_id: int = Form(...),
                     site_location: str = Form(""),
                     site_id: int | None = Form(None),
                     scope_boundary: str = Form(""),
                     valid_from: str = Form(""),
                     valid_until: str = Form("")):
    db = get_db()
    try:
        if permit_type not in ("hot_work", "confined_space", "electrical", "height", "excavation", "general"):
            return JSONResponse({"ok": False, "message": "invalid permit type"}, status_code=400)
        result = data_service.create_permit(
            db, permit_type=permit_type, title=title, description=description,
            vendor_id=vendor_id, risk_assessment_id=risk_assessment_id,
            site_location=site_location, site_id=site_id, scope_boundary=scope_boundary,
            valid_from=valid_from, valid_until=valid_until,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        if not result["ok"]:
            code = 400 if result.get("code") == "BRN-001" else 404
            return JSONResponse(result, status_code=code)
        log_audit(db, request.state.user["id"], request.state.user.get("org_id"),
                  "permit.create", "permits", result["permit"]["id"],
                  new_value={"permit_ref": result["permit"]["permit_ref"],
                             "site_id": result["permit"].get("site_id")})
        return JSONResponse(result, status_code=201)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    finally:
        db.close()


@router.post("/api/{permit_id}/approve")
@require_auth
@require_capability("module.permits.access")
async def api_approve(request: Request, permit_id: int, step_id: int = Form(...),
                      decision: str = Form("approved"), comments: str = Form("")):
    db = get_db()
    try:
        result = data_service.approve_permit_step(
            db, permit_id, step_id, request.state.user, decision, comments)
        if not result["ok"]:
            return JSONResponse({"ok": False, "message": result["message"]}, status_code=400)
        return JSONResponse({"ok": True, "result": result,
                             "permit": data_service.get_permit(db, permit_id)})
    finally:
        db.close()


@router.post("/api/{permit_id}/close")
@require_auth
@require_capability("ptw.close")
async def api_close(request: Request, permit_id: int,
                    site_restored: str = Form("false"),
                    checklist: str = Form("{}")):
    import json
    db = get_db()
    try:
        try:
            checklist_dict = json.loads(checklist)
        except json.JSONDecodeError:
            checklist_dict = {}
        result = data_service.close_permit(
            db, permit_id, site_restored=site_restored.lower() == "true",
            checklist=checklist_dict, closed_by=request.state.user["id"])
        return JSONResponse(result)
    finally:
        db.close()
