"""SHECMV - Vendor Compliance routes (guide 10)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.audit import log_audit
from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.vendor_compliance import data_service, risk_assessment_service
from sheplatform.templating import templates

router = APIRouter(prefix="/vendors")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.vendors.access")
async def vendors_shell(request: Request):
    return templates.TemplateResponse(request, "vendor_compliance/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/list")
@require_auth
@require_capability("module.vendors.access")
async def api_list(request: Request, status: str = ""):
    db = get_db()
    try:
        items = data_service.list_vendors(db, status=status or None,
                                          org_id=request.state.user.get("org_id"))
        return JSONResponse({"vendors": items})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
@require_capability("module.vendors.access")
async def api_create(request: Request,
                     company_name: str = Form(...),
                     contact_person: str = Form(""),
                     email: str = Form(""),
                     phone: str = Form(""),
                     risk_profile: str = Form("medium")):
    db = get_db()
    try:
        if risk_profile not in ("high", "medium", "low"):
            return JSONResponse({"ok": False, "message": "invalid risk profile"}, status_code=400)
        vendor = data_service.create_vendor(
            db, company_name=company_name, contact_person=contact_person,
            email=email, phone=phone, risk_profile=risk_profile,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        log_audit(db, request.state.user["id"], request.state.user.get("org_id"),
                  "vendor.create", "vendors", vendor["id"],
                  new_value={"vendor_ref": vendor["vendor_ref"]})
        return JSONResponse({"ok": True, "vendor": vendor}, status_code=201)
    finally:
        db.close()


@router.post("/api/{vendor_id}/certifications")
@require_auth
@require_capability("module.vendors.access")
async def api_add_cert(request: Request, vendor_id: int,
                       cert_name: str = Form(...),
                       expiry_date: str = Form(...),
                       cert_number: str = Form("")):
    db = get_db()
    try:
        cert = data_service.add_certification(
            db, vendor_id=vendor_id, cert_name=cert_name,
            expiry_date=expiry_date, cert_number=cert_number)
        status = data_service._refresh_vendor_cert_status(db, vendor_id)
        return JSONResponse({"ok": True, "certification": cert, "vendor_status": status})
    finally:
        db.close()


@router.get("/api/{vendor_id}/certifications")
@require_auth
@require_capability("module.vendors.access")
async def api_list_certs(request: Request, vendor_id: int):
    db = get_db()
    try:
        certs = data_service.list_certifications(db, vendor_id)
        return JSONResponse({"certifications": certs})
    finally:
        db.close()


# ---- Risk assessments ----
@router.get("/api/{vendor_id}/assessments")
@require_auth
@require_capability("module.vendors.access")
async def api_list_assessments(request: Request, vendor_id: int):
    db = get_db()
    try:
        items = risk_assessment_service.list_assessments(db, vendor_id=vendor_id)
        return JSONResponse({"assessments": items})
    finally:
        db.close()


@router.post("/api/{vendor_id}/assessments")
@require_auth
@require_capability("module.vendors.access")
async def api_create_assessment(request: Request, vendor_id: int,
                                scope_of_work: str = Form(...),
                                workforce_size: int | None = Form(None),
                                equipment_list: str = Form(""),
                                site_conditions: str = Form(""),
                                risk_rating: str = Form("medium")):
    db = get_db()
    try:
        assessment = risk_assessment_service.create_assessment(
            db, vendor_id=vendor_id, scope_of_work=scope_of_work,
            workforce_size=workforce_size, equipment_list=equipment_list,
            site_conditions=site_conditions, risk_rating=risk_rating,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "assessment": assessment}, status_code=201)
    finally:
        db.close()


@router.post("/api/assessments/{assessment_id}/approve")
@require_auth
@require_capability("module.permits.approve")
async def api_approve_assessment(request: Request, assessment_id: int):
    db = get_db()
    try:
        result = risk_assessment_service.approve_assessment(
            db, assessment_id, request.state.user["id"])
        return JSONResponse(result)
    finally:
        db.close()
