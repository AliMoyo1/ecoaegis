# SHE Management Platform — Implementation Plan

**Source:** SHE_BRS_1.1.0_Final.docx (Author: Brendon Buwerimwe, June 2026)
**BRS Overview:** 10 process modules, 14 business rules, 70+ functional requirements, cross-module Risk Register, ESG KPIs, Stakeholder Engagement, Annual Sustainability Reporting

> **Architecture note (Aug 2026):** See [ARCHITECTURE.md](ARCHITECTURE.md) for the authoritative architecture - Hermes intelligence layer + deterministic SHE Core, WhatsApp/web user separation, workflow state machines, schema, and the ThemisIQ integration (ERM/ORM risk sharing). [SOLUTION_DESIGN.md](SOLUTION_DESIGN.md) holds schema/API detail. This plan keeps the delivery phases.

---

## Phase 0: Foundation (Core Infrastructure)

**Goal:** The platform skeleton — authentication, RBAC, master data registries, basic UI shell.

### Deliverables
- [ ] FastAPI project scaffold with dual-engine DB (SQLite dev / PG prod)
- [ ] User model + auth (session-based, bcrypt, MFA-ready, SSO integration point)
- [ ] RBAC system — 9 internal roles + external actor portals
- [ ] Master data registries:
  - Central Risk Register (section 3.1 → FNR-SHE-052..059)
  - Key Issues SHE Tracker (section 3.2)
  - Vendor Compliance Roster (section 3.3)
  - Community Grievance Register (section 3.4 — fields per table 34)
  - Training Records & Competency Matrix (section 3.5, LMS integration stub)
- [ ] Audit log engine (SEC-SHE-005: tamper-evident, 7-year retention)
- [ ] Base UI shell: module navigation, role-enforced menu, notification panel
- [ ] Scheduler groundwork (APScheduler for callback/alert/rollover tasks)

**Business Rules enforced:** BRN-SHE-005 (approval chain), BRN-SHE-012 (compliance escalation)
**Security requirements:** SEC-SHE-001 (auth+MFA), SEC-SHE-002 (RBAC), SEC-SHE-003 (TLS), SEC-SHE-004 (encryption at rest), SEC-SHE-007 (secret management)
**NFRs covered:** NFR-SHE-004 (audit trail), NFR-SHE-005 (scalability), NFR-SHE-007 (REST APIs)

**Estimated effort:** High (40-50% of total project) — this is the bedrock everything else builds on.

---

## Phase 1: Module Blitz (Build All 10 Modules)

**Goal:** Ship all 10 process modules in parallel or rapid succession. Each module follows the same pattern:
- Database schema (table per module)
- Service layer (business logic)
- Route layer (REST endpoints)
- SPA UI (module-specific index.html + JS)

### Module Delivery Order (dependencies matter)

| Order | Module | BRS Ref | Business Rules | Depends On |
|---|---|---|---|---|
| 1 | **SHEIMI** — Accident & Incident Investigation | Section 5.5, FNR-SHE-022..026 | BRN-SHE-002, 005 | Phase 0 |
| 2 | **SHECMV** — Vendor SHE Compliance (PTW) | Section 5.1, FNR-SHE-001..006 | BRN-SHE-001, 005, 013 | Phase 0 |
| 3 | **SHECCM** — Community Complaints | Section 5.2, FNR-SHE-007..011 | BRN-SHE-003, 005, 010 | Phase 0 |
| 4 | **SHEIA** — Environmental Impact Assessment | Section 5.10, FNR-SHE-046..051 | BRN-SHE-004, 005, 014 | Phase 0 |
| 5 | **SHEEPRP** — Emergency Preparedness | Section 5.4, FNR-SHE-017..021 | BRN-SHE-005, 011 | Phase 0 |
| 6 | **SHEER** — Live Emergency Response | Section 5.7, FNR-SHE-031..035 | BRN-SHE-005, 007 | SHEEPRP |
| 7 | **SHE EC&SC** — External Communications | Section 5.3, FNR-SHE-012..016 | BRN-SHE-005, 008 | SHECCM |
| 8 | **SHEAWPM** — Annual Workplan | Section 5.6, FNR-SHE-027..030 | BRN-SHE-005, 006, 011 | SHEIMI, SHEER |
| 9 | **SHER** — SHE Business Reporting | Section 5.8, FNR-SHE-036..040 | BRN-SHE-005, 012 | All above |
| 10 | **SHET&A** — Environmental Training | Section 5.9, FNR-SHE-041..045 | BRN-SHE-005, 009 | SHEIMI (incident-triggered gaps) |

### Cross-Module Integration Points (from Table 18)

Build these as the modules complete:
- SHEIA → SHECMV: environmental hazards trigger vendor risk assessment (Integration #1)
- SHECMV → SHEIMI: PTW incidents auto-linked to vendor record (#2)
- SHEIMI → Risk Register: on incident closure (#3)
- SHECCM → Risk Register: residual grievance flags asset risk (#4)
- SHECCM → SHEEPRP: residual risk → targeted mock drill (#5)
- SHEER → SHEEPRP: post-crisis → EPRP improvement queue (#6)
- SHER → SHET&A: report gaps → training needs (#7)
- SHEIMI → SHET&A: incident root cause → training gap (#8)
- SHER → SHEAWPM: Board recommendations → workplan actions (#9)
- SHECMV/SHEIA → Procurement ERP: API webhook (#10)

### Per-Module Template
Each module needs:
```
modules/sheimi/
├── data_service.py    # DB queries, business logic
├── routes.py          # REST endpoints
└── templates/
    ├── index.html     # Module SPA
    ├── sheimi.js      # Module logic
    └── sheimi.css     # Module styles
```

**Estimated effort:** Medium per module (3-5 days each) = 8-12 weeks total for all 10.

---

## Phase 2: ESG & Stakeholder Modules

**Goal:** Cross-cutting capabilities that consume data from Phase 1 modules.

| Module | BRS Ref | Requirements |
|---|---|---|
| **ESG KPI Dashboard** | Section 12, ESG-FR-001..010, FNR-SHE-060..064 | 40+ KPIs, monthly data entry, target configuration, trend charts, red/amber/green status, automated alerts on threshold breach, environmental incident linkage |
| **Stakeholder Engagement Register** | Section 13, SE-FR-001..010, FNR-SHE-065..070 | Stakeholder directory, engagement scheduling, quarterly feedback (Q1-Q4), regulatory submission tracking, community grievance linkage, escalation on age thresholds |
| **Annual Sustainability Reporting** | Section 14, AR-FR-001..010 | Reporting calendar, automated data collection, multi-tiered approval, report template generation, version control, Board portal integration |

**Estimated effort:** 6-8 weeks

---

## Phase 3: External Integrations

**Goal:** Connect the platform to the outside world.

| Integration | BRS Table 19 | Requirements |
|---|---|---|
| **Procurement ERP Gateway** | Interface #1 | Hold/release vendor onboarding tasks on SHECMV/SHEIA events |
| **Statutory Reporting Engine** | Interface #2 | Generate NSSA, EMA, ZRP templates from SHEIMI/SHEER data; track submission status |
| **Corporate Risk Register API** | Interface #3 | Bidirectional sync: incident/grievance/emergency/EIA closure → Risk Register |
| **LMS Integration** | Interface #4 | Push training records, pull competency data |
| **Corporate Communications Gateway** | Interface #5 | Push approved external comms, receive dispatch confirmations |
| **Board Reporting System** | Interface #6 | Format and deliver SHE reports in PDF/Word/Power BI |
| **EMA Regulatory Portal** | Interface #7 | Submit EIA reports, track acknowledgement |

**Estimated effort:** 4-6 weeks

---

## Phase 4: Non-Functional & Security Hardening

**Goal:** Production readiness.

| Requirement | BRS Ref | Action |
|---|---|---|
| System Availability 99.5% | NFR-SHE-001 | Load balancing, failover, maintenance windows |
| Workflow Response < 60s | NFR-SHE-002 | Performance testing, query optimisation |
| 7-Year Data Retention | NFR-SHE-003 | Archiving strategy, backup verification |
| Audit Trail Integrity | NFR-SHE-004 | Tamper-evident logging, periodic verification |
| Scalability | NFR-SHE-005 | Horizontal scaling assessment |
| Notification Reliability | NFR-SHE-006 | Email + in-platform, retry with backoff |
| Offline Resilience | NFR-SHE-008 | Field intake forms with local storage + sync |
| WCAG 2.1 AA Accessibility | NFR-SHE-009 | UI audit, colour-independent indicators |
| Penetration Testing | SEC-SHE-008 | Pre-production + annual pentest |
| API Rate Limiting | SEC-SHE-006 | Per-consumer limits, IP allowlisting |

**Estimated effort:** 4-6 weeks

---

## Phase 5: UAT, Training & Go-Live

| Activity | BRS Ref | Description |
|---|---|---|
| UAT Test Scripts | Section 10.1 | Priority: PTW gating, incident escalation, Risk Register sync, EIA clearance, statutory reporting, drill scheduling, approval chain |
| Regression Testing | Section 10.3 | After every release |
| Documentation | Section 10.4 | Design docs, process flow diagrams, integration docs, data dictionary |
| User Training | — | Role-based training: SHE Champions, Officers, Managers, Executives |
| Go-Live | — | Phased rollout by module (start with SHEIMI + SHECMV) |

**Estimated effort:** 4-6 weeks

---

## Summary Timeline

```
Phase 0: Foundation        ████████████████████░░░░░░░░░░░░  8-10 weeks
Phase 1: Modules 1-10      ████████████████████████████████  12-14 weeks (parallel)
Phase 2: ESG + Stakeholder ████████████████░░░░░░░░░░░░░░░░  6-8 weeks
Phase 3: Integrations      ████████████░░░░░░░░░░░░░░░░░░░░  4-6 weeks
Phase 4: Hardening         ████████████░░░░░░░░░░░░░░░░░░░░  4-6 weeks
Phase 5: UAT + Go-Live     ████████████░░░░░░░░░░░░░░░░░░░░  4-6 weeks
                            ──────────────────────────────────
                            Total: ~38-50 weeks (9-12 months)
```

**Note:** Phases 1-3 can overlap — for example, start SHER (Phase 1, module 9) as soon as the first 4 modules are stable, and begin ESG dashboard integration (Phase 2) once SHEIMI and SHEIA are producing data. Realistic minimum timeline with overlap: **6-8 months** to full go-live.

---

## Key Architecture Decisions (from this BRS)

| Decision | Rationale |
|---|---|
| **FastAPI + SQLite/PG dual-engine** | Same proven stack as ThemisIQ. You already own it. |
| **10 independent module SPAs** | Each module has its own `index.html` + JS, loaded by the base shell. Decouples build and testing. |
| **Workflow engine as service layer** | Not a BPMN tool — the RACI logic lives in each module's `data_service.py`. Explicit, auditable, testable. |
| **Risk Register as write-through cache** | Any module that changes risk state writes directly to the central `risk_register` table. No eventual consistency — risk data is live. |
| **External integrations via webhook stubs** | Phase 0 builds stubs that log "would send to ERP here". Real API keys and endpoints configured in Phase 3. |
| **No mobile app** | BRS asks for offline-capable field intake (NFR-SHE-008). Solved via PWA (Progressive Web App) instead of native app — lower cost, same offline capability. |

---

**Next step:** I can start building Phase 0 if you're ready, or we can discuss any part of this plan first.
