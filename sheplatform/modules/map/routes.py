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
from sheplatform.modules.map import (
    coordinate_import_service,
    data_service,
    layer_service,
    site_resolution_service,
)
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


def _authorized_layer_keys(user: dict) -> list[str]:
    return [
        key for key, spec in layer_service.LAYER_REGISTRY.items()
        if has_capability(user, spec.capability)
    ]


def _layer_filters(*, status: str = "", type: str = "", severity: str = "",
                   since: str = "") -> dict[str, str | None]:
    return {
        "status": status or None,
        "type": type or None,
        "severity": severity or None,
        "since": since or None,
    }


@router.get("/api/manifest")
@require_auth
@require_capability("module.map.access")
async def api_layer_manifest(request: Request, bbox: str = ""):
    try:
        bounds = layer_service.parse_bbox(bbox)
    except ValueError as exc:
        return _json({"detail": str(exc)}, status_code=400)
    return _json(layer_service.manifest_for(
        _authorized_layer_keys(request.state.user), bounds))


@router.get("/api/layer/{layer_key}")
@require_auth
@require_capability("module.map.access")
async def api_layer(request: Request, layer_key: str, bbox: str = "", limit: int = 500,
                    min_lng: str = "", min_lat: str = "", max_lng: str = "",
                    max_lat: str = "",
                    status: str = "", type: str = "", severity: str = "",
                    since: str = ""):
    spec = layer_service.LAYER_REGISTRY.get(layer_key)
    if spec is None:
        return _json({"detail": "Layer not found"}, status_code=404)
    if not has_capability(request.state.user, spec.capability):
        return _json({"detail": "Forbidden"}, status_code=403)
    try:
        bounds = (layer_service.parse_bbox(bbox) if bbox else
                  layer_service.parse_bbox_values(min_lng, min_lat, max_lng, max_lat))
    except ValueError as exc:
        return _json({"detail": str(exc)}, status_code=400)
    db = get_db()
    try:
        try:
            result = layer_service.get_layer_collection(
                db, layer_key=layer_key,
                org_id=request.state.user.get("org_id"), bbox=bounds, limit=limit,
                filters=_layer_filters(status=status, type=type, severity=severity, since=since),
            )
        except ValueError as exc:
            return _json({"detail": str(exc)}, status_code=400)
        return _json(result)
    finally:
        db.close()


@router.get("/api/facility/{site_id}")
@require_auth
@require_capability("module.map.access")
async def api_facility(request: Request, site_id: int):
    db = get_db()
    try:
        count_layers = [
            key for key, spec in layer_service.LAYER_REGISTRY.items()
            if key != "facilities" and has_capability(request.state.user, spec.capability)
        ]
        result = layer_service.get_facility_detail(
            db, site_id=site_id, org_id=request.state.user.get("org_id"),
            count_layers=count_layers)
        if result is None:
            return _json({"detail": "Facility not found"}, status_code=404)
        return _json(result)
    finally:
        db.close()


@router.get("/api/unlocated/{layer_key}")
@require_auth
@require_capability("module.map.access")
async def api_unlocated(request: Request, layer_key: str, limit: int = 100,
                        status: str = "", type: str = "", severity: str = "",
                        since: str = ""):
    spec = layer_service.LAYER_REGISTRY.get(layer_key)
    if spec is None:
        return _json({"detail": "Layer not found"}, status_code=404)
    if not has_capability(request.state.user, spec.capability):
        return _json({"detail": "Forbidden"}, status_code=403)
    db = get_db()
    try:
        try:
            result = layer_service.get_unlocated_records(
                db, layer_key=layer_key, org_id=request.state.user.get("org_id"),
                limit=limit,
                filters=_layer_filters(status=status, type=type, severity=severity, since=since),
            )
        except ValueError as exc:
            return _json({"detail": str(exc)}, status_code=400)
        return _json(result)
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


def _resolution_write_response(result: dict) -> JSONResponse:
    if result.get("ok"):
        return _json(result)
    message = result.get("message", "site resolution could not be completed")
    if message == site_resolution_service.RECORD_NOT_FOUND_MESSAGE:
        status_code = 404
    elif message == site_resolution_service.RECORD_ALREADY_LINKED_MESSAGE:
        status_code = 409
    else:
        status_code = 400
    return _json(result, status_code=status_code)


@router.get("/api/site-resolution")
@require_auth
@require_capability("module.settings.access")
async def api_site_resolution_queue(request: Request, status: str = "pending",
                                    limit: int = 100):
    db = get_db()
    try:
        try:
            result = site_resolution_service.list_resolution_queue(
                db, org_id=request.state.user.get("org_id"),
                review_status=status, limit=limit,
            )
        except ValueError as exc:
            return _json({"ok": False, "message": str(exc)}, status_code=400)
        return _json(result)
    finally:
        db.close()


@router.post("/api/site-resolution/{record_type}/{record_id}/resolve")
@require_auth
@require_capability("module.settings.access")
async def api_site_resolution_apply(request: Request, record_type: str, record_id: int,
                                    site_id: int = Form(...),
                                    decision_note: str = Form("")):
    db = get_db()
    try:
        try:
            result = site_resolution_service.resolve_record(
                db, record_type=record_type, record_id=record_id, site_id=site_id,
                org_id=request.state.user.get("org_id"),
                reviewed_by=request.state.user["id"], decision_note=decision_note,
            )
        except ValueError as exc:
            return _json({"ok": False, "message": str(exc)}, status_code=400)
        return _resolution_write_response(result)
    finally:
        db.close()


@router.post("/api/site-resolution/{record_type}/{record_id}/skip")
@require_auth
@require_capability("module.settings.access")
async def api_site_resolution_skip(request: Request, record_type: str, record_id: int,
                                   decision_note: str = Form("")):
    db = get_db()
    try:
        try:
            result = site_resolution_service.skip_record(
                db, record_type=record_type, record_id=record_id,
                org_id=request.state.user.get("org_id"),
                reviewed_by=request.state.user["id"], decision_note=decision_note,
            )
        except ValueError as exc:
            return _json({"ok": False, "message": str(exc)}, status_code=400)
        return _resolution_write_response(result)
    finally:
        db.close()


@router.post("/api/site-resolution/{record_type}/{record_id}/create-site")
@require_auth
@require_capability("module.settings.access")
async def api_site_resolution_create_site(
    request: Request,
    record_type: str,
    record_id: int,
    site_code: str = Form(...),
    site_name: str = Form(...),
    city: str = Form(""),
    region: str = Form(""),
    site_type: str = Form("facility"),
):
    db = get_db()
    try:
        try:
            result = site_resolution_service.create_site_and_resolve(
                db, record_type=record_type, record_id=record_id,
                site_code=site_code, site_name=site_name, city=city, region=region,
                site_type=site_type, org_id=request.state.user.get("org_id"),
                reviewed_by=request.state.user["id"],
            )
        except ValueError as exc:
            return _json({"ok": False, "message": str(exc)}, status_code=400)
        return _resolution_write_response(result)
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
