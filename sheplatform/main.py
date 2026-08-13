"""SHE Platform - FastAPI app entrypoint (guide 3.2, 25)."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from sheplatform.config import settings
from sheplatform.core.middleware import security_headers_middleware
from sheplatform.database import init_db

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="SHE Management Platform", version="0.1.0")

app.middleware("http")(security_headers_middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[] if not settings.DEBUG else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="sheplatform/static"), name="static")


@app.on_event("startup")
async def startup():
    # Register event handlers BEFORE anything can emit (guide 21)
    from sheplatform.core import event_handlers  # noqa: F401
    init_db()
    logging.getLogger("sheplatform").info("SHE platform DB initialised")


@app.get("/health")
async def health():
    return {"status": "ok", "app": "ecoaegis", "version": "0.1.0"}


# ---- Router registration (lazy imports to avoid circular deps, guide 3.2) ----
from sheplatform.modules.launcher.routes_auth import router as auth_router
from sheplatform.modules.launcher.routes_admin import router as admin_router
from sheplatform.modules.launcher.routes_dashboard import router as dashboard_router
from sheplatform.modules.incidents.routes import router as incidents_router
from sheplatform.modules.risk_register.routes import router as risks_router
from sheplatform.modules.vendor_compliance.routes import router as vendors_router
from sheplatform.modules.permit_to_work.routes import router as permits_router
from sheplatform.modules.community_complaints.routes import router as grievances_router
from sheplatform.modules.eia.routes import router as eia_router
from sheplatform.modules.emergency.routes import router as emergency_router
from sheplatform.modules.training.routes import router as training_router
from sheplatform.modules.reporting.routes import router as reports_router
from sheplatform.modules.external_comms.routes import router as comms_router
from sheplatform.modules.workplan.routes import router as workplan_router
from sheplatform.modules.esg_kpi.routes import router as esg_router
from sheplatform.modules.stakeholder.routes import router as stakeholder_router
from sheplatform.modules.evidence.routes import router as evidence_router
from sheplatform.modules.integration.routes import router as integration_router
from sheplatform.modules.ai.routes import router as ai_router
from sheplatform.modules.capa.routes import router as capa_router
from sheplatform.modules.inspections.routes import router as inspections_router
from sheplatform.modules.observations.routes import router as observations_router
from sheplatform.modules.documents.routes import router as documents_router
from sheplatform.modules.compliance.routes import router as compliance_router
from sheplatform.modules.contractors.routes import router as contractors_router
from sheplatform.modules.chemicals.routes import router as chemicals_router
from sheplatform.modules.benchmark.routes import router as benchmark_router

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(dashboard_router)
app.include_router(incidents_router)
app.include_router(risks_router)
app.include_router(vendors_router)
app.include_router(permits_router)
app.include_router(grievances_router)
app.include_router(eia_router)
app.include_router(emergency_router)
app.include_router(training_router)
app.include_router(reports_router)
app.include_router(comms_router)
app.include_router(workplan_router)
app.include_router(esg_router)
app.include_router(stakeholder_router)
app.include_router(evidence_router)
app.include_router(integration_router)
app.include_router(ai_router)
app.include_router(capa_router)
app.include_router(inspections_router)
app.include_router(observations_router)
app.include_router(documents_router)
app.include_router(compliance_router)
app.include_router(contractors_router)
app.include_router(chemicals_router)
app.include_router(benchmark_router)


# ---- Background schedulers (guide 22; single-worker mode) ----
@app.on_event("startup")
async def start_scheduler():
    try:
        from sheplatform.database import get_db_background
        from sheplatform.modules.incidents.scheduler import start_scheduler as start_inc_scheduler
        from sheplatform.modules.vendor_compliance.scheduler import start_scheduler as start_vendor_scheduler
        from sheplatform.modules.reporting.scheduler import start_scheduler as start_report_scheduler
        app.state.inc_scheduler = start_inc_scheduler(get_db_background)
        app.state.vendor_scheduler = start_vendor_scheduler(get_db_background)
        app.state.report_scheduler = start_report_scheduler(get_db_background)

        # ThemisIQ sync queue drainer (spec 11.4: every 5 minutes)
        from sheplatform.modules.integration.themis_sync import drain_queue
        from apscheduler.schedulers.background import BackgroundScheduler
        import logging
        _sync_scheduler = BackgroundScheduler()

        def _drain():
            db = get_db_background()
            try:
                drain_queue(db)
            except Exception:
                logging.getLogger("sheplatform").exception("themis sync drain failed")
            finally:
                db.close()

        _sync_scheduler.add_job(_drain, "interval", minutes=5, id="themis_sync_drain")
        _sync_scheduler.start()
        app.state.integration_scheduler = _sync_scheduler
    except Exception:
        import logging
        logging.getLogger("sheplatform").exception("scheduler start failed")
