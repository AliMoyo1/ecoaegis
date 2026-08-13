"""SHER - Reporting routes (guide 16)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.reporting import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/reports")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.reports.access")
async def reports_shell(request: Request):
    return templates.TemplateResponse(request, "reporting/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/list")
@require_auth
@require_capability("module.reports.access")
async def api_list(request: Request, status: str = ""):
    db = get_db()
    try:
        items = data_service.list_reports(db, status=status or None,
                                          org_id=request.state.user.get("org_id"))
        return JSONResponse({"reports": items})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
@require_capability("module.reports.access")
async def api_create(request: Request,
                     report_type: str = Form(...),
                     title: str = Form(...),
                     period_start: str = Form(""),
                     period_end: str = Form(""),
                     submission_deadline: str = Form("")):
    db = get_db()
    try:
        result = data_service.create_report(
            db, report_type=report_type, title=title, period_start=period_start,
            period_end=period_end, submission_deadline=submission_deadline,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        if not result["ok"]:
            return JSONResponse(result, status_code=400)
        return JSONResponse(result, status_code=201)
    finally:
        db.close()


@router.post("/api/{report_id}/submit")
@require_auth
@require_capability("module.reports.access")
async def api_submit(request: Request, report_id: int):
    db = get_db()
    try:
        return JSONResponse(data_service.submit_for_approval(db, report_id))
    finally:
        db.close()


@router.post("/api/{report_id}/approve-step")
@require_auth
@require_capability("module.reports.approve")
async def api_approve_step(request: Request, report_id: int,
                           step_id: int = Form(...),
                           decision: str = Form("approved"),
                           comments: str = Form("")):
    db = get_db()
    try:
        result = data_service.approve_step(db, report_id, step_id,
                                           request.state.user, decision, comments)
        return JSONResponse(result)
    finally:
        db.close()


@router.post("/api/{report_id}/action-items")
@require_auth
@require_capability("module.reports.access")
async def api_action_item(request: Request, report_id: int,
                          action_text: str = Form(...),
                          assigned_to: int | None = Form(None),
                          due_date: str = Form("")):
    db = get_db()
    try:
        item = data_service.add_action_item(db, report_id, action_text,
                                            assigned_to, due_date)
        return JSONResponse({"ok": True, "action_item": item})
    finally:
        db.close()


@router.get("/api/key-issues")
@require_auth
@require_capability("module.reports.access")
async def api_key_issues(request: Request, status: str = ""):
    db = get_db()
    try:
        return JSONResponse({"key_issues": data_service.list_key_issues(db, status=status or None)})
    finally:
        db.close()


@router.post("/api/key-issues")
@require_auth
@require_capability("module.reports.access")
async def api_key_issue_create(request: Request,
                               title: str = Form(...),
                               description: str = Form(""),
                               severity: str = Form("medium"),
                               source_report_id: int | None = Form(None)):
    db = get_db()
    try:
        issue = data_service.add_key_issue(
            db, source_report_id or 0, title, description, severity,
            org_id=request.state.user.get("org_id"),
            created_by=request.state.user["id"])
        return JSONResponse({"ok": True, "key_issue": issue}, status_code=201)
    finally:
        db.close()
