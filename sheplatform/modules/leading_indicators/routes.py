"""Leading-indicator "sites to watch" routes (guide C3)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.leading_indicators import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/leading-indicators")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.leading_indicators.access")
async def shell(request: Request):
    return templates.TemplateResponse(request, "leading_indicators/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/sites-to-watch")
@require_auth
@require_capability("module.leading_indicators.access")
async def api_sites_to_watch(request: Request):
    db = get_db()
    try:
        sites = data_service.per_site_scores(db, request.state.user.get("org_id"))
        return JSONResponse({"sites": sites})
    finally:
        db.close()


@router.post("/api/explain/{site_id}")
@require_auth
@require_capability("module.leading_indicators.access")
async def api_explain(request: Request, site_id: int):
    db = get_db()
    try:
        result = await data_service.explain_site(db, site_id, request.state.user.get("org_id"))
        if not result["ok"]:
            return JSONResponse(result, status_code=404)
        return JSONResponse(result)
    finally:
        db.close()
