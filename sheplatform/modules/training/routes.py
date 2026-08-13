"""SHET&A - Training routes (guide 15)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.training import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/training")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.training.access")
async def training_shell(request: Request):
    return templates.TemplateResponse(request, "training/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/needs")
@require_auth
@require_capability("module.training.access")
async def api_needs(request: Request, status: str = ""):
    db = get_db()
    try:
        return JSONResponse({"needs": data_service.list_needs(db, status=status or None, org_id=request.state.user.get("org_id"))})
    finally:
        db.close()


@router.post("/api/needs")
@require_auth
@require_capability("module.training.access")
async def api_need_create(request: Request,
                          title: str = Form(...),
                          description: str = Form(""),
                          source_trigger: str = Form("gap_analysis")):
    db = get_db()
    try:
        need = data_service.create_need(
            db, title=title, description=description, source_trigger=source_trigger,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "need": need}, status_code=201)
    finally:
        db.close()


@router.get("/api/sessions")
@require_auth
@require_capability("module.training.access")
async def api_sessions(request: Request):
    db = get_db()
    try:
        return JSONResponse({"sessions": data_service.list_sessions(db)})
    finally:
        db.close()


@router.post("/api/sessions")
@require_auth
@require_capability("module.training.access")
async def api_session_create(request: Request,
                             title: str = Form(...),
                             scheduled_date: str = Form(...),
                             need_id: int | None = Form(None),
                             trainer: str = Form(""),
                             delivery_method: str = Form("internal")):
    db = get_db()
    try:
        session = data_service.schedule_session(
            db, need_id=need_id, title=title, scheduled_date=scheduled_date,
            trainer=trainer, delivery_method=delivery_method,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "session": session}, status_code=201)
    finally:
        db.close()


@router.post("/api/sessions/{session_id}/attendance")
@require_auth
@require_capability("module.training.access")
async def api_attendance(request: Request, session_id: int,
                         user_id: int = Form(...),
                         attended: str = Form("true"),
                         competency_score: float | None = Form(None),
                         evaluation: str = Form("")):
    db = get_db()
    try:
        record = data_service.record_attendance(
            db, session_id=session_id, user_id=user_id,
            attended=attended.lower() == "true",
            competency_score=competency_score, evaluation=evaluation)
        return JSONResponse({"ok": True, "attendance": record})
    finally:
        db.close()


@router.get("/api/competency/{user_id}")
@require_auth
@require_capability("module.training.access")
async def api_competency(request: Request, user_id: int):
    db = get_db()
    try:
        return JSONResponse({"competency": data_service.get_competency(db, user_id)})
    finally:
        db.close()
