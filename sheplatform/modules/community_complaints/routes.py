"""SHECCM - Community Complaints routes (guide 12)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.audit import log_audit
from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.community_complaints import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/grievances")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.grievances.access")
async def grievances_shell(request: Request):
    return templates.TemplateResponse(request, "community_complaints/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/list")
@require_auth
@require_capability("module.grievances.access")
async def api_list(request: Request, status: str = ""):
    db = get_db()
    try:
        items = data_service.list_grievances(db, status=status or None,
                                             org_id=request.state.user.get("org_id"))
        return JSONResponse({"grievances": items})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
@require_capability("module.grievances.access")
async def api_create(request: Request,
                     description: str = Form(...),
                     source_channel: str = Form("portal"),
                     complainant_name: str = Form(""),
                     complainant_contact: str = Form(""),
                     severity: str = Form("medium")):
    db = get_db()
    try:
        if source_channel not in ("portal", "phone", "email", "walk_in", "media", "regulator", "ngo"):
            return JSONResponse({"ok": False, "message": "invalid channel"}, status_code=400)
        grievance = data_service.create_grievance(
            db, description=description, source_channel=source_channel,
            complainant_name=complainant_name, complainant_contact=complainant_contact,
            severity=severity, created_by=request.state.user["id"],
            org_id=request.state.user.get("org_id"))
        log_audit(db, request.state.user["id"], request.state.user.get("org_id"),
                  "grievance.create", "grievances", grievance["id"],
                  new_value={"case_ref": grievance["case_ref"]})
        return JSONResponse({"ok": True, "grievance": grievance}, status_code=201)
    finally:
        db.close()


@router.post("/api/{grievance_id}/notify")
@require_auth
@require_capability("module.grievances.access")
async def api_notify(request: Request, grievance_id: int, method: str = Form("phone")):
    db = get_db()
    try:
        return JSONResponse(data_service.record_notification(
            db, grievance_id, method, notified_by=request.state.user["id"]))
    finally:
        db.close()


@router.post("/api/{grievance_id}/resolve")
@require_auth
@require_capability("module.grievances.access")
async def api_resolve(request: Request, grievance_id: int,
                      resolution_outcome: str = Form(...),
                      is_residual_risk: str = Form("false"),
                      asset_id: str = Form("")):
    db = get_db()
    try:
        return JSONResponse(data_service.resolve_grievance(
            db, grievance_id, resolution_outcome=resolution_outcome,
            is_residual_risk=is_residual_risk.lower() == "true", asset_id=asset_id))
    finally:
        db.close()


@router.post("/api/{grievance_id}/close")
@require_auth
@require_capability("module.grievances.access")
async def api_close(request: Request, grievance_id: int):
    db = get_db()
    try:
        result = data_service.close_grievance(db, grievance_id, request.state.user["id"])
        code = 400 if result.get("code") == "BRN-010" else 200
        return JSONResponse(result, status_code=code)
    finally:
        db.close()
