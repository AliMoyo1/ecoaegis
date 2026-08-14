"""Chemicals / SDS routes."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.attachments import get_attachment
from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.chemicals import data_service
from sheplatform.modules.chemicals.sds_extraction import (
    apply_sds_fields,
    extract_sds_from_pdf,
)
from sheplatform.templating import templates

router = APIRouter(prefix="/chemicals", tags=["chemicals"])


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.chemicals.access")
async def chemicals_shell(request: Request):
    return templates.TemplateResponse(request, "chemicals/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/list")
@require_auth
@require_capability("module.chemicals.access")
async def api_list(request: Request, hazard_class: str = "", site_id: int = 0):
    db = get_db()
    try:
        items = data_service.list_chemicals(
            db, hazard_class=hazard_class or None, site_id=site_id or None,
            org_id=request.state.user.get("org_id"))
        return JSONResponse({"chemicals": items})
    finally:
        db.close()


@router.get("/api/summary")
@require_auth
@require_capability("module.chemicals.access")
async def api_summary(request: Request):
    db = get_db()
    try:
        return JSONResponse(data_service.hazard_summary(db, request.state.user.get("org_id")))
    finally:
        db.close()


@router.get("/api/sites")
@require_auth
@require_capability("module.chemicals.access")
async def api_sites(request: Request):
    db = get_db()
    try:
        rows = db.execute("SELECT id, site_name, city FROM sites WHERE status = 'active' "
                          "ORDER BY site_name").fetchall()
        return JSONResponse({"sites": [dict(r) for r in rows]})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
@require_capability("chemicals.manage")
async def api_create(request: Request, name: str = Form(...), cas_number: str = Form(""),
                     supplier: str = Form(""), hazard_class: str = Form(""),
                     pictogram: str = Form(""), sds_path: str = Form(""),
                     sds_review_date: str = Form(""), sds_status: str = Form("current"),
                     quantity_units: str = Form(""), storage_location: str = Form(""),
                     site_id: int = Form(0)):
    db = get_db()
    try:
        chem = data_service.create_chemical(
            db, name=name, cas_number=cas_number, supplier=supplier,
            hazard_class=hazard_class, pictogram=pictogram, sds_path=sds_path,
            sds_review_date=sds_review_date or None, sds_status=sds_status,
            quantity_units=quantity_units, storage_location=storage_location,
            site_id=site_id or None, created_by=request.state.user["id"],
            org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "chemical": chem})
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()


@router.get("/api/{chemical_id}")
@require_auth
@require_capability("module.chemicals.access")
async def api_detail(request: Request, chemical_id: int):
    db = get_db()
    try:
        rows = data_service.list_chemicals(
            db, org_id=request.state.user.get("org_id"))
        chem = next((c for c in rows if c["id"] == chemical_id), None)
        if not chem:
            return JSONResponse({"detail": "not found"}, status_code=404)
        return JSONResponse({"ok": True, "chemical": chem})
    finally:
        db.close()


@router.post("/api/{chemical_id}/sds-upload")
@require_auth
@require_capability("chemicals.manage")
async def api_sds_upload(request: Request, chemical_id: int,
                         file: UploadFile = File(...),
                         sds_review_date: str = Form("")):
    """Upload an SDS PDF against a chemical, extract fields, and store the
    attachment. Returns extracted fields for human review without overwriting
    the chemical record.
    """
    db = get_db()
    try:
        user = request.state.user
        org_id = user.get("org_id")
        content = await file.read()

        from sheplatform.core.attachments import save_attachment
        att = save_attachment(
            db, entity_type="chemical", entity_id=chemical_id,
            file_bytes=content, original_name=file.filename or "sds.pdf",
            mime_type=file.content_type or "application/pdf", kind="file",
            org_id=org_id, uploaded_by=user["id"])

        extraction = await extract_sds_from_pdf(content)
        if not extraction["ok"]:
            return JSONResponse({
                "ok": False,
                "attachment_id": att["id"],
                "message": extraction.get("error"),
                "raw": extraction.get("raw_json"),
            }, status_code=422)

        data_service.update_chemical(
            db, chemical_id, org_id=org_id, user_id=user["id"],
            sds_attachment_id=att["id"],
            sds_review_date=sds_review_date or None,
            sds_status="draft",
            sds_extracted=extraction["fields"])

        return JSONResponse({
            "ok": True,
            "attachment_id": att["id"],
            "extraction": extraction,
            "preview": apply_sds_fields(
                data_service.list_chemicals(db, org_id=org_id)[0]
                if data_service.list_chemicals(db, org_id=org_id) else {},
                extraction["fields"]),
        })
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()


@router.post("/api/{chemical_id}/sds-apply")
@require_auth
@require_capability("chemicals.manage")
async def api_sds_apply(request: Request, chemical_id: int,
                        hazard_class: str = Form(""), supplier: str = Form(""),
                        cas_number: str = Form(""), sds_status: str = Form("current"),
                        extracted_json: str = Form("")):
    """Apply reviewed SDS extracted fields to the chemical record."""
    db = get_db()
    try:
        user = request.state.user
        org_id = user.get("org_id")
        import json
        extracted = json.loads(extracted_json) if extracted_json else {}
        updates = {
            "sds_status": sds_status or "current",
            "sds_extracted": extracted,
        }
        if hazard_class:
            updates["hazard_class"] = hazard_class
        if supplier:
            updates["supplier"] = supplier
        if cas_number:
            updates["cas_number"] = cas_number

        chem = data_service.update_chemical(
            db, chemical_id, org_id=org_id, user_id=user["id"], **updates)
        if not chem:
            return JSONResponse({"detail": "not found"}, status_code=404)
        return JSONResponse({"ok": True, "chemical": chem})
    except (ValueError, json.JSONDecodeError) as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    finally:
        db.close()
