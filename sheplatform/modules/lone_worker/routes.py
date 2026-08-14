"""Lone worker / man-down routes (guide C2). SPA shell + JSON API."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.lone_worker import data_service
from sheplatform.modules.lone_worker.scheduler import escalate_session
from sheplatform.templating import templates

router = APIRouter(prefix="/lone-worker")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.lone_worker.access")
async def lone_worker_shell(request: Request):
    return templates.TemplateResponse(request, "lone_worker/templates/index.html",
                                      {"user": request.state.user})


@router.post("/api/start")
@require_auth
@require_capability("module.lone_worker.access")
async def api_start(request: Request,
                    expected_duration_minutes: str = Form(...),
                    location: str = Form(""),
                    latitude: str = Form(""),
                    longitude: str = Form(""),
                    nominated_contact_name: str = Form(""),
                    nominated_contact_phone: str = Form("")):
    db = get_db()
    try:
        try:
            duration = int(expected_duration_minutes)
        except ValueError:
            return JSONResponse({"ok": False, "message": "expected_duration_minutes must be a number"}, status_code=400)
        if duration <= 0 or duration > 1440:
            return JSONResponse({"ok": False, "message": "expected_duration_minutes must be between 1 and 1440"}, status_code=400)
        lat = lng = None
        if latitude and longitude:
            try:
                lat, lng = float(latitude), float(longitude)
            except ValueError:
                return JSONResponse({"ok": False, "message": "latitude/longitude must be numbers"}, status_code=400)

        session = data_service.start_checkin(
            db, worker_id=request.state.user["id"], expected_duration_minutes=duration,
            location=location, latitude=lat, longitude=lng,
            nominated_contact_name=nominated_contact_name,
            nominated_contact_phone=nominated_contact_phone,
            org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "session": session}, status_code=201)
    finally:
        db.close()


@router.post("/api/{session_id}/checkin")
@require_auth
@require_capability("module.lone_worker.access")
async def api_checkin(request: Request, session_id: int):
    db = get_db()
    try:
        result = data_service.check_in(db, session_id, request.state.user["id"])
        if not result["ok"]:
            return JSONResponse(result, status_code=404)
        return JSONResponse(result)
    finally:
        db.close()


@router.post("/api/{session_id}/extend")
@require_auth
@require_capability("module.lone_worker.access")
async def api_extend(request: Request, session_id: int, additional_minutes: str = Form(...)):
    db = get_db()
    try:
        try:
            minutes = int(additional_minutes)
        except ValueError:
            return JSONResponse({"ok": False, "message": "additional_minutes must be a number"}, status_code=400)
        result = data_service.extend_checkin(db, session_id, request.state.user["id"], minutes)
        if not result["ok"]:
            status = 400 if "positive" in result["message"] else 404
            return JSONResponse(result, status_code=status)
        return JSONResponse(result)
    finally:
        db.close()


@router.post("/api/{session_id}/cancel")
@require_auth
@require_capability("module.lone_worker.access")
async def api_cancel(request: Request, session_id: int):
    db = get_db()
    try:
        result = data_service.cancel_checkin(db, session_id, request.state.user["id"])
        if not result["ok"]:
            return JSONResponse(result, status_code=404)
        return JSONResponse(result)
    finally:
        db.close()


@router.post("/api/{session_id}/man-down")
@require_auth
@require_capability("module.lone_worker.access")
async def api_man_down(request: Request, session_id: int):
    """Early, device-triggered escalation (guide C2 step 3): the client's
    devicemotion inactivity timer calls this when a check/are-you-OK prompt
    goes unanswered. Same escalation path the scheduler uses for a lapsed
    deadline, just triggered sooner. Still requires the session to belong
    to the caller - only the worker's own device can raise this for them.
    """
    db = get_db()
    try:
        session = db.execute(
            "SELECT * FROM lone_worker_checkins WHERE id = %s AND worker_id = %s AND status = 'active'",
            (session_id, request.state.user["id"])).fetchone()
        if session is None:
            return JSONResponse({"ok": False, "message": "no active session found for this worker"}, status_code=404)
        result = escalate_session(db, dict(session))
        return JSONResponse({"ok": True, **result})
    finally:
        db.close()


@router.get("/api/list")
@require_auth
@require_capability("module.lone_worker.access")
async def api_list(request: Request):
    db = get_db()
    try:
        sessions = data_service.list_active_checkins(db, request.state.user.get("org_id"))
        return JSONResponse({"sessions": sessions})
    finally:
        db.close()
