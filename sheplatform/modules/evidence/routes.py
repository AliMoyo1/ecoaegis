"""Evidence vault routes (guide 4.13)."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth
from sheplatform.database import get_db
from sheplatform.modules.evidence import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/evidence")


@router.get("", response_class=HTMLResponse)
@require_auth
async def evidence_shell(request: Request):
    return templates.TemplateResponse(request, "evidence/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/list")
@require_auth
async def api_list(request: Request, entity_type: str = "", entity_id: int | None = None):
    db = get_db()
    try:
        items = data_service.list_evidence(db, entity_type=entity_type or None,
                                           entity_id=entity_id)
        return JSONResponse({"evidence": items})
    finally:
        db.close()


@router.post("/api/upload")
@require_auth
async def api_upload(request: Request,
                     entity_type: str = Form(...),
                     entity_id: int = Form(...),
                     file: UploadFile = File(...)):
    db = get_db()
    try:
        content = await file.read()
        if len(content) > 5 * 1024 * 1024:  # 5 MB limit (guide 5.5)
            return JSONResponse({"ok": False, "message": "file too large (max 5 MB)"},
                                status_code=400)
        ev = data_service.store_evidence(
            db, entity_type=entity_type, entity_id=entity_id,
            original_name=file.filename or "file", file_bytes=content,
            mime_type=file.content_type or "",
            uploaded_by=request.state.user["id"],
            org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "evidence": ev}, status_code=201)
    finally:
        db.close()


@router.post("/api/{evidence_id}/verify")
@require_auth
async def api_verify(request: Request, evidence_id: int):
    db = get_db()
    try:
        ok, msg = data_service.verify_file(db, evidence_id)
        return JSONResponse({"ok": ok, "message": msg})
    finally:
        db.close()
