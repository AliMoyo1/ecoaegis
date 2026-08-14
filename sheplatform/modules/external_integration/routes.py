"""B5 external integration and portal submission routes.

Routes:
- GET  /integrations                 - integration dashboard page
- GET  /integrations/api/endpoints   - list configured endpoints
- POST /integrations/api/endpoints   - create an endpoint
- POST /integrations/api/endpoints/{id}/secrets - store a secret
- POST /webhooks/themisiq            - receive ThemisIQ signed webhooks
- GET  /integrations/api/channels    - list submission channels
- POST /integrations/api/channels/seed - seed default channels
- POST /integrations/api/reports/{report_id}/submit/{channel_key} - submit report
- GET  /integrations/api/submissions  - list submission deliveries
- POST /integrations/api/submissions/{delivery_id}/status - update delivery status
- POST /integrations/api/queue/process - process pending queue (admin/scheduler)
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.templating import render
from sheplatform.database import get_db
from sheplatform.modules.external_integration import data_service

router = APIRouter()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
@router.get("/integrations", response_class=HTMLResponse)
@require_auth
@require_capability("module.integrations.access")
async def integrations_page(request: Request):
    return render(request, "external_integration/templates/index.html", {"user": request.state.user})


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/integrations/api/endpoints")
@require_auth
@require_capability("module.integrations.access")
async def list_endpoints(request: Request):
    with get_db() as db:
        org_id = request.state.user.get("org_id")
        rows = db.execute(
            """SELECT id, endpoint_key, name, system_type, direction, base_url,
                      auth_type, headers, timeout_seconds, rate_limit_per_minute,
                      active, org_id, created_by, created_at
               FROM integration_endpoints WHERE org_id=%s ORDER BY system_type, name""",
            (org_id,),
        ).fetchall()
        return {"endpoints": [dict(r) for r in rows]}


@router.post("/integrations/api/endpoints")
@require_auth
@require_capability("module.integrations.manage")
async def create_endpoint(request: Request):
    body = await request.json()
    endpoint_id = data_service.create_endpoint(
        endpoint_key=body.get("endpoint_key"),
        name=body.get("name"),
        system_type=body.get("system_type"),
        direction=body.get("direction", "outbound"),
        base_url=body.get("base_url"),
        auth_type=body.get("auth_type", "api_key"),
        auth_config=body.get("auth_config") or {},
        headers=body.get("headers") or {},
        timeout_seconds=body.get("timeout_seconds", 30),
        rate_limit_per_minute=body.get("rate_limit_per_minute", 60),
        org_id=request.state.user["org_id"],
        created_by=request.state.user["id"],
    )
    return {"id": endpoint_id, "status": "created"}


@router.post("/integrations/api/endpoints/{endpoint_id}/secrets")
@require_auth
@require_capability("module.integrations.manage")
async def set_endpoint_secret(request: Request, endpoint_id: int):
    body = await request.json()
    with get_db() as db:
        row = db.execute(
            "SELECT id FROM integration_endpoints WHERE id=%s AND org_id=%s",
            (endpoint_id, request.state.user["org_id"]),
        ).fetchone()
        if not row:
            return JSONResponse({"detail": "Endpoint not found"}, status_code=404)
    data_service.upsert_endpoint_secret(
        endpoint_id, body.get("secret_name", "api_key"), body.get("secret_value", "")
    )
    return {"status": "secret stored"}


# ---------------------------------------------------------------------------
# ThemisIQ inbound webhook
# ---------------------------------------------------------------------------
@router.post("/webhooks/themisiq")
async def themisiq_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-ThemisIQ-Signature", "")
    # Look up secret by a configured themisiq inbound endpoint
    with get_db() as db:
        ep = db.execute(
            "SELECT * FROM integration_endpoints WHERE system_type='themisiq' AND direction IN ('inbound','bidirectional') LIMIT 1"
        ).fetchone()
        secret = ""
        if ep:
            sec = db.execute(
                "SELECT secret_value FROM integration_secrets WHERE endpoint_id=%s AND secret_name=%s",
                (ep["id"], "api_key"),
            ).fetchone()
            secret = sec["secret_value"] if sec else ""
    if not secret:
        return JSONResponse({"detail": "not configured"}, status_code=501)
    if not data_service.verify_themisiq_signature(raw_body, signature, secret):
        return JSONResponse({"detail": "signature mismatch"}, status_code=401)
    payload = json.loads(raw_body)
    result = data_service.handle_themisiq_webhook(payload)
    data_service._log(
        ep["endpoint_key"] if ep else "themisiq",
        "inbound",
        payload.get("idempotency_key"),
        payload,
        result,
        200,
        True,
        None,
        0,
    )
    return result


# ---------------------------------------------------------------------------
# Submission channels
# ---------------------------------------------------------------------------
@router.get("/integrations/api/channels")
@require_auth
@require_capability("module.integrations.access")
async def list_channels(request: Request):
    return {"channels": data_service.list_submission_channels(request.state.user["org_id"])}


@router.post("/integrations/api/channels/seed")
@require_auth
@require_capability("module.integrations.manage")
async def seed_channels(request: Request):
    data_service.seed_default_channels(request.state.user["org_id"])
    return {"status": "seeded"}


# ---------------------------------------------------------------------------
# Report submission
# ---------------------------------------------------------------------------
@router.post("/integrations/api/reports/{report_id}/submit/{channel_key}")
@require_auth
@require_capability("module.statutory.manage")
async def submit_report(request: Request, report_id: int, channel_key: str):
    try:
        result = data_service.submit_report_to_channel(report_id, channel_key, request.state.user["id"])
        return result
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


@router.get("/integrations/api/submissions")
@require_auth
@require_capability("module.integrations.access")
async def list_submissions(request: Request):
    with get_db() as db:
        rows = db.execute(
            """SELECT d.* FROM submission_deliveries d
               JOIN statutory_reports r ON r.id = d.report_id
               WHERE r.org_id=%s
               ORDER BY d.created_at DESC""",
            (request.state.user["org_id"],),
        ).fetchall()
        return {"submissions": [dict(r) for r in rows]}


@router.post("/integrations/api/submissions/{delivery_id}/status")
@require_auth
@require_capability("module.integrations.manage")
async def update_submission_status(request: Request, delivery_id: int):
    body = await request.json()
    data_service.record_submission_status(
        delivery_id,
        body.get("status", "pending"),
        body.get("response_payload"),
        body.get("error_message"),
    )
    return {"status": "updated"}


# ---------------------------------------------------------------------------
# Queue processing (called by scheduler or admin)
# ---------------------------------------------------------------------------
@router.post("/integrations/api/queue/process")
@require_auth
@require_capability("module.integrations.manage")
async def process_queue(request: Request, limit: int = 10):
    results = data_service.process_pending_queue(limit)
    return {"processed": results}
