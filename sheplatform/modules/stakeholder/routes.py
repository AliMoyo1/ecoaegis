"""Stakeholder Engagement routes (guide 20)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.stakeholder import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/stakeholders")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.stakeholder.access")
async def stakeholder_shell(request: Request):
    return templates.TemplateResponse(request, "stakeholder/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/stakeholders")
@require_auth
@require_capability("module.stakeholder.access")
async def api_stakeholders(request: Request):
    db = get_db()
    try:
        return JSONResponse({"stakeholders": data_service.list_stakeholders(db)})
    finally:
        db.close()


@router.post("/api/stakeholders")
@require_auth
@require_capability("module.stakeholder.access")
async def api_stakeholder_create(request: Request,
                                 name: str = Form(...),
                                 category: str = Form("community"),
                                 contact_person: str = Form(""),
                                 phone: str = Form(""),
                                 email: str = Form("")):
    db = get_db()
    try:
        stakeholder = data_service.create_stakeholder(
            db, name=name, category=category, contact_person=contact_person,
            phone=phone, email=email, org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "stakeholder": stakeholder}, status_code=201)
    finally:
        db.close()


@router.get("/api/engagements")
@require_auth
@require_capability("module.stakeholder.access")
async def api_engagements(request: Request, status: str = ""):
    db = get_db()
    try:
        return JSONResponse({"engagements": data_service.list_engagements(db, status=status or None)})
    finally:
        db.close()


@router.post("/api/engagements")
@require_auth
@require_capability("module.stakeholder.access")
async def api_engagement_create(request: Request,
                                stakeholder_id: int = Form(...),
                                engagement_issue: str = Form(...),
                                she_objectives: str = Form(""),
                                target_date: str = Form(""),
                                frequency: str = Form("")):
    db = get_db()
    try:
        engagement = data_service.create_engagement(
            db, stakeholder_id=stakeholder_id, engagement_issue=engagement_issue,
            she_objectives=she_objectives, target_date=target_date, frequency=frequency,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "engagement": engagement}, status_code=201)
    finally:
        db.close()


@router.post("/api/engagements/{engagement_id}/feedback")
@require_auth
@require_capability("module.stakeholder.access")
async def api_feedback(request: Request, engagement_id: int,
                       quarter: int = Form(...), feedback: str = Form(...)):
    db = get_db()
    try:
        return JSONResponse(data_service.record_quarterly_feedback(
            db, engagement_id, quarter, feedback))
    finally:
        db.close()


@router.post("/api/engagements/{engagement_id}/complete")
@require_auth
@require_capability("module.stakeholder.access")
async def api_complete(request: Request, engagement_id: int):
    db = get_db()
    try:
        return JSONResponse(data_service.complete_engagement(db, engagement_id))
    finally:
        db.close()
