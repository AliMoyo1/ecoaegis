"""SHEIA - EIA routes (guide 13)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.audit import log_audit
from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.eia import data_service
from sheplatform.modules.map.site_relationship_service import list_active_sites
from sheplatform.templating import templates

router = APIRouter(prefix="/eia")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.eia.access")
async def eia_shell(request: Request):
    db = get_db()
    try:
        sites = list_active_sites(db, request.state.user.get("org_id"))
        return templates.TemplateResponse(
            request, "eia/templates/index.html",
            {"user": request.state.user, "sites": sites},
        )
    finally:
        db.close()


@router.get("/api/projects")
@require_auth
@require_capability("module.eia.access")
async def api_list(request: Request, status: str = ""):
    db = get_db()
    try:
        items = data_service.list_projects(db, status=status or None,
                                           org_id=request.state.user.get("org_id"))
        return JSONResponse({"projects": items})
    finally:
        db.close()


@router.post("/api/projects")
@require_auth
@require_capability("module.eia.access")
async def api_create(request: Request,
                     project_name: str = Form(...),
                     department: str = Form(""),
                     project_type: str = Form(""),
                     location: str = Form(""),
                     site_id: int | None = Form(None)):
    db = get_db()
    try:
        project = data_service.create_project(
            db, project_name=project_name, department=department,
            project_type=project_type, location=location, site_id=site_id,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        log_audit(db, request.state.user["id"], request.state.user.get("org_id"),
                  "eia.project.create", "eia_projects", project["id"],
                  new_value={"project_ref": project["project_ref"],
                             "site_id": project.get("site_id")})
        return JSONResponse({"ok": True, "project": project}, status_code=201)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    finally:
        db.close()


@router.post("/api/projects/{project_id}/screening")
@require_auth
@require_capability("module.eia.access")
async def api_screening(request: Request, project_id: int,
                        eia_required: str = Form("true")):
    db = get_db()
    try:
        return JSONResponse(data_service.complete_screening(
            db, project_id, eia_required=eia_required.lower() == "true"))
    finally:
        db.close()


@router.get("/api/consultants")
@require_auth
@require_capability("module.eia.access")
async def api_consultants(request: Request):
    db = get_db()
    try:
        return JSONResponse({"consultants": data_service.list_consultants(db, org_id=request.state.user.get("org_id"))})
    finally:
        db.close()


@router.post("/api/consultants")
@require_auth
@require_capability("module.eia.access")
async def api_consultant_create(request: Request,
                                name: str = Form(...),
                                company: str = Form(""),
                                ema_accreditation_number: str = Form("")):
    db = get_db()
    try:
        consultant = data_service.register_consultant(
            db, name=name, company=company,
            ema_accreditation_number=ema_accreditation_number,
            org_id=request.state.user.get("org_id"))
        return JSONResponse({"ok": True, "consultant": consultant}, status_code=201)
    finally:
        db.close()


@router.post("/api/consultants/{consultant_id}/verify")
@require_auth
@require_capability("module.eia.access")
async def api_consultant_verify(request: Request, consultant_id: int,
                                verified: bool = Form(True)):
    db = get_db()
    try:
        result = data_service.verify_consultant_accreditation(
            db, consultant_id, request.state.user["id"], verified=verified)
        if not result["ok"]:
            return JSONResponse(result, status_code=400)
        return JSONResponse(result)
    finally:
        db.close()


@router.post("/api/projects/{project_id}/consultant")
@require_auth
@require_capability("module.eia.access")
async def api_assign_consultant(request: Request, project_id: int,
                                consultant_id: int = Form(...)):
    db = get_db()
    try:
        result = data_service.assign_consultant(db, project_id, consultant_id)
        code = 400 if result.get("code") == "FNR-048" else 200
        return JSONResponse(result, status_code=code)
    finally:
        db.close()


@router.post("/api/projects/{project_id}/submit-ema")
@require_auth
@require_capability("module.eia.access")
async def api_submit_ema(request: Request, project_id: int,
                         submission_ref: str = Form("")):
    db = get_db()
    try:
        return JSONResponse(data_service.submit_to_ema(db, project_id, submission_ref))
    finally:
        db.close()


@router.post("/api/projects/{project_id}/ema-decision")
@require_auth
@require_capability("module.eia.access")
async def api_ema_decision(request: Request, project_id: int,
                           decision: str = Form(...)):
    db = get_db()
    try:
        return JSONResponse(data_service.record_ema_decision(db, project_id, decision))
    finally:
        db.close()
