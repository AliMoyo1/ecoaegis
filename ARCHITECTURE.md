# SHE Management Platform - System Architecture

**Source:** SHE_BRS_1.1.0_Final.docx (Author: Brendon Buwerimwe, June 2026)
**Companion docs:** SOLUTION_DESIGN.md (schema + API surface), IMPLEMENTATION_PLAN.md (delivery phases)
**Status:** Draft v0.2
**Date:** August 2026
**Author:** Ali Moyo (with Hermes Agent)

---

## 0. Document Purpose

This is the authoritative architecture for the SHE Management Platform. It describes:

1. The overall system and every component
2. How the components connect (internal and external)
3. The Hermes intelligence layer and how it drives WhatsApp + web
4. **The ThemisIQ integration** - how the two platforms share operational and enterprise risk
5. Every integration contract, mapped to real code where it exists

**Integration facts were verified against the actual ThemisIQ source tree** (`~/Documents/One For All/oneforall/`), not against documentation. Where the code and docs disagree, the code wins and the discrepancy is flagged.

---

## 1. System Context

```
                          ┌─────────────────────────────────────────────┐
                          │                 USERS                        │
                          │  SHE Champions │ Officers │ Managers │ CRO  │
                          │  COO/CEO │ Board │ HODs │ Line Managers      │
                          │  External: Complainants, EMA, Regulators    │
                          └──────┬──────────────────────────┬───────────┘
                                 │                          │
                  WhatsApp       │                          │   Web browser
                  (field users)  │                          │   (dashboards, approvals)
                                 ▼                          ▼
              ┌─────────────────────────────────────────────────────────┐
              │                  HERMES (Layer 1)                       │
              │  WhatsApp gateway   │  web chat   │  cron scheduler     │
              │  skills (per module)│  webhooks   │  notifications      │
              │  PROPOSES - never enforces                              │
              └───────────────┬─────────────────────────────────────────┘
                              │ she_platform toolset (restricted API calls)
                              │ Core -> Hermes webhooks (events for delivery)
                              ▼
              ┌─────────────────────────────────────────────────────────┐
              │                  SHE CORE (Layer 2)                     │
              │  FastAPI + PostgreSQL   |   workflow engine   |   RBAC  │
              │  append-only audit log | statutory templates            │
              │  DECIDES - enforces - logs                              │
              └──────┬───────────────────────────────┬──────────────────┘
                     │                               │
        REST + signed webhooks           REST (session/API-key)
                     │                               │
                     ▼                               ▼
      ┌──────────────────────────┐     ┌──────────────────────────────┐
      │   THEMISIQ (Layer 3)     │     │   EXTERNAL SYSTEMS           │
      │   GRC platform:          │     │  Procurement ERP (M)         │
      │   ERM enterprise risk    │     │  LMS (M)                     │
      │   ORM operational risk   │     │  EMA portal (M)              │
      │   GRID audit, ARIA gov   │     │  NSSA / ZRP statutory (M)    │
      │   BCM, Sentinel, Evidence│     │  Corporate Comms (S)         │
      │                          │     │  Board reporting (S)         │
      └──────────────────────────┘     └──────────────────────────────┘
```

### Layer Responsibilities (Non-Negotiable)

| Layer | Role | Authority |
|---|---|---|
| **Hermes** | Conversational front door, NL intake, drafting, triage, notifications, deadline engine | Proposes. Never enforces. |
| **SHE Core** | System of record, workflow engine, RBAC, gates, audit, statutory rendering | Decides. Enforces. Logs. |
| **ThemisIQ** | Enterprise + operational risk registers, audit, governance, obligations | Source of truth for enterprise risk picture |
| **External** | ERP/LMS/EMA/NSSA/ZRP/Comms/Board | Data exchange partners |

Why Hermes cannot enforce: BRN-SHE-001, 005, 007 require *technical prevention* (no PTW without approved RA, no skipped approvals, no re-occupation without certificate). LLMs are probabilistic; enforcement must be deterministic code with unit tests. The BRS's own UAT (Section 10.1) tests "approval chain integrity confirming no step can be skipped" - a deterministic test against the workflow engine.

---

## 2. User Identity and Multi-User Separation

### 2.1 Unified Identity

One `users` table is the identity spine for both surfaces:

```
users (id, employee_id, full_name, email, phone, role_tier, active)
```

Role tiers (Section 2 of BRS): champion < officer < she_manager < she_hod < line_manager < cro < coo_ceo < board_chair. External actors (complainants, EMA consultants, regulators) live in `external_actors` with portal-only scope.

### 2.2 WhatsApp Instance Separation

- Hermes gateway gives **per-chat sessions natively** (session store keyed per chat) - each phone number messaging the SHE bot gets its own isolated conversation, history, and context. Verified in Hermes docs: "Each platform adapter receives messages, routes them through a per-chat session store."
- **Identity binding:** `phone_bindings` table maps phone -> user_id via a one-time verification flow (employee confirms name/ID in chat). Gateway DM pairing restricts access to bound numbers only.
- Every action Hermes proposes via WhatsApp carries the bound `user_id`; SHE Core re-checks role at the enforcement point. Wrong-role or unbound numbers get 403 from RBAC, not from the model.

### 2.3 Web Instance Separation

- Web app uses real sessions, SSO/MFA per SEC-SHE-001 (org identity provider: Keycloak / Azure AD / existing IdM).
- Web users resolve to the same `users` rows as WhatsApp bindings - one identity, two front doors.
- External portal routes are the only thing external actors see; internal modules are unreachable (enforced in Core, not hidden in UI).

### 2.4 Cross-Surface Continuity

A SHE Officer logs an incident from her phone en route to site; the case continues in the web app at her desk. The case lives in SHE Core, not in the chat session. Chat sessions are *views* over Core state, never the source of truth.

---

## 3. SHE Core Architecture

### 3.1 Stack

| Layer | Choice | Rationale |
|---|---|---|
| API | FastAPI + Uvicorn | Same stack family as ThemisIQ; async, typed, testable |
| DB | PostgreSQL (dev: SQLite) | NFR-SHE-003 7-year retention, NFR-SHE-005 scalability, SEC-SHE-004 AES-256 at rest |
| Workflow | In-app state machine engine | BRS demands no-bypass chains; service-layer state machines are unit-testable |
| Auth | Session/JWT + SSO/MFA | SEC-SHE-001 |
| Scheduling | APScheduler (Core) + Hermes cron (Layer 1) | NFR-SHE-002 60s routing |
| Audit | Append-only `audit_log`, hash-chained | NFR-SHE-004, SEC-SHE-005 |

### 3.2 Data Model (Full)

Master registries and case tables (full DDL in SOLUTION_DESIGN.md):

| Table | Purpose | BRS Ref |
|---|---|---|
| `users`, `phone_bindings`, `external_actors` | Identity + binding | Sec 2 |
| `risk_register` | SHE Central Risk Register (27-field model) | Sec 3.1, 11.1 |
| `key_issues` | Key Issues SHE Tracker | Sec 3.2 |
| `vendors`, `vendor_certifications`, `ptw` | Vendor roster + PTW | Sec 3.3, SHECMV |
| `grievances` | Community Grievance Register | Sec 3.4, SHECCM |
| `training_records`, `competency_matrix` | Training + LMS sync | Sec 3.5, SHET&A |
| `incidents`, `investigations`, `root_cause_reports`, `statutory_submissions` | SHEIMI | Sec 5.5 |
| `workplans`, `workplan_tasks` | SHEAWPM | Sec 5.6 |
| `emergency_cases`, `drills`, `eprp_plans`, `relocation_tasks` | SHEER + SHEEPRP | Sec 5.4, 5.7 |
| `communications`, `awareness_plans` | SHE EC&SC | Sec 5.3 |
| `reports` | SHER | Sec 5.8 |
| `eia_projects`, `eia_screenings`, `ema_decisions` | SHEIA | Sec 5.10 |
| `kpi_targets`, `kpi_actuals` | ESG KPI module | Sec 12 |
| `stakeholders`, `engagements`, `quarterly_feedback` | Stakeholder register | Sec 13 |
| `workflow_instances`, `workflow_steps` | Approval chains as state machines | Sec 4, BRN-005 |
| `notifications` | Email + in-platform + WhatsApp, retry/backoff | NFR-SHE-006 |
| `audit_log` | Tamper-evident, hash-chained, append-only | NFR-SHE-004, SEC-SHE-005 |
| `themisiq_links` | Cross-platform entity mapping (see Sec 6.5) | new |

### 3.3 Workflow Engine

Generic state machine. Each approval chain = `workflow_instances` row + ordered `workflow_steps`. Only the role in `step.required_role` may act; `advance()` is role-checked and idempotent. Escalation on approver unavailability logs delegation event (timestamp, original assignee, reason code) per BRN-005.

Chains per module:

| Module | Chain | Steps (RACI) |
|---|---|---|
| SHEIMI | Investigation commissioning | CRO -> CEO |
| SHEIMI | Root cause report | CRO -> COO -> CEO -> Board Chair |
| SHECMV | RA -> PTW | Officer submit -> SHE Manager approve |
| SHECCM | Resolution plan | Officer -> Line Manager -> SHE Manager |
| SHEIA | EIA report | SHE Manager approve -> EMA submit |
| SHEEPRP | Disaster plan | Champion -> Senior Mgmt (resource check per step) |
| SHEER | Site Safe for Occupation | SHE Manager + HOD Security (dual sign-off) |
| SHE EC&SC | External comms | Officer draft -> SHE HOD approve -> Corp Comms |
| SHEAWPM | Annual workplan | Manager -> Committee -> CRO -> COO |
| SHER | Reports | Manager -> CRO -> Chart of Authority |

### 3.4 Risk Scoring (Sec 11.2) - Computed in DB

- Inherent = Likelihood x Impact (1-25)
- Residual = Inherent / Control Effectiveness (1-5)
- Priority: >= 12 High (CRO), 8-11 Medium (SHE Manager), <= 7 Low (operational)
- Implemented as `GENERATED ALWAYS` columns: read-only, cannot be overridden (FNR-SHE-053)
- Residual >= 12 triggers notification + CRO dashboard flag until score reduced or CRO acknowledges with written rationale (FNR-SHE-056)

---

## 4. Hermes Integration Surface (Layer 1)

### 4.1 Hermes Tools - `she_platform` Toolset

Hermes gets a restricted toolset that ONLY calls SHE Core API (no terminal, no file writes, no browser):

| Tool | Purpose | Min Role |
|---|---|---|
| `she_intake_incident` | Guided NL intake -> POST /incidents | champion |
| `she_check_ptw_status` | Read PTW state | champion |
| `she_create_grievance` | Portal/WhatsApp grievance intake | external/complainant |
| `she_check_risk` | Risk register query | champion |
| `she_draft_report` | Draft SHER report from metrics | officer |
| `she_escalate_case` | Propose escalation -> Core decides | role-checked |
| `she_lookup_deadline` | Statutory deadlines for a case | champion |

Enforcement: the `she_platform` toolset is the ONLY toolset enabled for the WhatsApp platform (`platform_toolsets.whatsapp`), and each tool's server-side call carries the bound user context. The agent cannot propose outside Core's gates.

### 4.2 Hermes Cron - Deadline Engine

| Job | Schedule | BRS Rule |
|---|---|---|
| Incident 24h/48h deadline | every 15 min | BRN-002 |
| Certification expiry (30d warning, suspend on lapse) | daily | BRN-013 |
| Mock drill year-end 60d warning | daily | BRN-011 |
| Report overdue + grace -> COO | daily | BRN-012 |
| Key Issues age escalation | daily | Sec 3.2 |
| Stakeholder reminders (7d) + 30d/60d escalation | daily | SE-FR-003, SE-FR-008 |
| Risk review date prompts | daily | FNR-SHE-055 |
| Annual sustainability calendar (7/3/1d alerts) | daily | AR-FR-001 |

### 4.3 Webhooks

- **Core -> Hermes:** `POST /webhooks/hermes` - event notifications (PTW expired, grievance escalated, statutory due, KPI breach). Hermes drafts human message, delivers to right channel (WhatsApp/email/in-platform per user preference).
- **Hermes -> Core:** `POST /webhooks/shec-core` - actions proposed in chat that require Core enforcement (approve step, log observation, create case).

### 4.4 Skills

One skill per module (`sheimi-intake`, `shecmv-ptw`, `sheccm-grievance`, `sheeprp-drill`, `sheer-response`, `sheawpm-workplan`, `sher-report`, `shetna-training`, `sheia-eia`, `sheecsc-comms`) encoding guided conversations: mandatory fields, phrasing, follow-up questions. Skills never bypass Core.

---

## 5. External System Integrations

All per BRS Section 7. REST + JSON, TLS 1.2+, rate-limited, IP allowlisted (SEC-SHE-006). Priority M = must, S = should.

| # | System | Direction | Contract | Priority |
|---|---|---|---|---|
| 1 | Procurement ERP | SHE -> ERP | POST hold/release/flag vendor onboarding tasks on SHECMV/SHEIA/SHET&A events | M |
| 2 | Statutory Reporting Engine | SHE -> NSSA/EMA/ZRP | Render incident/emergency data into regulatory templates; track submission + acknowledgement | M |
| 3 | Corporate Risk Register API | SHE <-> (internal) | Bidirectional sync with SHE risk_register (see Sec 6) | M |
| 4 | LMS | SHE <-> LMS | Push completion/competency/refresher; pull gap data | M |
| 5 | Corporate Communications | SHE -> Comms | Approved docs + metadata (segment, medium, frequency, HOD timestamp); return dispatch confirmations | S |
| 6 | Board Reporting | SHE -> Board infra | PDF/Word/Power BI feed of approved reports | S |
| 7 | EMA Portal | SHE -> EMA | EIA report submission + acknowledgement tracking | M |

All external calls are idempotent (client-generated `X-Idempotency-Key`), retried with backoff, logged to `integration_logs`.

---

## 6. ThemisIQ Integration (The Core of This Document)

### 6.1 Why Connect Them

The BRS defines a Central Risk Register fed by SHE incidents, grievances, emergencies, and EIA closures. ThemisIQ already manages **enterprise risk (ERM)** and **operational risk (ORM)** across the organisation, with an event bus, audit, and a shared `risk_register` view. Running two disconnected risk systems would recreate Risk Category 1 from the BRS ("incident data... maintained in separate, unconnected systems"). The two platforms share one risk picture:

- **SHE Core** is the system of record for *safety/environmental* risk detail (27-field register, statutory instruments, PCN owners).
- **ThemisIQ** is the system of record for the *enterprise/operational* risk picture (ERM register, appetite, KRIs, obligations, audit).
- **Hermes** orchestrates notifications between them (deadline nags, escalation alerts) but never stores risk state.

### 6.2 Integration Topology

```
   SHE CORE                                    THEMISIQ
┌────────────────────┐                 ┌───────────────────────────┐
│ themisiq_links     │                 │ webhooks table            │
│ (mapping table)    │                 │  - url = SHE webhook      │
│                    │                 │  - secret = shared secret │
│ /webhooks/themisiq ◄─── signed POST ──── core/webhooks.py        │
│  (verify HMAC)     │   (outbound     │  (dispatch_event on       │
│                    │    webhooks)    │   every emit())           │
│                    │                 │                           │
│ REST client ───────┼─── POST /erm/api/risks ────► ERM module     │
│  (session or       │─── POST /orm/api/events ──► ORM module      │
│   API key)         │─── POST /erm/api/obligations ► obligations  │
└────────────────────┘                 └───────────────────────────┘
```

### 6.3 Direction 1: ThemisIQ -> SHE (Outbound Webhooks - WORKS TODAY)

Verified in `core/webhooks.py`: when any event is emitted on the ThemisIQ bus, every active webhook subscribed to that event_type receives a signed POST.

**Envelope (stable contract, from source):**

```json
{
  "event_type": "erm.risk.escalated",
  "source_module": "erm",
  "source_entity_type": "risk",
  "source_entity_id": 42,
  "timestamp": "2026-08-08T10:00:00+00:00",
  "organisation_id": null,
  "triggered_by_user": 7,
  "data": { }
}
```

**Signature:** `X-ThemisIQ-Signature: sha256=<hex>` = HMAC-SHA256 of the raw body using the webhook's stored secret. SHE Core MUST verify this before trusting the payload.

**Delivery guarantees (from source):** 3 attempts, exponential backoff (1s, 2s, 4s), 10s timeout; 2xx = success; 429/5xx/network = retry; 4xx non-429 = permanent failure, no retry. Every attempt logged to `webhook_logs` (URL, code, body, success). Delivery never blocks the source operation.

**Setup (no ThemisIQ code changes):** register a webhook in ThemisIQ admin (`/admin/webhooks` or `POST /api/admin/webhooks`) with:
- url = `https://she-core.internal/webhooks/themisiq`
- secret = shared secret (stored in vault, SEC-SHE-007)
- events = comma-separated list of subscribed event types

**Subscribed events:**

| Event | When | SHE Action |
|---|---|---|
| `erm.risk.identified` | New enterprise risk | Create/update SHE risk mirror (High priority) |
| `erm.risk.escalated` | Severity bumped | Flag in SHE register, notify SHE Manager + CRO via Hermes |
| `erm.risk.mitigated` / `erm.risk.closed` | Risk lifecycle | Sync SHE mirror status |
| `erm.appetite.breached` | KRI/threshold crossed | Notify CRO; check SHE exposure |
| `orm.event.logged` | New op-risk event | If SHE-related (dept/process match) create SHE incident link |
| `orm.event.elevated` | Op-risk severity bumped | Escalate to ERM mirror; notify |
| `orm.event.resolved` | Op-risk closed | Sync |
| `bcm.incident.declared` / `resolved` | BCM incident | Cross-check SHE emergency cases |
| `sentinel.breach.confirmed` | Privacy breach | If site/facility impact, link to SHE emergency awareness |

### 6.4 Direction 2: SHE -> ThemisIQ (Inbound REST - TWO GAPS TO CLOSE)

SHE Core pushes risk-relevant state into ThemisIQ via its existing JSON APIs:

| ThemisIQ endpoint (verified) | SHE event | Payload mapping |
|---|---|---|
| `POST /erm/api/risks` | SHE residual risk (grievance/incident/EIA closure) | title, description, category, likelihood, impact, treatment, owner, source_module='she', source_risk_id=<she risk id> |
| `POST /orm/api/events` | SHE incident (safety/environmental) | title, event_type, severity, department, process_affected, root_cause, financial_impact, owner |
| `POST /erm/api/obligations` | SHE statutory obligations (NSSA/EMA/ZRP/POTRAZ) | regulator, regulation_name, obligation, due_date, owner, status |
| `PUT /erm/api/risks/{id}` | Risk score changes | residual scores, status |

**GAP 1 - Auth (verified):** ThemisIQ's API routes are protected by `require_capability(...)` which validates **session cookies**, not API keys. The `api_keys` table EXISTS in the schema (`key_hash`, `key_prefix`, `scopes`, `is_active`, `expires_at`) but **no middleware reads it** - the table is unused dead schema. Two options:

- **Option A (recommended, small ThemisIQ change):** wire `api_keys` into middleware - add an `X-API-Key` header check alongside session auth on the JSON routes. ~1 file change, uses existing table, gives scoped M2M tokens (`scopes='read'` / `scopes='read,write'`). This is a ThemisIQ dev-branch change.
- **Option B (no ThemisIQ change, less clean):** create a dedicated "SHE Integration" service account in ThemisIQ, log in once to get a session cookie, use it for API calls. Fragile (session expiry, 24h max-age, must re-login) and pollutes audit with a synthetic user. Not recommended for production.

**GAP 2 - Cross-module link namespace (verified):** `core/links.py` `_VALID_MODULES = {aria, grid, bcm, sentinel, platform, evidence, erm, orm}` - **`she` is not a valid module**. Any `create_cross_module_link()` call with source_module='she' is rejected with a warning. Small change: add `"she"` to the frozenset so SHE cases can link to ThemisIQ artifacts (e.g., SHE incident -> ERM risk via `triggers` relationship). ThemisIQ dev-branch change.

### 6.5 Entity Mapping (Cross-Platform)

SHE Core maintains `themisiq_links` (id, she_entity_type, she_entity_id, themisiq_entity_type, themisiq_entity_id, direction, created_at) so drill-down works both ways:

| SHE entity | ThemisIQ entity | Relationship | Trigger |
|---|---|---|---|
| incident (SHEIMI) | orm_event | `derived_from` (SHE -> ThemisIQ) | incident created |
| incident root_cause closed | orm_event + erm_risk | `triggers` | closure (BRN-003 pattern) |
| grievance residual risk | erm_risk | `triggers` | grievance closed residual |
| emergency case closed | erm_risk + orm_event | `derived_from` | SHEER close-out |
| EIA rejected/high-risk | erm_risk | `triggers` | EMA decision |
| statutory obligation | erm_regulatory_obligation | `related` | obligation created |
| enterprise risk (mirror) | erm_risk | `related` (bidirectional) | webhook received |

### 6.6 BRN-003 Through the ThemisIQ Lens

BRN-SHE-003: grievance closed as residual risk must (1) flag asset in Central Risk Register, (2) mandate targeted mock drill, (3) include in next Board report - simultaneously.

With ThemisIQ connected, the transaction becomes:

```
SHE Core transaction (atomic):
  1. risk_register row created/flagged (SHE register)
  2. drill record created (SHEEPRP)
  3. Board report item queued (SHER)
  4. themisiq_links row: grievance -> erm_risk (triggers)
  5. POST /erm/api/risks (SHE residual risk appears in enterprise register)

Then: outbound webhook to Hermes:
  "Grievance G-2026-001 closed as residual risk. Risk flagged in SHE + ERM
   registers, targeted drill scheduled, Board report item queued."
```

### 6.7 Reliability and Failure Modes

| Failure | Behavior |
|---|---|
| ThemisIQ webhook delivery fails (network/5xx) | ThemisIQ retries 3x with backoff; SHE verifies signature on each attempt; webhook_logs records all |
| SHE Core down when webhook arrives | ThemisIQ retries; if still failing, event stays in ThemisIQ `events` table (status=pending) for replay - verified the events table has status + processed_at columns |
| SHE -> ThemisIQ API call fails | SHE retries with backoff; idempotency via source_risk_id/source_module (ThemisIQ dedupes on source_entity) |
| Signature mismatch | SHE Core rejects payload, logs alert, never processes |
| ThemisIQ unavailable for long period | SHE continues standalone (risk register is SHE's own); sync queue (`themisiq_links` + `integration_logs`) replays on recovery |

**Degraded-mode principle:** SHE Core must function fully even if ThemisIQ is down. ThemisIQ is a risk *consumer/aggregator* for SHE; SHE is not dependent on ThemisIQ for its own compliance gates.

### 6.8 ThemisIQ Changes Required (Dev Branch)

| Change | File | Effort | Why |
|---|---|---|---|
| Wire `api_keys` table into auth middleware | `core/middleware.py` | Small | M2M auth for SHE -> ThemisIQ |
| Add `"she"` to `_VALID_MODULES` | `core/links.py` | Trivial | Cross-module links for SHE entities |
| (Optional) register SHE webhook subscription docs | launcher admin UI already exists | None | Admin UI already supports CRUD + test ping + logs |

No changes to ThemisIQ's event catalog or webhook engine are needed - they are complete and tested (`tests/test_webhooks.py`, `tests/live_webhook_test.py` exist in the prod tree).

---

## 7. Security Architecture

| Requirement | Implementation |
|---|---|
| SEC-SHE-001 Auth | SSO/MFA via org IdM (Keycloak/Azure AD); WhatsApp via phone binding + DM pairing |
| SEC-SHE-002 RBAC | 9 role tiers + external actors; capability map mirrors ThemisIQ's `core/rbac.py` pattern |
| SEC-SHE-003 TLS 1.2+ | All endpoints; API gateway rejects plain HTTP with 4xx |
| SEC-SHE-004 AES-256 at rest | PostgreSQL TDE/volume encryption; SQLite dev uses encrypted volume |
| SEC-SHE-005 Audit | Append-only `audit_log`, hash-chained (prev_hash -> hash), no UPDATE/DELETE grants, 7-year retention + archival |
| SEC-SHE-006 API protection | Rate limiting per consumer, IP allowlists for ERP/LMS/EMA/ThemisIQ, malformed request rejection |
| SEC-SHE-007 Secrets | HashiCorp Vault (or equiv): DB creds, ThemisIQ webhook secret, API keys, ERP creds. Never in config/env/source |
| SEC-SHE-008 Pentest | Pre-prod + annual; findings tracked 30d |

ThemisIQ webhook verification: HMAC-SHA256 of raw body vs `X-ThemisIQ-Signature` header, constant-time compare.

---

## 8. Deployment Topology

```
┌────────────────────────────────────────────────────────────┐
│  VPS / cloud host (99.5% availability, NFR-SHE-001)        │
│                                                            │
│  ┌─────────────┐   ┌──────────────────┐   ┌─────────────┐  │
│  │ Hermes      │   │ SHE Core         │   │ ThemisIQ    │  │
│  │ gateway     │──▶│ FastAPI          │   │ FastAPI     │  │
│  │ (WhatsApp   │   │ + PostgreSQL     │◀─▶│ + SQLite    │  │
│  │  Cloud API) │   │ + workflow eng.  │   │ (GRC)       │  │
│  └─────────────┘   └──────────────────┘   └─────────────┘  │
│       │                    │                    │          │
│       └── web app (React/SPA, SSO/MFA, external portal)    │
│                                                            │
│  Nginx/TLS termination | Vault (secrets) | backups (7yr)   │
└────────────────────────────────────────────────────────────┘
```

- Hermes and ThemisIQ can co-locate on the same host (ThemisIQ is a single binary + SQLite; Hermes is the gateway process).
- SHE Core gets PostgreSQL because 7-year retention + concurrent writers (NFR-SHE-003, 005) exceed SQLite's comfort zone (ThemisIQ's own ARCHITECTURE.md notes SQLite WAL "doesn't tolerate concurrent writers across hosts").
- Offline field intake (NFR-SHE-008): WhatsApp messages queue on the phone natively - this is the primary offline channel. Web users without WhatsApp get a PWA with local storage + sync.

---

## 9. End-to-End Flows

### 9.1 Field Incident (WhatsApp) -> Statutory Submission

```
1. SHE Champion messages SHE bot on WhatsApp: "cable fell on a worker at Harare depot"
2. Hermes (sheimi-intake skill) runs guided intake: who, when, severity, witnesses, photos
3. Hermes calls she_intake_incident -> POST /incidents (bound user, role=champion)
4. SHE Core creates incident, ref auto-assigned, statutory_deadline = +48h if critical
5. Core emits webhook to Hermes: "Incident INC-2026-0041 logged, critical, deadline 48h"
6. Hermes notifies SHE Unit + Line Manager (BRN-002 initial notification)
7. CRO commissions investigation (workflow: CRO -> CEO)
8. Root cause report routes CRO -> COO -> CEO -> Board Chair (no skipping)
9. On closure: Core updates risk_register, generates NSSA/EMA/ZRP templates
10. Statutory Submission engine files reports, tracks acknowledgements
11. Core POSTs to ThemisIQ /orm/api/events + /erm/api/risks (risk enters enterprise picture)
12. Hermes cron watches 24h/48h deadlines; escalates to CRO if not met
```

### 9.2 Community Grievance -> BRN-003 Triple + ThemisIQ

```
1. Complainant submits via external portal (or WhatsApp grievance intake)
2. Grievance logged in SHECCM register, case ref assigned
3. Investigation + resolution plan -> Line Manager implements
4. Resolution outcome: "residual risk"
5. Core transaction (atomic): risk_register flag + drill record + Board item + ThemisIQ erm_risk push
6. Core sends complainant notification (BRN-010) - case cannot close without notified_at
7. Hermes notifies HOD + CRO of escalation (FNR-SHE-010)
```

### 9.3 Enterprise Risk from ThemisIQ -> SHE

```
1. ThemisIQ ERM risk escalates (severity bump) -> emit(erm.risk.escalated)
2. core/webhooks.py dispatches signed POST to SHE webhook
3. SHE Core verifies HMAC, maps via themisiq_links, updates SHE mirror
4. Core webhooks Hermes: "Enterprise risk R-2041 escalated - SHE exposure check needed"
5. Hermes notifies SHE Manager + CRO on their preferred channel
```

---

## 10. Non-Functional Requirements Mapping

| NFR | How it is met |
|---|---|
| NFR-SHE-001 99.5% availability | Managed host, gateway as service, maintenance windows out of hours + 48h notice |
| NFR-SHE-002 60s routing / 3s views | In-process workflow engine, indexed queries, async webhooks |
| NFR-SHE-003 7-year retention | PostgreSQL + archival strategy, configurable per record type |
| NFR-SHE-004 audit integrity | Hash-chained append-only log, periodic verification |
| NFR-SHE-005 scalability | PostgreSQL, stateless API, horizontal scale at the API tier |
| NFR-SHE-006 notification reliability | Email + in-platform + WhatsApp, retry with backoff, delivery logs |
| NFR-SHE-007 REST/webhook standards | All integrations REST + JSON; webhook support everywhere |
| NFR-SHE-008 offline resilience | WhatsApp native queueing + PWA for web users |
| NFR-SHE-009 WCAG 2.1 AA | Colour-independent indicators (BRS explicitly requires labels/patterns/text) |

---

## 11. Phased Delivery (Detail in IMPLEMENTATION_PLAN.md)

| Phase | Scope | ThemisIQ dependency |
|---|---|---|
| 0 | Spine: identity, RBAC, audit, workflow engine, risk register, Hermes gateway + toolset lockdown | None (build standalone) |
| 1 | SHEIMI + SHECMV (incidents, PTW, BRN-002) | None |
| 2 | SHECCM + BRN-003 triple + Key Issues | None (design themisiq_links now) |
| 3 | SHEEPRP + SHEER + SHET&A | None |
| 4 | SHER + ESG + stakeholders + annual reporting | None |
| 5 | External integrations (ERP, LMS, EMA, statutory) | **ThemisIQ connect: api_keys middleware + _VALID_MODULES change + webhook registration + /erm /orm pushes** |
| 6 | Hardening, UAT, go-live | Webhook replay verification |

ThemisIQ integration is deliberately Phase 5: SHE must prove its own risk register first, then share it. The two ThemisIQ code changes (api_keys middleware, _VALID_MODULES) can be prepared any time on the ThemisIQ dev branch.

---

## 12. Open Questions

1. **CURA + Cassava AI Incident Module** (ESG-FR-007): is the SHE platform replacing or integrating with them? Affects KPI data source wiring.
2. **WhatsApp bridge:** WhatsApp Cloud API (official, prod) vs personal Baileys bridge (pilot). Cloud API needs Meta Business verification + template approval.
3. **Identity provider:** confirm org IdM for SEC-SHE-001.
4. **ThemisIQ deploy target:** is ThemisIQ already hosted somewhere SHE can reach, or does it co-locate with SHE Core on the new host?
5. **Statutory templates:** NSSA/EMA/ZRP report formats and submission channels (portal/email/physical).
6. **Board reporting format:** PDF/Word/Power BI feed - confirm Board infrastructure.
7. **ThemisIQ api_keys scoping:** confirm which ThemisIQ roles the SHE integration account should map to (`erm.risk.manage`, `orm.event.manage` at minimum).

---

## 13. Verified Integration Facts (Source-Audited)

All of the following were verified against `~/Documents/One For All/oneforall/` source on 2026-08-08:

1. `core/webhooks.py` - complete outbound webhook engine: HMAC-SHA256 signing (`X-ThemisIQ-Signature: sha256=<hex>`), 3 retries/backoff, `webhook_logs` table, stable envelope, non-blocking dispatch from `emit()`.
2. `core/events.py` `emit()` - stores to `events` table (status=pending, processed_at), runs in-process handlers, then fans out webhooks. Replayable.
3. `events` table has `status` + `processed_at` columns - replay/observability supported.
4. ERM API: `/erm/api/risks` GET/POST/PUT/DELETE, `/erm/api/obligations` (regulator, regulation_name, obligation, due_date, status, linked_erm_risk_id), pillars/objectives/emerging risks/frameworks endpoints - all session-auth via `require_capability`.
5. ORM API: `/orm/api/events` GET/POST/PUT, `/orm/api/kris`, `/orm/api/rcsa` - session-auth.
6. `api_keys` table exists (key_hash, key_prefix, scopes, is_active, expires_at) but NO middleware uses it - M2M auth is a gap (GAP 1).
7. `core/links.py` `_VALID_MODULES` does NOT include `she` (GAP 2).
8. Shared `risk_register` table (title, description, source_module, source_entity_type, source_entity_id, category, likelihood, impact, risk_score GENERATED, risk_level, owner, treatment, status, review_date) - SHE can write rows with source_module='she' if desired.
9. Webhook admin: `/admin/webhooks` UI + `/api/admin/webhooks` CRUD + `/api/admin/webhooks/{id}/test` + logs - registration requires no code.
10. ThemisIQ has its own workflow engine (`workflow_definitions` table) and RLS (`core/rls.py`, business-unit scoping via `bu_scope_ids`) - SHE integration account must be granted business-unit scope.
11. ThemisIQ modules: launcher, aria, grid, bcm, sentinel, erm, orm, evidence - all mounted in `main.py` with `include_router`.
12. Dev worktree at `~/Documents/themis-plans/oneforall/` is stubbed (89-byte files) - ThemisIQ changes must be made against synced real files from prod, per project convention.

---

*End of architecture document. Companion: SOLUTION_DESIGN.md (schema/API detail), IMPLEMENTATION_PLAN.md (phases).*
