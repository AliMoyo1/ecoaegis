"""SHE EC&SC - External Communications routes (guide 17)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.external_comms import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/comms")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.comms.access")
async def comms_shell(request: Request):
    return templates.TemplateResponse(request, "external_comms/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/list")
@require_auth
async def api_list(request: Request, status: str = ""):
    db = get_db()
    try:
        return JSONResponse({"comms": data_service.list_comms(db, status=status or None, org_id=request.state.user.get("org_id"))})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
@require_capability("comms.draft")
async def api_create(request: Request,
                     concern_description: str = Form(...),
                     target_segment: str = Form(""),
                     communication_brief: str = Form(""),
                     medium: str = Form("digital"),
                     frequency: str = Form("")):
    db = get_db()
    try:
        comms = data_service.create_comms(
            db, concern_description=concern_description, target_segment=target_segment,
            communication_brief=communication_brief, medium=medium, frequency=frequency,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "comms": comms}, status_code=201)
    finally:
        db.close()


@router.post("/api/{comms_id}/submit")
@require_auth
@require_capability("comms.draft")
async def api_submit(request: Request, comms_id: int):
    db = get_db()
    try:
        return JSONResponse(data_service.submit_for_hod_review(db, comms_id))
    finally:
        db.close()


@router.post("/api/{comms_id}/hod-approve")
@require_auth
@require_capability("comms.approve")
async def api_hod_approve(request: Request, comms_id: int, comments: str = Form("")):
    db = get_db()
    try:
        result = data_service.hod_approve(db, comms_id, request.state.user["id"], comments)
        code = 400 if result.get("code") == "BRN-008" else 200
        return JSONResponse(result, status_code=code)
    finally:
        db.close()


@router.post("/api/{comms_id}/dispatch")
@require_auth
@require_capability("comms.draft")
async def api_dispatch(request: Request, comms_id: int):
    db = get_db()
    try:
        result = data_service.dispatch(db, comms_id, request.state.user["id"])
        code = 400 if result.get("code") == "BRN-008" else 200
        return JSONResponse(result, status_code=code)
    finally:
        db.close()


@router.post("/api/{comms_id}/close")
@require_auth
@require_capability("comms.approve")
async def api_close(request: Request, comms_id: int, assessment: str = Form("")):
    db = get_db()
    try:
        result = data_service.close_with_effectiveness(db, comms_id, assessment)
        code = 400 if result.get("code") == "FNR-016" else 200
        return JSONResponse(result, status_code=code)
    finally:
        db.close()
