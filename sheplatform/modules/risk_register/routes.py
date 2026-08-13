"""Risk Register routes (guide 9)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sheplatform.core.audit import log_audit
from sheplatform.core.middleware import require_auth, require_capability
from sheplatform.database import get_db
from sheplatform.modules.risk_register import data_service
from sheplatform.templating import templates

router = APIRouter(prefix="/risks")


@router.get("", response_class=HTMLResponse)
@require_auth
@require_capability("module.risk_register.access")
async def risks_shell(request: Request):
    return templates.TemplateResponse(request, "risk_register/templates/index.html",
                                      {"user": request.state.user})


@router.get("/api/list")
@require_auth
@require_capability("module.risk_register.access")
async def api_list(request: Request, category: str = "", status: str = ""):
    db = get_db()
    try:
        items = data_service.list_risks(db, category=category or None, status=status or None,
                                        org_id=request.state.user.get("org_id"))
        return JSONResponse({"risks": items})
    finally:
        db.close()


@router.post("/api/create")
@require_auth
@require_capability("module.risk_register.write")
async def api_create(request: Request,
                     hazard_description: str = Form(...),
                     risk_category: str = Form("operational"),
                     likelihood: int = Form(3),
                     impact: int = Form(3),
                     control_effectiveness: int = Form(3),
                     managerial_response: str = Form("mitigate"),
                     process_function: str = Form("")):
    db = get_db()
    try:
        if risk_category not in ("operational", "financial", "regulatory", "strategic"):
            return JSONResponse({"ok": False, "message": "invalid category"}, status_code=400)
        if not (1 <= likelihood <= 5 and 1 <= impact <= 5 and 1 <= control_effectiveness <= 5):
            return JSONResponse({"ok": False, "message": "scores must be 1-5"}, status_code=400)
        risk = data_service.create_risk(
            db, hazard_description=hazard_description, risk_category=risk_category,
            likelihood=likelihood, impact=impact,
            control_effectiveness=control_effectiveness,
            managerial_response=managerial_response, process_function=process_function,
            created_by=request.state.user["id"], org_id=request.state.user.get("org_id"))
        log_audit(db, request.state.user["id"], request.state.user.get("org_id"),
                  "risk.create", "risks", risk["id"],
                  new_value={"ref": risk["risk_ref"], "residual": float(risk["residual_score"])})
        return JSONResponse({"ok": True, "risk": risk}, status_code=201)
    finally:
        db.close()


@router.get("/api/{risk_id}")
@require_auth
@require_capability("module.risk_register.access")
async def api_detail(request: Request, risk_id: int):
    db = get_db()
    try:
        risk = data_service.get_risk(db, risk_id)
        if risk is None:
            return JSONResponse({"ok": False, "message": "not found"}, status_code=404)
        return JSONResponse({"risk": risk})
    finally:
        db.close()


@router.post("/api/{risk_id}/update")
@require_auth
@require_capability("module.risk_register.write")
async def api_update(request: Request, risk_id: int,
                     likelihood: int | None = Form(None),
                     impact: int | None = Form(None),
                     control_effectiveness: int | None = Form(None),
                     managerial_response: str | None = Form(None),
                     status: str | None = Form(None),
                     status_update: str | None = Form(None)):
    db = get_db()
    try:
        risk = data_service.update_risk(
            db, risk_id, likelihood=likelihood, impact=impact,
            control_effectiveness=control_effectiveness,
            managerial_response=managerial_response, status=status,
            status_update=status_update, by_user=request.state.user["id"])
        if risk is None:
            return JSONResponse({"ok": False, "message": "not found"}, status_code=404)
        log_audit(db, request.state.user["id"], request.state.user.get("org_id"),
                  "risk.update", "risks", risk_id, new_value={"residual": float(risk["residual_score"])})
        return JSONResponse({"ok": True, "risk": risk})
    finally:
        db.close()
