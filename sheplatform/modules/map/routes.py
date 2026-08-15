"""Geographic map routes (guide C1). SPA shell + JSON API."""
from __future__ import annotations

import logging
from time import perf_counter

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.config import settings
from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.core.rbac import has_capability
from sheplatform.database import get_db
from sheplatform.modules.map import coordinate_import_service, data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/map")
PRIVATE_API_HEADERS = {"Cache-Control": "private, no-store"}
logger = logging.getLogger(__name__)


def _json(content: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content, status_code=status_code, headers=PRIVATE_API_HEADERS)


def _record_metrics_safely(db, metrics: list[dict]) -> None:
    """Metrics must never make an operational map request fail."""
    try:
        for metric in metrics:
            data_service.record_map_metric(db, **metric, commit=False)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Private map metric recording failed", exc_info=True)


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.map.access")
async def map_shell(request: Request):
    db = get_db()
    try:
        _record_metrics_safely(db, [{
            "event_type": "map_session",
            "org_id": request.state.user.get("org_id"),
        }])
    finally:
        db.close()
    return templates.TemplateResponse(request, "map/templates/index.html", {
        "user": request.state.user,
        "can_edit_coordinates": has_capability(request.state.user, "module.settings.access"),
        "map_engine": settings.MAP_ENGINE,
        "tile_url": settings.MAP_TILE_URL_TEMPLATE,
        "tile_attribution": settings.MAP_TILE_ATTRIBUTION,
        "map_center_lat": settings.MAP_CENTER_LAT,
        "map_center_lng": settings.MAP_CENTER_LNG,
        "map_default_zoom": settings.MAP_DEFAULT_ZOOM,
        "map_min_zoom": settings.MAP_MIN_ZOOM,
        "map_max_zoom": settings.MAP_MAX_ZOOM,
        "map_request_debounce_ms": settings.MAP_REQUEST_DEBOUNCE_MS,
    })


@router.get("/api/points")
@require_auth
@require_capability("module.map.access")
async def api_points(request: Request, severity: str = "", type: str = "", since: str = ""):
    db = get_db()
    try:
        started = perf_counter()
        org_id = request.state.user.get("org_id")
        incidents = data_service.list_incident_points(
            db, org_id, severity=severity or None, incident_type=type or None,
            since=since or None)
        sites = data_service.list_site_points(db, org_id)
        duration_ms = (perf_counter() - started) * 1000
        unlocated = data_service.count_unlocated_sites(db, org_id)
        _record_metrics_safely(db, [
            {"event_type": "layer_request", "layer_name": "incidents",
             "feature_count": len(incidents), "duration_ms": duration_ms,
             "org_id": org_id},
            {"event_type": "layer_request", "layer_name": "sites",
             "feature_count": len(sites), "unlocated_count": unlocated,
             "duration_ms": duration_ms, "org_id": org_id},
        ])
        return _json({"incidents": incidents, "sites": sites})
    finally:
        db.close()


@router.get("/api/sites")
@require_auth
@require_capability("module.settings.access")
async def api_coordinate_sites(request: Request):
    db = get_db()
    try:
        sites = data_service.list_sites_for_coordinate_admin(
            db, request.state.user.get("org_id"))
        return _json({"sites": sites})
    finally:
        db.close()


@router.post("/api/sites/{site_id}/coords")
@require_auth
@require_capability("module.settings.access")
async def api_set_site_coords(request: Request, site_id: int,
                              latitude: str = Form(...), longitude: str = Form(...),
                              source: str = Form("manual"),
                              accuracy_m: str = Form("")):
    db = get_db()
    try:
        org_id = request.state.user.get("org_id")
        try:
            result = data_service.set_site_coords(
                db, site_id=site_id, latitude=latitude, longitude=longitude,
                source=source, accuracy_m=accuracy_m or None,
                updated_by=request.state.user["id"], org_id=org_id)
        except ValueError as exc:
            return _json({"ok": False, "message": str(exc)}, status_code=400)
        if not result["ok"]:
            return _json(result, status_code=404)
        _record_metrics_safely(db, [{
            "event_type": "coordinate_save", "org_id": org_id,
            "coordinate_source": result["site"]["coordinate_source"],
        }])
        return _json(result)
    finally:
        db.close()


@router.delete("/api/sites/{site_id}/coords")
@require_auth
@require_capability("module.settings.access")
async def api_clear_site_coords(request: Request, site_id: int):
    db = get_db()
    try:
        result = data_service.clear_site_coords(
            db, site_id=site_id, updated_by=request.state.user["id"],
            org_id=request.state.user.get("org_id"))
        if not result["ok"]:
            return _json(result, status_code=404)
        _record_metrics_safely(db, [{
            "event_type": "coordinate_clear",
            "org_id": request.state.user.get("org_id"),
        }])
        return _json(result)
    finally:
        db.close()


@router.post("/api/metrics/provider-failure")
@require_auth
@require_capability("module.map.access")
async def api_provider_failure(request: Request):
    db = get_db()
    try:
        _record_metrics_safely(db, [{
            "event_type": "provider_failure",
            "org_id": request.state.user.get("org_id"),
        }])
        return _json({"ok": True})
    finally:
        db.close()


@router.get("/api/metrics/summary")
@require_auth
@require_capability("module.settings.access")
async def api_metrics_summary(request: Request):
    db = get_db()
    try:
        return _json(data_service.map_metrics_summary(
            db, request.state.user.get("org_id")))
    finally:
        db.close()


@router.post("/api/coordinate-imports/preview")
@require_auth
@require_capability("module.settings.access")
async def api_coordinate_import_preview(request: Request,
                                        file: UploadFile = File(...)):
    db = get_db()
    try:
        contents = await file.read(coordinate_import_service.MAX_IMPORT_BYTES + 1)
        try:
            result = coordinate_import_service.preview_coordinate_import(
                db, file_bytes=contents, org_id=request.state.user.get("org_id"),
                created_by=request.state.user["id"])
        except ValueError as exc:
            return _json({"ok": False, "message": str(exc)}, status_code=400)
        return _json(result, status_code=201)
    finally:
        db.close()


@router.get("/api/coordinate-imports/{import_id}")
@require_auth
@require_capability("module.settings.access")
async def api_coordinate_import(request: Request, import_id: int):
    db = get_db()
    try:
        result = coordinate_import_service.get_coordinate_import(
            db, import_id=import_id, org_id=request.state.user.get("org_id"))
        return _json(result, status_code=200 if result["ok"] else 404)
    finally:
        db.close()


@router.post("/api/coordinate-imports/{import_id}/commit")
@require_auth
@require_capability("module.settings.access")
async def api_coordinate_import_commit(request: Request, import_id: int,
                                       overwrite_existing: bool = Form(False)):
    db = get_db()
    try:
        try:
            result = coordinate_import_service.commit_coordinate_import(
                db, import_id=import_id, org_id=request.state.user.get("org_id"),
                updated_by=request.state.user["id"],
                overwrite_existing=overwrite_existing)
        except ValueError as exc:
            return _json({"ok": False, "message": str(exc)}, status_code=400)
        if result["ok"]:
            return _json(result)
        if result.get("requires_overwrite"):
            return _json(result, status_code=409)
        status_code = 404 if result["message"] == "coordinate import not found" else 400
        return _json(result, status_code=status_code)
    finally:
        db.close()
