"""CAPA routes."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.capa import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/capa", tags=["capa"])


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.capa.access")
async def capa_shell(request: Request):
    return templates.TemplateResponse(request, "capa/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/users")
@require_auth
@require_capability("module.capa.access")
async def api_users(request: Request):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, email, first_name, last_name, role_key FROM users "
            "WHERE is_active = TRUE ORDER BY first_name").fetchall()
        return JSONResponse({"users": [dict(r) for r in rows]})
    finally:
        db.close()


@router.get("/api/list")
@require_auth
@require_capability("module.capa.access")
async def api_list(request: Request, status: str = ""):
    db = get_db()
    try:
        items = data_service.list_actions(db, status=status or None,
                                          org_id=request.state.user.get("org_id"))
        return JSONResponse({"actions": items})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
@require_capability("capa.create")
async def api_create(request: Request, title: str = Form(...), description: str = Form(""),
                     source_type: str = Form(...), source_id: int = Form(0),
                     priority: str = Form("medium"), assigned_to: int = Form(...),
                     due_date: str = Form("")):
    db = get_db()
    try:
        action = data_service.create_action(
            db, title=title, description=description, source_type=source_type,
            source_id=source_id, priority=priority, assigned_to=assigned_to,
            due_date=due_date, created_by=request.state.user["id"],
            org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "action": action})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()


@router.post("/api/{action_id}/start")
@require_auth
async def api_start(request: Request, action_id: int):
    db = get_db()
    try:
        action = data_service.start_action(db, action_id, request.state.user["id"])
        return JSONResponse({"ok": True, "action": action})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()


@router.post("/api/{action_id}/complete")
@require_auth
async def api_complete(request: Request, action_id: int, note: str = Form("")):
    db = get_db()
    try:
        action = data_service.complete_action(db, action_id, request.state.user["id"], note)
        return JSONResponse({"ok": True, "action": action})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()


@router.post("/api/{action_id}/verify")
@require_auth
@require_capability("capa.verify")
async def api_verify(request: Request, action_id: int, note: str = Form("")):
    db = get_db()
    try:
        action = data_service.verify_action(db, action_id, request.state.user["id"], note)
        return JSONResponse({"ok": True, "action": action})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()
