"""AI routes (guide 23): /api/ai/chat + feature endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth
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
    return JSONResponse(await service.chat(question))


@router.post("/api/briefing")
@require_auth
async def api_briefing(request: Request):
    return JSONResponse(await service.daily_briefing())


@router.post("/api/incident-copilot/{incident_id}")
@require_auth
async def api_incident_copilot(request: Request, incident_id: int):
    return JSONResponse(await service.incident_copilot(incident_id))


@router.post("/api/root-cause/{incident_id}")
@require_auth
async def api_root_cause(request: Request, incident_id: int):
    return JSONResponse(await service.root_cause_assistant(incident_id))


@router.post("/api/predictive-risk")
@require_auth
async def api_predictive(request: Request):
    return JSONResponse(await service.predictive_risk())


@router.post("/api/training-gaps")
@require_auth
async def api_training_gaps(request: Request):
    return JSONResponse(await service.training_gap_detection())


@router.post("/api/statutory-report")
@require_auth
async def api_statutory(request: Request, report_type: str = Form("nssa")):
    return JSONResponse(await service.statutory_report_generator(report_type))
