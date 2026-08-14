"""Document control routes."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.documents import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_class=HTMLResponse)
@require_auth
async def documents_shell(request: Request):
    return templates.TemplateResponse(request, "documents/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/list")
@require_auth
async def api_list(request: Request, doc_type: str = "", status: str = ""):
    db = get_db()
    try:
        items = data_service.list_documents(
            db, doc_type=doc_type or None, status=status or None,
            org_id=request.state.user.get("org_id"))
        return JSONResponse({"documents": items})
    finally:
        db.close()


@router.get("/api/{doc_id}/unacknowledged")
@require_auth
@require_capability("documents.manage")
async def api_unacknowledged(request: Request, doc_id: int):
    db = get_db()
    try:
        return JSONResponse({"users": data_service.unacknowledged_users(db, doc_id)})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
@require_capability("documents.manage")
async def api_create(request: Request, title: str = Form(...), doc_type: str = Form("sop"),
                     description: str = Form(""), version: str = Form("1.0"),
                     file_path: str = Form(""), review_due_date: str = Form(""),
                     supersedes: int = Form(0)):
    db = get_db()
    try:
        doc = data_service.create_document(
            db, title=title, doc_type=doc_type, description=description, version=version,
            file_path=file_path, review_due_date=review_due_date,
            supersedes=supersedes or None, created_by=request.state.user["id"],
            org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "document": doc})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()


@router.post("/api/{doc_id}/submit")
@require_auth
@require_capability("documents.manage")
async def api_submit(request: Request, doc_id: int):
    db = get_db()
    try:
        doc = data_service.submit_for_review(db, doc_id, request.state.user["id"])
        return JSONResponse({"ok": True, "document": doc})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()


@router.post("/api/{doc_id}/approve")
@require_auth
@require_capability("documents.approve")
async def api_approve(request: Request, doc_id: int):
    db = get_db()
    try:
        doc = data_service.approve_document(db, doc_id, request.state.user["id"])
        return JSONResponse({"ok": True, "document": doc})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()


@router.post("/api/{doc_id}/acknowledge")
@require_auth
async def api_acknowledge(request: Request, doc_id: int):
    db = get_db()
    try:
        out = data_service.acknowledge_document(db, doc_id, request.state.user["id"])
        return JSONResponse({"ok": True, **out})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()


@router.post("/api/ask")
@require_auth
async def api_ask(request: Request, question: str = Form(...)):
    """Guide C3 document Q&A. Same broad read access as list/view - anyone
    who can read the SOP library can ask it a question."""
    db = get_db()
    try:
        result = await data_service.ask_sops(db, question, request.state.user.get("org_id"))
        return JSONResponse(result)
    finally:
        db.close()
