"""Observations routes."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.observations import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/observations", tags=["observations"])


@router.get("", response_class=HTMLResponse)
@require_auth
async def observations_shell(request: Request):
    return templates.TemplateResponse(request, "observations/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/list")
@require_auth
async def api_list(request: Request, status: str = "", severity: str = ""):
    db = get_db()
    try:
        items = data_service.list_observations(
            db, status=status or None, severity=severity or None,
            org_id=request.state.user.get("org_id"))
        return JSONResponse({"observations": items})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
async def api_create(request: Request, obs_type: str = Form(...), title: str = Form(...),
                     description: str = Form(""), location: str = Form(""),
                     severity: str = Form("low")):
    db = get_db()
    try:
        obs = data_service.create_observation(
            db, obs_type=obs_type, title=title, description=description,
            location=location, severity=severity,
            reported_by=request.state.user["id"],
            org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "observation": obs})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()


@router.post("/api/{obs_id}/acknowledge")
@require_auth
@require_capability("observations.triage")
async def api_acknowledge(request: Request, obs_id: int):
    db = get_db()
    try:
        obs = data_service.acknowledge_observation(db, obs_id, request.state.user["id"])
        return JSONResponse({"ok": True, "observation": obs})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()


@router.post("/api/{obs_id}/raise-capa")
@require_auth
@require_capability("observations.triage")
async def api_raise_capa(request: Request, obs_id: int):
    db = get_db()
    try:
        out = data_service.raise_corrective_action(
            db, obs_id, request.state.user["id"], org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, **out})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()


@router.post("/api/{obs_id}/close")
@require_auth
@require_capability("observations.triage")
async def api_close(request: Request, obs_id: int, resolution: str = Form("")):
    db = get_db()
    try:
        obs = data_service.close_observation(db, obs_id, request.state.user["id"], resolution)
        return JSONResponse({"ok": True, "observation": obs})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()
