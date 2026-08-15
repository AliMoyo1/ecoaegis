"""Geographic map routes (guide C1). SPA shell + JSON API."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.config import settings
from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.core.rbac import has_capability
from sheplatform.database import get_db
from sheplatform.modules.map import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/map")
PRIVATE_API_HEADERS = {"Cache-Control": "private, no-store"}


def _json(content: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content, status_code=status_code, headers=PRIVATE_API_HEADERS)


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.map.access")
async def map_shell(request: Request):
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
        org_id = request.state.user.get("org_id")
        incidents = data_service.list_incident_points(
            db, org_id, severity=severity or None, incident_type=type or None,
            since=since or None)
        sites = data_service.list_site_points(db, org_id)
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
        return _json(result)
    finally:
        db.close()
