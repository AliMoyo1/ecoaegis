"""SHEEPRP + SHEER - Emergency routes (guide 14)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.audit import log_audit
from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.emergency import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/emergency")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.emergency.access")
async def emergency_shell(request: Request):
    return templates.TemplateResponse(request, "emergency/templates/index.html",
                                      {"user": request.state.user})


# ---- Plans ----
@router.get("/api/plans")
@require_auth
@require_capability("module.emergency.access")
async def api_plans(request: Request):
    db = get_db()
    try:
        return JSONResponse({"plans": data_service.list_plans(db, org_id=request.state.user.get("org_id"))})
    finally:
        db.close()


@router.post("/api/plans")
@require_auth
@require_capability("module.emergency.access")
async def api_plan_create(request: Request, title: str = Form(...)):
    db = get_db()
    try:
        plan = data_service.create_plan(db, title=title,
                                        created_by=request.state.user["id"],
                                        org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "plan": plan}, status_code=201)
    finally:
        db.close()


@router.post("/api/plans/{plan_id}/approve")
@require_auth
@require_capability("module.emergency.access")
async def api_plan_approve(request: Request, plan_id: int):
    db = get_db()
    try:
        return JSONResponse(data_service.approve_plan(db, plan_id, request.state.user["id"]))
    finally:
        db.close()


# ---- Drills ----
@router.get("/api/drills")
@require_auth
@require_capability("module.emergency.access")
async def api_drills(request: Request):
    db = get_db()
    try:
        return JSONResponse({"drills": data_service.list_drills(db)})
    finally:
        db.close()


@router.post("/api/drills")
@require_auth
@require_capability("module.emergency.access")
async def api_drill_create(request: Request,
                           drill_type: str = Form(...),
                           scheduled_date: str = Form(...),
                           plan_id: int | None = Form(None)):
    db = get_db()
    try:
        drill = data_service.schedule_drill(
            db, plan_id=plan_id, drill_type=drill_type, scheduled_date=scheduled_date,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "drill": drill}, status_code=201)
    finally:
        db.close()


@router.post("/api/drills/{drill_id}/complete")
@require_auth
@require_capability("module.emergency.access")
async def api_drill_complete(request: Request, drill_id: int,
                             observations: str = Form(""), feedback: str = Form("")):
    db = get_db()
    try:
        return JSONResponse(data_service.complete_drill(
            db, drill_id, observations=observations, feedback=feedback))
    finally:
        db.close()


# ---- Emergency events ----
@router.get("/api/events")
@require_auth
@require_capability("module.emergency.access")
async def api_events(request: Request):
    db = get_db()
    try:
        return JSONResponse({"emergencies": data_service.list_emergencies(db, org_id=request.state.user.get("org_id"))})
    finally:
        db.close()


@router.post("/api/events")
@require_auth
@require_capability("module.emergency.access")
async def api_event_create(request: Request,
                           title: str = Form(...),
                           description: str = Form(""),
                           severity: str = Form("high"),
                           site_location: str = Form("")):
    db = get_db()
    try:
        event = data_service.create_emergency(
            db, title=title, description=description, severity=severity,
            site_location=site_location, created_by=request.state.user["id"],
            org_id=request.state.user.get("org_id"))
        log_audit(db, request.state.user["id"], request.state.user.get("org_id"),
                  "emergency.create", "emergency_events", event["id"],
                  new_value={"event_ref": event["event_ref"]})
        return JSONResponse({"ok": True, "emergency": event}, status_code=201)
    finally:
        db.close()


@router.post("/api/events/{emergency_id}/site-safe")
@require_auth
@require_capability("module.emergency.access")
async def api_site_safe(request: Request, emergency_id: int,
                        she_manager_id: int = Form(...),
                        hod_security_id: int = Form(...)):
    db = get_db()
    try:
        result = data_service.issue_site_safe_certificate(
            db, emergency_id, she_manager_id=she_manager_id, hod_security_id=hod_security_id)
        code = 400 if result.get("code") == "BRN-007" else 200
        return JSONResponse(result, status_code=code)
    finally:
        db.close()


@router.post("/api/events/{emergency_id}/post-crisis")
@require_auth
@require_capability("module.emergency.access")
async def api_post_crisis(request: Request, emergency_id: int,
                          root_cause: str = Form("")):
    db = get_db()
    try:
        return JSONResponse(data_service.transition_post_crisis(
            db, emergency_id, root_cause=root_cause))
    finally:
        db.close()
