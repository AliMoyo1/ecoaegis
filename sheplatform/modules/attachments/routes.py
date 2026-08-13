"""Attachments generic routes (guide 3.1)."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from sheplatform.core.attachments import (
    delete_attachment,
    get_attachment,
    list_attachments,
    save_attachment,
)
from sheplatform.core.middleware import require_auth
from sheplatform.database import get_db

router = APIRouter(prefix="/attachments")

_ALLOWED_ENTITY_TYPES = {"incident", "observation", "inspection", "chemical", "permit", "risk"}


def _json_response(ok: bool, **kwargs) -> JSONResponse:
    payload = {"ok": ok}
    payload.update(kwargs)
    return JSONResponse(payload)


@router.get("/api/serve/{attachment_id}")
@require_auth
async def serve(request: Request, attachment_id: int):
    db = get_db()
    try:
        att = get_attachment(db, attachment_id, request.state.user.get("org_id"))
        if att is None:
            return JSONResponse({"detail": "not found"}, status_code=404)
        from sheplatform.core.attachments import ATTACHMENTS_DIR
        path = ATTACHMENTS_DIR / att["file_name"]
        if not path.exists():
            return JSONResponse({"detail": "not found"}, status_code=404)
        return FileResponse(
            str(path), media_type=att["mime_type"] or "application/octet-stream",
            filename=att["original_name"])
    finally:
        db.close()


@router.post("/api/file/{attachment_id}/delete")
@require_auth
async def remove(request: Request, attachment_id: int):
    db = get_db()
    try:
        ok = delete_attachment(
            db, attachment_id, request.state.user.get("org_id"),
            user_id=request.state.user["id"])
        if not ok:
            raise HTTPException(status_code=404, detail="not found")
        return _json_response(True)
    finally:
        db.close()


@router.post("/api/{entity_type}/{entity_id}")
@require_auth
async def upload(request: Request, entity_type: str, entity_id: int,
                 file: UploadFile = File(...), kind: str = Form("file")):
    if entity_type not in _ALLOWED_ENTITY_TYPES:
        raise HTTPException(status_code=400, detail=f"unsupported entity_type: {entity_type}")
    db = get_db()
    try:
        content = await file.read()
        att = save_attachment(
            db, entity_type=entity_type, entity_id=entity_id,
            file_bytes=content, original_name=file.filename or "file",
            mime_type=file.content_type or "", kind=kind,
            org_id=request.state.user.get("org_id"),
            uploaded_by=request.state.user["id"])
        return _json_response(True, attachment=att)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.get("/api/{entity_type}/{entity_id}")
@require_auth
async def listing(request: Request, entity_type: str, entity_id: int):
    if entity_type not in _ALLOWED_ENTITY_TYPES:
        raise HTTPException(status_code=400, detail=f"unsupported entity_type: {entity_type}")
    db = get_db()
    try:
        items = list_attachments(
            db, entity_type=entity_type, entity_id=entity_id,
            org_id=request.state.user.get("org_id"))
        return _json_response(True, attachments=items)
    finally:
        db.close()
