"""AI routes (guide 23): /api/ai/chat + feature endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.ai import service
from sheplatform.templating import templates

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("", response_class=HTMLResponse)
@require_auth
async def ai_panel(request: Request):
    return templates.TemplateResponse(request, "ai/templates/panel.html",
                                      {"user": request.state.user})


@router.get("/api/status")
@require_auth
async def api_status(request: Request):
    from sheplatform.core.ai_client import provider_info
    return JSONResponse(provider_info())


@router.post("/api/chat")
@require_auth
async def api_chat(request: Request, question: str = Form(...)):
    return JSONResponse(await service.chat(question, request.state.user.get("org_id")))


@router.post("/api/briefing")
@require_auth
async def api_briefing(request: Request):
    return JSONResponse(await service.daily_briefing(request.state.user.get("org_id")))


@router.post("/api/incident-copilot/{incident_id}")
@require_auth
@require_capability("incident.investigate")
async def api_incident_copilot(request: Request, incident_id: int):
    result = await service.incident_copilot(incident_id, request.state.user.get("org_id"))
    if not result.get("ok"):
        status = 404 if "not found" in result.get("message", "") else 400
        return JSONResponse(result, status_code=status)
    return JSONResponse(result)


@router.post("/api/root-cause/{incident_id}")
@require_auth
@require_capability("incident.investigate")
async def api_root_cause(request: Request, incident_id: int):
    result = await service.root_cause_assistant(incident_id, request.state.user.get("org_id"))
    if not result.get("ok"):
        status = 404 if "not found" in result.get("message", "") else 400
        return JSONResponse(result, status_code=status)
    return JSONResponse(result)


@router.post("/api/draft-actions/{incident_id}")
@require_auth
@require_capability("incident.investigate")
async def api_draft_actions(request: Request, incident_id: int):
    result = await service.draft_corrective_actions(
        incident_id, request.state.user.get("org_id"))
    if not result.get("ok"):
        status = 404 if "not found" in result.get("message", "") else 400
        return JSONResponse(result, status_code=status)
    return JSONResponse(result)


@router.post("/api/classify-incident")
@require_auth
@require_capability("incident.create")
async def api_classify_incident(request: Request, description: str = Form(...)):
    result = await service.classify_incident(description)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return JSONResponse(result)


@router.post("/api/similar-incidents")
@require_auth
async def api_similar_incidents(request: Request, description: str = Form(""),
                                incident_id: int = Form(0)):
    org_id = request.state.user.get("org_id")
    if incident_id:
        from sheplatform.modules.incidents.data_service import get_incident
        db = get_db()
        try:
            inc = get_incident(db, incident_id)
            if inc and org_id and inc.get("org_id") == org_id:
                description = f"{inc.get('title', '')} {inc.get('description', '')}"
                exclude = incident_id
            else:
                exclude = None
        finally:
            db.close()
    else:
        exclude = None
    return JSONResponse(await service.similar_incidents(description, org_id, exclude_id=exclude))


@router.post("/api/sql-chat")
@require_auth
async def api_sql_chat(request: Request, question: str = Form(...)):
    return JSONResponse(await service.safe_sql_chat(question, request.state.user.get("org_id")))


@router.post("/api/predictive-risk")
@require_auth
async def api_predictive(request: Request):
    return JSONResponse(await service.predictive_risk(request.state.user.get("org_id")))


@router.post("/api/training-gaps")
@require_auth
async def api_training_gaps(request: Request):
    return JSONResponse(await service.training_gap_detection(request.state.user.get("org_id")))


@router.post("/api/statutory-report")
@require_auth
async def api_statutory(request: Request, report_type: str = Form("nssa")):
    return JSONResponse(await service.statutory_report_generator(report_type, request.state.user.get("org_id")))
