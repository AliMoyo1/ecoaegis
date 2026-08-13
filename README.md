# EcoAegis - SHE Management Platform

Safety, Health and Environment management platform for Econet (built on ThemisIQ-proven architecture). Named **EcoAegis** - "aegis" (protective shield) + "eco" (Econet / environment).

## Features

**Operational core (14 guide modules):**
- **SHEIMI** Incident management - statutory 48h critical deadlines (BRN-002), investigation flow, `incident.closed` -> risk auto-creation
- **Risk Register** - residual scoring (inherent / control effectiveness), priority bands, 5x5 heat map
- **SHECMV** Vendor compliance - certification expiry alerts, auto-suspend of PTW eligibility (BRN-013)
- **PTW** Permit to Work - requires APPROVED risk assessment (BRN-001), 4-step approval chain
- **SHECCM** Community complaints - notify-before-close (BRN-010), residual risk -> register + drill mandate
- **SHEIA** Environmental Impact Assessment - clearance gate (BRN-004), EMA-accredited consultant check
- **SHEEPRP/SHEER** Emergency - dual sign-off (BRN-007), drills, post-crisis improvement queue
- **SHET&A** Training - needs -> sessions -> competency matrix, outsourced -> procurement (BRN-014)
- **SHER** Reporting - per-type approval chains, overdue escalation (BRN-012)
- **SHE EC&SC** External comms - HOD approval gate (BRN-008)
- **SHEAWPM** Annual workplan - >= 40% preventive (BRN-006), drill closure block
- **ESG KPIs** - RAG status, non-zero critical KPI auto-creates incident
- **Stakeholder engagement** - quarterly feedback
- **Evidence vault** - SHA-256 verified file integrity

**Competitor-parity modules (2026 benchmark):**
- **CAPA** - closed-loop corrective actions with 2-person verification
- **Inspections** - checklist-driven, failed items auto-create CAPA
- **Observations** - see-something-say-something quick capture
- **Document control** - SOP/policy versioning + staff acknowledgement
- **Compliance obligations** - NSSA/EMA/ZRP statutory calendar
- **Contractor management** - site-readiness gate (induction + insurance + certs)
- **Chemical/SDS register** - hazard inventory
- **Site benchmarking** - Red/Amber/Green cross-site ranking

**Platform:**
- AI copilot (grounded, no-hallucination) - incident copilot, root cause 5-Why, predictive risk, training gaps, statutory report drafts, chat
- Multi-provider AI (Kimi/DeepSeek/Gemini/Anthropic)
- ThemisIQ bidirectional integration (risk sync + HMAC webhooks)
- Role-based access (12 roles, 30+ capabilities), audit trail, approval chains, schedulers
- Dark mode, glass UI, mobile drawer

## Architecture

- **Stack:** FastAPI + Jinja2 server-rendered templates + vanilla JS, raw parameterized SQL (no ORM)
- **Package:** `sheplatform/` - `core/` (auth, rbac, audit, events, workflow, webhooks, notifications, middleware, ai_client) + `modules/` (one dir per module: routes.py + data_service.py + event_handlers.py + scheduler.py)
- **DB:** dual-engine - SQLite for dev (with %s->? wrapper), PostgreSQL for prod via DATABASE_URL
- **Tests:** pytest, 138 passing (25 files), fresh SQLite per test

## Quick start

```bash
# 1. install
python -m venv venv
venv/Scripts/activate        # windows
pip install -r requirements.txt

# 2. configure
cp .env.example .env         # add your AI keys (optional)

# 3. seed + run
python -m sheplatform.seeds.seed
uvicorn sheplatform.main:app --host 127.0.0.1 --port 8082
```

Open http://127.0.0.1:8082 - seeded users: `superadmin@she.local` / `manager@she.local` / `officer@she.local` / `employee@she.local` (password `ChangeMe!123`).

## Tests

```bash
python -m pytest tests/ -q
```

## ThemisIQ integration

Direction 1: corporate-material risks (residual >= 12, regulatory, or manual flag) push to ThemisIQ. Direction 2: ThemisIQ escalated/appetite events webhook in. See `docs/SHE_THEMISIQ_INTEGRATION.md` (interface control document).

## Docs

- `docs/COMPETITOR_BENCHMARK.md` - EHS market comparison (Cority, Mitti, VelocityEHS)
- `docs/ARCHITECTURE.md`, `docs/SOLUTION_DESIGN.md` - original design
- Full implementation guide: IMPLEMENTATION_GUIDE v1.0.0 (BRN/FNR/NFR traceability)

## License

Proprietary - Econet Group internal.
