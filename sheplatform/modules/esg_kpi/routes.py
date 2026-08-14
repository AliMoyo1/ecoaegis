"""ESG KPI routes (guide 19)."""
from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.esg_kpi import csv_service, data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/esg")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.esg.access")
async def esg_shell(request: Request):
    return templates.TemplateResponse(request, "esg_kpi/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/kpis")
@require_auth
@require_capability("module.esg.access")
async def api_kpis(request: Request, category: str = ""):
    db = get_db()
    try:
        return JSONResponse({"kpis": data_service.list_kpis(db, category=category or None)})
    finally:
        db.close()


@router.post("/api/seed")
@require_auth
@require_capability("module.esg.access")
async def api_seed(request: Request):
    db = get_db()
    try:
        n = data_service.seed_kpis(db, org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "seeded": n})
    finally:
        db.close()


@router.post("/api/entries")
@require_auth
@require_capability("module.esg.access")
async def api_entry(request: Request,
                    kpi_id: int = Form(...),
                    period: str = Form(...),
                    actual_value: float = Form(...),
                    target_value: float | None = Form(None),
                    notes: str = Form("")):
    db = get_db()
    try:
        result = data_service.record_kpi_entry(
            db, kpi_id=kpi_id, period=period, actual_value=actual_value,
            target_value=target_value, notes=notes,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        if not result["ok"]:
            return JSONResponse(result, status_code=404)
        return JSONResponse(result, status_code=201)
    finally:
        db.close()


@router.get("/api/entries")
@require_auth
@require_capability("module.esg.access")
async def api_entries(request: Request, kpi_id: int | None = None, period: str = ""):
    db = get_db()
    try:
        return JSONResponse({"entries": data_service.list_entries(db, kpi_id=kpi_id,
                                                                  period=period or None)})
    finally:
        db.close()


@router.get("/api/summary")
@require_auth
@require_capability("module.esg.access")
async def api_summary(request: Request):
    db = get_db()
    try:
        return JSONResponse(data_service.dashboard_summary(db))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# B3 CSV / mapping / API-key ingestion routes
# ---------------------------------------------------------------------------

@router.post("/api/csv/upload")
@require_auth
@require_capability("module.esg.manage")
async def api_csv_upload(request: Request,
                         file: UploadFile = File(...),
                         mapping_name: str = Form(""),
                         default_period: str = Form("")):
    db = get_db()
    try:
        contents = await file.read()
        result = csv_service.parse_csv_upload(
            db, file_bytes=contents, file_name=file.filename or "upload.csv",
            mapping_name=mapping_name or None,
            org_id=request.state.user.get("org_id"),
            created_by=request.state.user["id"],
            default_period=default_period or None)
        return JSONResponse(result, status_code=201)
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    finally:
        db.close()


@router.post("/api/csv/{upload_id}/reconcile")
@require_auth
@require_capability("module.esg.manage")
async def api_csv_reconcile(request: Request, upload_id: int):
    db = get_db()
    try:
        return JSONResponse(csv_service.detect_duplicates(db, upload_id))
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    finally:
        db.close()


@router.post("/api/csv/{upload_id}/commit")
@require_auth
@require_capability("module.esg.manage")
async def api_csv_commit(request: Request, upload_id: int,
                         skip_anomalies: bool = Form(True)):
    db = get_db()
    try:
        result = csv_service.commit_upload(
            db, upload_id, created_by=request.state.user["id"],
            skip_anomalies=skip_anomalies)
        if not result["ok"]:
            return JSONResponse(result, status_code=400)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    finally:
        db.close()


@router.get("/api/csv/{upload_id}/rows")
@require_auth
@require_capability("module.esg.access")
async def api_csv_rows(request: Request, upload_id: int, status: str = ""):
    db = get_db()
    try:
        return JSONResponse({"rows": csv_service.get_upload_rows(
            db, upload_id, status=status or None)})
    finally:
        db.close()


@router.get("/api/csv/uploads")
@require_auth
@require_capability("module.esg.access")
async def api_csv_uploads(request: Request):
    db = get_db()
    try:
        org_id = request.state.user.get("org_id")
        rows = db.execute(
            "SELECT * FROM esg_csv_uploads WHERE org_id = %s ORDER BY id DESC LIMIT 50",
            (org_id,)).fetchall()
        return JSONResponse({"uploads": [dict(r) for r in rows]})
    finally:
        db.close()


@router.post("/api/mappings")
@require_auth
@require_capability("module.esg.manage")
async def api_create_mapping(request: Request,
                             mapping_name: str = Form(...),
                             mappings_json: str = Form(...)):
    db = get_db()
    try:
        import json
        mappings = json.loads(mappings_json)
        result = csv_service.create_mapping(
            db, mapping_name=mapping_name, mappings=mappings,
            org_id=request.state.user.get("org_id"),
            created_by=request.state.user["id"])
        return JSONResponse(result, status_code=201)
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    finally:
        db.close()


@router.get("/api/mappings")
@require_auth
@require_capability("module.esg.access")
async def api_list_mappings(request: Request, mapping_name: str = ""):
    db = get_db()
    try:
        return JSONResponse({"mappings": csv_service.list_mappings(
            db, mapping_name=mapping_name or None,
            org_id=request.state.user.get("org_id"))})
    finally:
        db.close()


@router.post("/api/api-keys")
@require_auth
@require_capability("module.esg.admin")
async def api_create_api_key(request: Request, name: str = Form(...)):
    db = get_db()
    try:
        result = csv_service.create_api_key(
            db, name=name, org_id=request.state.user.get("org_id"),
            created_by=request.state.user["id"])
        return JSONResponse(result, status_code=201)
    finally:
        db.close()


@router.post("/api/ingest")
async def api_ingest(request: Request):
    """External ops system push endpoint. Authenticated via X-ESG-API-Key header."""
    key = request.headers.get("X-ESG-API-Key", "")
    if not key:
        return JSONResponse({"ok": False, "message": "missing api key"}, status_code=401)
    db = get_db()
    try:
        key_record = csv_service.verify_api_key(db, key)
        if not key_record:
            return JSONResponse({"ok": False, "message": "invalid api key"}, status_code=401)
        if "esg.ingest" not in json.loads(key_record["scopes"]):
            return JSONResponse({"ok": False, "message": "insufficient scope"}, status_code=403)
        payload = await request.json()
        result = csv_service.record_api_kpi_payload(
            db, key_record=key_record, payload=payload)
        if not result["ok"]:
            return JSONResponse(result, status_code=400)
        return JSONResponse(result, status_code=201)
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    finally:
        db.close()
