"""Compliance obligations routes."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.compliance import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/compliance", tags=["compliance"])


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.compliance.access")
async def compliance_shell(request: Request):
    return templates.TemplateResponse(request, "compliance/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/list")
@require_auth
@require_capability("module.compliance.access")
async def api_list(request: Request, status: str = "", regulator: str = ""):
    db = get_db()
    try:
        items = data_service.list_obligations(
            db, status=status or None, regulator=regulator or None,
            org_id=request.state.user.get("org_id"))
        return JSONResponse({"obligations": items})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
@require_capability("compliance.manage")
async def api_create(request: Request, regulation: str = Form(...),
                     obligation: str = Form(...), regulator: str = Form(...),
                     owner_id: int = Form(...), frequency: str = Form("annual"),
                     next_due_date: str = Form("")):
    db = get_db()
    try:
        ob = data_service.create_obligation(
            db, regulation=regulation, obligation=obligation, regulator=regulator,
            owner_id=owner_id, frequency=frequency, next_due_date=next_due_date,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "obligation": ob})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()


@router.post("/api/{ob_id}/compliant")
@require_auth
@require_capability("compliance.manage")
async def api_compliant(request: Request, ob_id: int, evidence: str = Form("")):
    db = get_db()
    try:
        ob = data_service.mark_compliant(db, ob_id, request.state.user["id"], evidence)
        return JSONResponse({"ok": True, "obligation": ob})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()
