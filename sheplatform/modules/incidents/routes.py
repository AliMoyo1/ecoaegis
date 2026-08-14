"""SHEIMI - Incident routes (guide 8). SPA shell + JSON API."""
from __future__ import annotations

import json
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
                     location: str = Form(""),
                     ai_classify: str = Form(""),
                     accept_ai: str = Form(""),
                     immediate_actions: str = Form(""),
                     estimated_cost: str = Form(""),
                     witnesses_json: str = Form("")):
    from sheplatform.modules.ai import service as ai_service
    db = get_db()
    try:
        suggestion = None
        if ai_classify == "true" and description:
            suggestion = await ai_service.classify_incident(description)
            suggestion = suggestion.get("suggestion") if suggestion.get("ok") else None

        if accept_ai == "true" and suggestion:
            title = suggestion.get("title") or title
            severity = suggestion.get("severity") or severity
            incident_type = suggestion.get("incident_type") or incident_type

        if severity not in ("critical", "high", "medium", "low"):
            return JSONResponse({"ok": False, "message": "invalid severity"}, status_code=400)
        if incident_type not in ("accident", "near_miss", "environmental", "vehicle", "medical", "fatality"):
            return JSONResponse({"ok": False, "message": "invalid incident_type"}, status_code=400)
        occurred_at = occurred_at or datetime.now(timezone.utc).isoformat()
        ai_metadata = {"suggestion": suggestion} if suggestion else None

        cost = None
        if estimated_cost:
            try:
                cost = float(estimated_cost)
            except ValueError:
                return JSONResponse({"ok": False, "message": "estimated_cost must be a number"}, status_code=400)
        witnesses = []
        if witnesses_json:
            try:
                witnesses = json.loads(witnesses_json)
            except ValueError:
                return JSONResponse({"ok": False, "message": "witnesses_json must be valid JSON"}, status_code=400)

        incident = data_service.create_incident(
            db, title=title, description=description, severity=severity,
            incident_type=incident_type, occurred_at=occurred_at,
            location=location, reported_by=request.state.user["id"],
            org_id=request.state.user.get("org_id"), ai_metadata=ai_metadata,
            immediate_actions=immediate_actions, estimated_cost=cost, witnesses=witnesses)
        log_audit(db, request.state.user["id"], request.state.user.get("org_id"),
                  "incident.create", "incidents", incident["id"],
                  new_value={"ref": incident["incident_ref"], "severity": severity,
                             "ai_classified": bool(ai_metadata)})
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
        org_id = request.state.user.get("org_id")
        if org_id and incident.get("org_id") and incident["org_id"] != org_id:
            return JSONResponse({"ok": False, "message": "not found"}, status_code=404)
        incident["timeline"] = data_service.get_timeline(db, incident_id)
        incident["injuries"] = data_service.list_injuries(db, incident_id)
        return JSONResponse({"incident": incident})
    finally:
        db.close()


@router.get("/{incident_id}", response_class=HTMLResponse)
@require_auth
@require_capability("module.incidents.access")
async def incident_detail_page(request: Request, incident_id: int):
    db = get_db()
    try:
        incident = data_service.get_incident(db, incident_id)
        if incident is None:
            return RedirectResponse(url="/incidents", status_code=303)
        return templates.TemplateResponse(request, "incidents/templates/detail.html",
                                          {"user": request.state.user, "incident_id": incident_id})
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


# ---- B5: incident intake depth ----

@router.post("/api/{incident_id}/injuries")
@require_auth
@require_capability("module.incidents.access")
async def api_add_injury(request: Request, incident_id: int,
                         injured_name: str = Form(""),
                         injured_type: str = Form("employee"),
                         body_part: str = Form(""),
                         injury_type: str = Form(""),
                         lost_time_days: str = Form("0"),
                         medical_treatment: str = Form("")):
    db = get_db()
    try:
        # Same org guard as api_detail: an injury can only be added to an
        # incident the caller's own org can see.
        incident = data_service.get_incident(db, incident_id)
        if incident is None:
            return JSONResponse({"ok": False, "message": "incident not found"}, status_code=404)
        org_id = request.state.user.get("org_id")
        if org_id and incident.get("org_id") and incident["org_id"] != org_id:
            return JSONResponse({"ok": False, "message": "incident not found"}, status_code=404)
        if injured_type not in ("employee", "contractor", "public", "other"):
            return JSONResponse({"ok": False, "message": "invalid injured_type"}, status_code=400)
        try:
            days = int(lost_time_days or 0)
        except ValueError:
            return JSONResponse({"ok": False, "message": "lost_time_days must be a number"}, status_code=400)

        result = data_service.add_injury(
            db, incident_id, injured_name=injured_name, injured_type=injured_type,
            body_part=body_part, injury_type=injury_type, lost_time_days=days,
            medical_treatment=medical_treatment, created_by=request.state.user["id"],
            org_id=org_id)
        if not result["ok"]:
            return JSONResponse(result, status_code=404)
        return JSONResponse(result, status_code=201)
    finally:
        db.close()


@router.get("/api/settings/exposure-hours")
@require_auth
@require_capability("module.settings.access")
async def api_get_exposure_hours(request: Request):
    """The LTIFR denominator (organisations.settings.annual_exposure_hours)."""
    db = get_db()
    try:
        stats = data_service.get_ltifr_stats(db, request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "hours_worked": stats["hours_worked"]})
    finally:
        db.close()


@router.post("/api/settings/exposure-hours")
@require_auth
@require_capability("module.settings.access")
async def api_set_exposure_hours(request: Request, annual_exposure_hours: str = Form(...)):
    db = get_db()
    try:
        org_id = request.state.user.get("org_id")
        if not org_id:
            return JSONResponse({"ok": False, "message": "no organisation"}, status_code=400)
        try:
            hours = float(annual_exposure_hours)
        except ValueError:
            return JSONResponse({"ok": False, "message": "must be a number"}, status_code=400)
        if hours <= 0:
            return JSONResponse({"ok": False, "message": "must be positive"}, status_code=400)
        result = data_service.set_exposure_hours(db, org_id, hours)
        log_audit(db, request.state.user["id"], org_id, "settings.exposure_hours",
                  "organisations", org_id, new_value={"annual_exposure_hours": hours})
        return JSONResponse(result)
    finally:
        db.close()
