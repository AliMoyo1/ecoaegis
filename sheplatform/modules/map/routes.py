"""Geographic map routes (guide C1). SPA shell + JSON API."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.config import settings
from sheplatform.core.audit import log_audit
from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.map import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/map")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.map.access")
async def map_shell(request: Request):
    return templates.TemplateResponse(request, "map/templates/index.html", {
        "user": request.state.user,
        "tile_url": settings.MAP_TILE_URL_TEMPLATE,
        "tile_attribution": settings.MAP_TILE_ATTRIBUTION,
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
        return JSONResponse({"incidents": incidents, "sites": sites})
    finally:
        db.close()


@router.post("/api/sites/{site_id}/coords")
@require_auth
@require_capability("module.settings.access")
async def api_set_site_coords(request: Request, site_id: int,
                              latitude: str = Form(...), longitude: str = Form(...)):
    db = get_db()
    try:
        org_id = request.state.user.get("org_id")
        try:
            lat = float(latitude)
            lng = float(longitude)
        except ValueError:
            return JSONResponse({"ok": False, "message": "latitude/longitude must be numbers"},
                               status_code=400)
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return JSONResponse({"ok": False, "message": "coordinates out of range"},
                               status_code=400)
        result = data_service.set_site_coords(db, site_id, lat, lng, org_id)
        if not result["ok"]:
            return JSONResponse(result, status_code=404)
        log_audit(db, request.state.user["id"], org_id, "site.set_coords",
                  "sites", site_id, new_value={"latitude": lat, "longitude": lng})
        return JSONResponse(result)
    finally:
        db.close()
