"""Asset register + telemetry routes (guide C4). SPA shell + JSON API."""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.assets import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/assets")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.assets.access")
async def assets_shell(request: Request):
    return templates.TemplateResponse(request, "assets/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/list")
@require_auth
@require_capability("module.assets.access")
async def api_list(request: Request):
    db = get_db()
    try:
        return JSONResponse({"assets": data_service.list_assets(db, request.state.user.get("org_id"))})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
@require_capability("assets.manage")
async def api_create(request: Request, asset_ref: str = Form(...), name: str = Form(...),
                     asset_type: str = Form(...), install_date: str = Form(""),
                     service_interval_hours: str = Form(""), esg_kpi_code: str = Form("")):
    db = get_db()
    try:
        if asset_type not in ("generator", "vehicle", "tower_equipment", "other"):
            return JSONResponse({"ok": False, "message": "invalid asset_type"}, status_code=400)
        interval = None
        if service_interval_hours:
            try:
                interval = float(service_interval_hours)
            except ValueError:
                return JSONResponse({"ok": False, "message": "service_interval_hours must be a number"}, status_code=400)
        asset = data_service.create_asset(
            db, asset_ref=asset_ref, name=name, asset_type=asset_type, install_date=install_date,
            service_interval_hours=interval, esg_kpi_code=esg_kpi_code,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "asset": asset}, status_code=201)
    finally:
        db.close()


@router.get("/api/{asset_id}/readings")
@require_auth
@require_capability("module.assets.access")
async def api_readings(request: Request, asset_id: int):
    db = get_db()
    try:
        asset = data_service.get_asset(db, asset_id)
        org_id = request.state.user.get("org_id")
        if asset is None or (org_id and asset.get("org_id") and asset["org_id"] != org_id):
            return JSONResponse({"ok": False, "message": "not found"}, status_code=404)
        return JSONResponse({"readings": data_service.list_readings(db, asset_id)})
    finally:
        db.close()


@router.get("/api/maintenance")
@require_auth
@require_capability("module.assets.access")
async def api_maintenance_list(request: Request, status: str = ""):
    db = get_db()
    try:
        tasks = data_service.list_maintenance_tasks(db, request.state.user.get("org_id"), status or None)
        return JSONResponse({"tasks": tasks})
    finally:
        db.close()


@router.post("/api/maintenance/{task_id}/complete")
@require_auth
@require_capability("assets.manage")
async def api_maintenance_complete(request: Request, task_id: int):
    db = get_db()
    try:
        result = data_service.complete_maintenance(
            db, task_id, request.state.user.get("org_id"), request.state.user["id"])
        if not result["ok"]:
            return JSONResponse(result, status_code=404)
        return JSONResponse(result)
    finally:
        db.close()


@router.post("/api/api-keys")
@require_auth
@require_capability("assets.manage")
async def api_create_api_key(request: Request, name: str = Form(...)):
    db = get_db()
    try:
        result = data_service.create_api_key(
            db, name=name, org_id=request.state.user.get("org_id"), created_by=request.state.user["id"])
        return JSONResponse(result, status_code=201)
    finally:
        db.close()


@router.post("/api/telemetry")
async def api_telemetry(request: Request):
    """Guide C4: keyed inbound telemetry endpoint for a field device or
    gateway script. Authenticated via X-Asset-API-Key, same pattern as
    the already-audited /esg/api/ingest (modules/esg_kpi/routes.py) -
    not a session, so no @require_auth here.
    """
    key = request.headers.get("X-Asset-API-Key", "")
    if not key:
        return JSONResponse({"ok": False, "message": "missing api key"}, status_code=401)
    db = get_db()
    try:
        key_record = data_service.verify_api_key(db, key)
        if not key_record:
            return JSONResponse({"ok": False, "message": "invalid api key"}, status_code=401)
        if "assets.telemetry" not in json.loads(key_record["scopes"]):
            return JSONResponse({"ok": False, "message": "insufficient scope"}, status_code=403)
        payload = await request.json()
        asset_ref = payload.get("asset_ref", "")
        if not asset_ref:
            return JSONResponse({"ok": False, "message": "asset_ref is required"}, status_code=400)
        result = data_service.record_telemetry(
            db, asset_ref=asset_ref, run_hours=payload.get("run_hours"),
            fuel_level_pct=payload.get("fuel_level_pct"), recorded_at=payload.get("recorded_at"),
            org_id=key_record["org_id"])
        if not result["ok"]:
            return JSONResponse(result, status_code=400)
        return JSONResponse(result, status_code=201)
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    finally:
        db.close()
