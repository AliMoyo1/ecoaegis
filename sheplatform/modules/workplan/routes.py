"""SHEAWPM - Annual Workplan routes (guide 18)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.workplan import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/workplan")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.workplan.access")
async def workplan_shell(request: Request):
    return templates.TemplateResponse(request, "workplan/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/list")
@require_auth
@require_capability("module.workplan.access")
async def api_list(request: Request):
    db = get_db()
    try:
        return JSONResponse({"workplans": data_service.list_workplans(db, org_id=request.state.user.get("org_id"))})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
@require_capability("module.workplan.access")
async def api_create(request: Request, fiscal_year: str = Form(...)):
    db = get_db()
    try:
        plan = data_service.create_workplan(
            db, fiscal_year=fiscal_year, created_by=request.state.user["id"],
            org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "workplan": plan}, status_code=201)
    finally:
        db.close()


@router.post("/api/{plan_id}/tasks")
@require_auth
@require_capability("module.workplan.access")
async def api_add_task(request: Request, plan_id: int,
                       title: str = Form(...),
                       control_type: str = Form(...),
                       cost_estimate: float | None = Form(None),
                       milestone_date: str = Form("")):
    db = get_db()
    try:
        result = data_service.add_task(
            db, workplan_id=plan_id, title=title, control_type=control_type,
            cost_estimate=cost_estimate, milestone_date=milestone_date)
        if not result["ok"]:
            return JSONResponse(result, status_code=400)
        return JSONResponse(result, status_code=201)
    finally:
        db.close()


@router.get("/api/{plan_id}/tasks")
@require_auth
@require_capability("module.workplan.access")
async def api_tasks(request: Request, plan_id: int):
    db = get_db()
    try:
        return JSONResponse({"tasks": data_service.list_tasks(db, plan_id)})
    finally:
        db.close()


@router.post("/api/{plan_id}/submit")
@require_auth
@require_capability("module.workplan.access")
async def api_submit(request: Request, plan_id: int):
    db = get_db()
    try:
        result = data_service.submit_for_review(db, plan_id)
        code = 400 if result.get("code") == "BRN-006" else 200
        return JSONResponse(result, status_code=code)
    finally:
        db.close()


@router.post("/api/{plan_id}/approve")
@require_auth
@require_capability("module.workplan.access")
async def api_approve(request: Request, plan_id: int):
    db = get_db()
    try:
        return JSONResponse(data_service.approve_workplan(db, plan_id, request.state.user["id"]))
    finally:
        db.close()


@router.post("/api/{plan_id}/close")
@require_auth
@require_capability("module.workplan.access")
async def api_close(request: Request, plan_id: int):
    db = get_db()
    try:
        result = data_service.close_workplan(db, plan_id)
        code = 400 if result.get("code") == "BRN-011" else 200
        return JSONResponse(result, status_code=code)
    finally:
        db.close()
