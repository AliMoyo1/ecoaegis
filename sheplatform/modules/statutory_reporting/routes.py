"""Statutory reporting routes (B4)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.statutory_reporting import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/statutory-reports")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.statutory.access")
async def statutory_shell(request: Request):
    return templates.TemplateResponse(request, "statutory_reporting/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/templates")
@require_auth
@require_capability("module.statutory.access")
async def api_templates(request: Request, authority: str = ""):
    db = get_db()
    try:
        data_service.seed_templates(db)
        items = data_service.list_templates(db, authority=authority or None)
        return JSONResponse({"templates": items})
    finally:
        db.commit()
        db.close()


@router.post("/api/reports")
@require_auth
@require_capability("module.statutory.manage")
async def api_create_report(request: Request,
                            template_key: str = Form(...),
                            period_start: str = Form(...),
                            period_end: str = Form(...),
                            incident_id: int | None = Form(None)):
    db = get_db()
    try:
        data_service.seed_templates(db)
        result = data_service.create_report(
            db, template_key=template_key, period_start=period_start,
            period_end=period_end, created_by=request.state.user["id"],
            org_id=request.state.user.get("org_id"), incident_id=incident_id)
        if not result["ok"]:
            return JSONResponse(result, status_code=400)
        return JSONResponse(result, status_code=201)
    finally:
        db.commit()
        db.close()


@router.get("/api/reports")
@require_auth
@require_capability("module.statutory.access")
async def api_list_reports(request: Request, status: str = "", authority: str = ""):
    db = get_db()
    try:
        items = data_service.list_reports(
            db, org_id=request.state.user.get("org_id"),
            status=status or None, authority=authority or None)
        return JSONResponse({"reports": items})
    finally:
        db.close()


@router.get("/api/reports/{report_id}")
@require_auth
@require_capability("module.statutory.access")
async def api_get_report(request: Request, report_id: int):
    db = get_db()
    try:
        report = data_service.get_report(db, report_id,
                                         org_id=request.state.user.get("org_id"))
        if not report:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        return JSONResponse({"report": report})
    finally:
        db.close()


@router.post("/api/reports/{report_id}/update")
@require_auth
@require_capability("module.statutory.manage")
async def api_update_report(request: Request, report_id: int,
                            updates_json: str = Form(...)):
    db = get_db()
    try:
        updates = json.loads(updates_json)
        result = data_service.update_report_data(db, report_id, updates,
                                                 updated_by=request.state.user["id"])
        if not result["ok"]:
            return JSONResponse(result, status_code=400)
        return JSONResponse(result)
    finally:
        db.commit()
        db.close()


@router.post("/api/reports/{report_id}/lock")
@require_auth
@require_capability("module.statutory.manage")
async def api_lock_report(request: Request, report_id: int):
    db = get_db()
    try:
        result = data_service.lock_report(db, report_id,
                                          locked_by=request.state.user["id"])
        if not result["ok"]:
            return JSONResponse(result, status_code=400)
        return JSONResponse(result)
    finally:
        db.commit()
        db.close()


@router.post("/api/reports/{report_id}/submit")
@require_auth
@require_capability("module.statutory.manage")
async def api_submit_report(request: Request, report_id: int,
                            channel: str = Form("manual"),
                            recipient: str = Form("")):
    db = get_db()
    try:
        result = data_service.submit_report(
            db, report_id, submitted_by=request.state.user["id"],
            channel=channel, recipient=recipient)
        if not result["ok"]:
            return JSONResponse(result, status_code=400)
        return JSONResponse(result)
    finally:
        db.commit()
        db.close()


@router.get("/api/reports/{report_id}/export.json")
@require_auth
@require_capability("module.statutory.access")
async def api_export_json(request: Request, report_id: int):
    db = get_db()
    try:
        report = data_service.get_report(db, report_id,
                                         org_id=request.state.user.get("org_id"))
        if not report:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        return JSONResponse(data_service.export_json(report))
    finally:
        db.close()


@router.get("/api/reports/{report_id}/export.txt")
@require_auth
@require_capability("module.statutory.access")
async def api_export_text(request: Request, report_id: int):
    db = get_db()
    try:
        report = data_service.get_report(db, report_id,
                                         org_id=request.state.user.get("org_id"))
        if not report:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        return PlainTextResponse(report.get("rendered_text", ""))
    finally:
        db.close()
