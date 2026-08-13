"""Site benchmarking routes."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.benchmark import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/benchmark", tags=["benchmark"])


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.benchmark.access")
async def benchmark_shell(request: Request):
    return templates.TemplateResponse(request, "benchmark/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/summary")
@require_auth
@require_capability("module.benchmark.access")
async def api_summary(request: Request):
    db = get_db()
    try:
        return JSONResponse(data_service.benchmark_summary(db))
    finally:
        db.close()
