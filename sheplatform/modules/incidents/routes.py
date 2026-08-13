"""SHEIMI - Incident routes (guide 8). SPA shell + JSON API."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from sheplatform.core.audit import log_audit
from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.core.rbac import has_capability
from sheplatform.database import get_db
from sheplatform.modules.incidents import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/incidents")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.incidents.access")
async def incidents_shell(request: Request):
    return templates.TemplateResponse(request, "incidents/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/list")
@require_auth
@require_capability("module.incidents.access")
async def api_list(request: Request, status: str = "", severity: str = "", type: str = ""):
    db = get_db()
    try:
        items = data_service.list_incidents(
            db, status=status or None, severity=severity or None, incident_type=type or None,
            org_id=request.state.user.get("org_id"))
        for i in items:
            i["pending_step"] = data_service.get_pending_approval_step(db, i["id"])
        return JSONResponse({"incidents": items})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
@require_capability("incident.create")
async def api_create(request: Request,
                     title: str = Form(...),
                     description: str = Form(...),
                     severity: str = Form(...),
                     incident_type: str = Form("accident"),
                     occurred_at: str = Form(""),
                     location: str = Form("")):
    db = get_db()
    try:
        if severity not in ("critical", "high", "medium", "low"):
            return JSONResponse({"ok": False, "message": "invalid severity"}, status_code=400)
        if incident_type not in ("accident", "near_miss", "environmental", "vehicle", "medical", "fatality"):
            return JSONResponse({"ok": False, "message": "invalid incident_type"}, status_code=400)
        occurred_at = occurred_at or datetime.now(timezone.utc).isoformat()
        incident = data_service.create_incident(
            db, title=title, description=description, severity=severity,
            incident_type=incident_type, occurred_at=occurred_at,
            location=location, reported_by=request.state.user["id"],
            org_id=request.state.user.get("org_id"))
        log_audit(db, request.state.user["id"], request.state.user.get("org_id"),
                  "incident.create", "incidents", incident["id"],
                  new_value={"ref": incident["incident_ref"], "severity": severity})
        return JSONResponse({"ok": True, "incident": incident}, status_code=201)
    finally:
        db.close()


@router.get("/api/{incident_id}")
@require_auth
@require_capability("module.incidents.access")
async def api_detail(request: Request, incident_id: int):
    db = get_db()
    try:
        incident = data_service.get_incident(db, incident_id)
        if incident is None:
            return JSONResponse({"ok": False, "message": "not found"}, status_code=404)
        incident["timeline"] = data_service.get_timeline(db, incident_id)
        return JSONResponse({"incident": incident})
    finally:
        db.close()


@router.post("/api/{incident_id}/timeline")
@require_auth
@require_capability("module.incidents.access")
async def api_timeline(request: Request, incident_id: int, event_text: str = Form(...)):
    db = get_db()
    try:
        entry = data_service.add_timeline_entry(db, incident_id, event_text,
                                                request.state.user["id"])
        return JSONResponse({"ok": True, "entry": entry})
    finally:
        db.close()


@router.post("/api/{incident_id}/assign-team")
@require_auth
@require_capability("incident.investigate")
async def api_assign_team(request: Request, incident_id: int, user_ids: str = Form("[]")):
    import json
    db = get_db()
    try:
        ids = json.loads(user_ids)
        result = data_service.assign_team(db, incident_id, ids, request.state.user["id"])
        return JSONResponse(result)
    finally:
        db.close()


@router.post("/api/{incident_id}/submit-report")
@require_auth
@require_capability("incident.investigate")
async def api_submit_report(request: Request, incident_id: int,
                            root_cause: str = Form(...),
                            immediate_cause: str = Form(""),
                            contributing_factors: str = Form("")):
    db = get_db()
    try:
        result = data_service.submit_root_cause_report(
            db, incident_id, root_cause, immediate_cause, contributing_factors,
            request.state.user["id"])
        return JSONResponse(result)
    finally:
        db.close()


@router.post("/api/{incident_id}/close")
@require_auth
@require_capability("incident.close")
async def api_close(request: Request, incident_id: int):
    db = get_db()
    try:
        result = data_service.close_incident(db, incident_id, request.state.user["id"])
        return JSONResponse(result)
    finally:
        db.close()


@router.post("/api/{incident_id}/approve-report")
@require_auth
@require_capability("incident.approve_report")
async def api_approve_report(request: Request, incident_id: int, step_id: int = Form(...),
                             decision: str = Form("approved"), comments: str = Form("")):
    db = get_db()
    try:
        result = data_service.approve_report_step(
            db, incident_id, step_id, request.state.user, decision, comments)
        if not result["ok"]:
            return JSONResponse({"ok": False, "message": result["message"]}, status_code=400)
        return JSONResponse({"ok": True, "result": result,
                             "incident": data_service.get_incident(db, incident_id)})
    finally:
        db.close()


@router.post("/api/{incident_id}/statutory")
@require_auth
@require_capability("module.incidents.access")
async def api_statutory(request: Request, incident_id: int, body: str = Form(...)):
    db = get_db()
    try:
        result = data_service.set_statutory_notified(db, incident_id, body)
        return JSONResponse(result)
    finally:
        db.close()
