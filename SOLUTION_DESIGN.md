# SHE Management Platform - Solution Design

**Source:** SHE_BRS_1.1.0_Final.docx (Author: Brendon Buwerimwe, June 2026)
**Companion:** [ARCHITECTURE.md](ARCHITECTURE.md) (authoritative system architecture incl. ThemisIQ integration) - this doc holds the schema + API surface detail
**Status:** Draft v0.1
**Date:** August 2026

---

## 1. Architectural Position (Read This First)

The BRS demands **deterministic enforcement**: BRN-SHE-001 ("manual or offline PTW issuance is strictly prohibited"), BRN-SHE-005 ("approvals may not be skipped, bypassed, or delegated"), BRN-SHE-007 (re-occupation gate). These are *system-level technical preventions*. An LLM is probabilistic and therefore **cannot be the enforcement layer**.

The system is therefore built in two layers:

```
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 1: HERMES (intelligence layer + front door)                  │
│                                                                     │
│  WhatsApp gateway   |   web chat   |   cron scheduler   |   skills  │
│                                                                     │
│  NL intake, triage, drafting, summarising, notifications,           │
│  deadline alerts, escalation messages, guided forms                 │
│                                                                     │
│  PROPOSES. Never decides. Never enforces.                           │
├─────────────────────────────────────────────────────────────────────┤
│  LAYER 2: SHE CORE (system of record - deterministic, ours)         │
│                                                                     │
│  FastAPI + PostgreSQL  |  workflow engine  |  RBAC                  │
│  append-only audit log |  statutory templates |  gate logic         │
│                                                                     │
│  DECIDES. Enforces. Logs everything.                                │
├─────────────────────────────────────────────────────────────────────┤
│  EXTERNAL: ERP procurement | LMS | EMA portal | NSSA/ZRP | Comms    │
└─────────────────────────────────────────────────────────────────────┘
```

### Division of Labour

| The BRS wants | Owner | Why |
|---|---|---|
| No PTW without approved Risk Assessment (BRN-001) | SHE Core (gate in code) | Deterministic, auditable |
| Approval chain cannot be skipped (BRN-005) | SHE Core (workflow engine) | LLM cannot be trusted to enforce |
| EIA clearance gate blocks project (BRN-004) | SHE Core | Technical block, not a suggestion |
| Incident logged within 24h, statutory notice in 48h (BRN-002) | Hermes cron + WhatsApp + Core API | Hermes nags, Core records |
| Field worker reports incident in natural language | Hermes (guided intake, drafts record) | Exactly what an agent is good at |
| Draft Board report from raw metrics | Hermes (drafting) | Human review follows anyway |
| BRN-003 triple trigger (risk flag + drill + board item) | SHE Core (single transaction) | 3 side effects = 1 atomic unit |
| Statutory report to NSSA/EMA/ZRP | SHE Core (template render + submission) | Format must be exact |

### Why Not "Hermes Does Everything"?

- Approval-skipping prevention, PTW gating, and risk scoring are **state machines and constraint checks**. They must be unit-testable to NFR-SHE-002 (60s routing) and auditable to SEC-SHE-005.
- The audit log (NFR-SHE-004, SEC-SHE-005) must be tamper-evident and 7-year retained. Hermes session transcripts are not that log.
- UAT (Section 10.1) explicitly tests "approval chain integrity confirming no step can be skipped" - this is a deterministic test against the workflow engine.

---

## 2. User Identity and Session Model (The Multi-User Question)

**Requirement:** multiple users across WhatsApp and web, isolated instances, RBAC per Section 2, external actors restricted to portals (SEC-SHE-002).

### 2.1 Unified Identity

One `users` table is the single source of truth. Both surfaces resolve to it:

```
users (id, employee_id, full_name, email, phone, role_tier, active)
```

**Role tier** encodes the Section 2 hierarchy: SHE Champion/Auditor < SHE Officer < SHE Manager/HoD SHE < SHE HOD < Line/Project Manager < CRO < COO/CEO < Board Chair. External actors (EMA consultant, regulator, complainant) are a separate `external_actors` table with portal-only access.

### 2.2 WhatsApp Instance Separation

- The Hermes gateway gives **per-chat sessions natively** (session store keyed per chat). Every phone number that messages the bot gets its own isolated conversation, context, and history. User A and User B are separate instances out of the box.
- **Identity binding:** `phone_bindings` table maps `phone_number -> user_id`, created through a one-time verification flow (employee confirms name/ID via the chat). The gateway's DM pairing authorization means only bound numbers can interact.
- Every action Hermes proposes via WhatsApp is stamped with the bound `user_id`. The Core API re-checks the role at the enforcement point. A message from an unbound or wrong-role number is rejected by RBAC, not by the model.

### 2.3 Web Instance Separation

- The web app (FastAPI + React) uses real sessions with SSO/MFA per SEC-SHE-001 (Keycloak / Azure AD / org identity provider).
- Each logged-in web user maps to the same `users` row as their WhatsApp binding - one identity, two front doors.
- External actors get a narrow portal route: grievance submission, EIA report delivery, statutory notice receipt. No internal module access (enforced in Core RBAC, not by UI hiding).

### 2.4 Session Model Diagram

```
 Identity layer (users + role tiers + external_actors)
        |
        +-- WhatsApp number ------> Hermes gateway session (per chat, isolated)
        +-- Web login (SSO/MFA) --> web app session
        +-- External portal ------> restricted portal session
        |
        v
   SHE Core API (every call: authenticated user + role check + gate)
        |
        v
   PostgreSQL (registries, cases, workflow_instances, audit_log)
```

**Cross-surface continuity:** a SHE Officer logs an incident from her phone en route, continues the same case in the web app at her desk. The case lives in the Core, not in the chat session.

---

## 3. Data Model (Phase 1 Spine)

Architect's reduction: the BRS is 10 modules, but structurally it is **5 registries + a case engine + gates + an audit log**. All tables share `created_by`, `created_at`, `updated_at`, `version`.

### 3.1 Core Tables

```sql
-- Identity & RBAC
CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id   TEXT UNIQUE NOT NULL,
  full_name     TEXT NOT NULL,
  email         TEXT UNIQUE,
  phone         TEXT UNIQUE,
  role_tier     TEXT NOT NULL CHECK (role_tier IN
    ('champion','officer','she_manager','she_hod','line_manager',
     'cro','coo_ceo','board_chair')),
  active        BOOLEAN DEFAULT TRUE
);

CREATE TABLE phone_bindings (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID REFERENCES users(id),
  phone         TEXT UNIQUE NOT NULL,
  verified_at   TIMESTAMPTZ,
  status        TEXT DEFAULT 'pending' CHECK (status IN ('pending','active','revoked'))
);

CREATE TABLE external_actors (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_type    TEXT CHECK (actor_type IN ('complainant','ema_consultant','regulator')),
  full_name     TEXT,
  phone         TEXT,
  email         TEXT,
  portal_access TEXT DEFAULT 'grievance_submit'  -- narrowest scope
);

-- Central Risk Register (Section 11.1 data model)
CREATE TABLE risk_register (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ref                TEXT UNIQUE NOT NULL,
  process_function   TEXT NOT NULL,
  pcn_owner_function TEXT,
  major_process      TEXT,
  process_owner      TEXT,
  process_objective  TEXT,
  key_risk           TEXT NOT NULL,
  risk_impact        TEXT,
  risk_category      TEXT CHECK (risk_category IN ('Operational','Financial','Regulatory','Strategic')),
  existing_controls  TEXT,
  likelihood         INT CHECK (likelihood BETWEEN 1 AND 5),
  impact             INT CHECK (impact BETWEEN 1 AND 5),
  inherent_score     INT GENERATED ALWAYS AS (likelihood * impact) STORED,  -- read-only
  control_effectiveness INT CHECK (control_effectiveness BETWEEN 1 AND 5),
  residual_score     NUMERIC GENERATED ALWAYS AS
                     (ROUND((likelihood * impact)::numeric / control_effectiveness, 2)) STORED,
  priority           TEXT GENERATED ALWAYS AS (
                     CASE WHEN (likelihood*impact)::numeric/control_effectiveness >= 12 THEN 'High'
                          WHEN (likelihood*impact)::numeric/control_effectiveness >= 8 THEN 'Medium'
                          ELSE 'Low' END) STORED,
  managerial_response TEXT CHECK (managerial_response IN ('Accept','Mitigate','Transfer','Avoid')),
  risk_direction     TEXT CHECK (risk_direction IN ('Increasing','Stable','Decreasing')),
  responsible_person TEXT,
  review_date        DATE,
  status_update      TEXT,
  compliant          BOOLEAN,
  statutory_instrument TEXT,
  source_module      TEXT,  -- SHECMV/SHECCM/SHEIMI/SHEER/SHEIA/...
  source_case_id     UUID,  -- drill-down to originating case (FNR-SHE-059)
  version            INT DEFAULT 1
);

-- Key Issues Tracker (Section 3.2)
CREATE TABLE key_issues (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  issue_type    TEXT CHECK (issue_type IN ('audit_exception','non_conformance','metric','observation')),
  description   TEXT NOT NULL,
  age_threshold_days INT DEFAULT 30,
  status        TEXT DEFAULT 'open' CHECK (status IN ('open','escalated','closed')),
  escalated_at  TIMESTAMPTZ
);

-- Vendor Compliance Roster + PTW (SHECMV)
CREATE TABLE vendors (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT NOT NULL,
  risk_profile  TEXT,
  ptw_eligible  BOOLEAN DEFAULT FALSE,  -- suspended on cert lapse (BRN-013)
  erp_vendor_id TEXT
);
CREATE TABLE vendor_certifications (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  vendor_id     UUID REFERENCES vendors(id),
  cert_type     TEXT NOT NULL,
  expires_on    DATE NOT NULL,
  renewed_on    DATE,
  active        BOOLEAN DEFAULT TRUE
);
CREATE TABLE ptw (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  vendor_id     UUID REFERENCES vendors(id),
  risk_assessment_approved BOOLEAN NOT NULL DEFAULT FALSE,  -- gate (BRN-001)
  scope         TEXT,
  site_boundaries TEXT,
  valid_from    DATE,
  valid_to      DATE,
  responsible_officer UUID REFERENCES users(id),
  status        TEXT DEFAULT 'draft' CHECK (status IN
                ('draft','pending_ra','issued','active','suspended','closed'))
);

-- Incidents (SHEIMI)
CREATE TABLE incidents (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ref           TEXT UNIQUE NOT NULL,
  reported_by   UUID REFERENCES users(id),
  occurred_at   TIMESTAMPTZ NOT NULL,
  logged_at     TIMESTAMPTZ DEFAULT now(),
  classification TEXT,
  severity      TEXT CHECK (severity IN ('critical','major','minor','near_miss')),
  description   TEXT NOT NULL,
  root_cause    TEXT,
  status        TEXT DEFAULT 'intake' CHECK (status IN
                ('intake','notified','investigation','report_review','remediation','statutory','closed')),
  ptw_id        UUID REFERENCES ptw(id),        -- auto-link vendor works (Integration #2)
  vendor_id     UUID REFERENCES vendors(id),
  statutory_deadline TIMESTAMPTZ  -- logged_at + 48h when critical (BRN-002)
);

-- Grievances (SHECCM)
CREATE TABLE grievances (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ref           TEXT UNIQUE NOT NULL,
  complainant   UUID REFERENCES external_actors(id),
  source_channel TEXT NOT NULL,
  received_at   TIMESTAMPTZ NOT NULL,
  classification TEXT,
  severity      TEXT,
  description   TEXT NOT NULL,
  investigator  UUID REFERENCES users(id),
  status        TEXT DEFAULT 'intake' CHECK (status IN
                ('intake','investigation','resolution_plan','negotiation','closed')),
  outcome       TEXT CHECK (outcome IN ('resolved','residual_risk')),
  notified_at   TIMESTAMPTZ,   -- BRN-010: notify before close
  notification_method TEXT
);

-- Workflow instances (approval chains as state machines - BRN-005)
CREATE TABLE workflow_instances (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  module        TEXT NOT NULL,          -- SHEIMI, SHECMV, ...
  case_id       UUID NOT NULL,
  current_step  TEXT NOT NULL,
  state         TEXT DEFAULT 'active' CHECK (state IN ('active','awaiting','completed','rejected','escalated')),
  created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE workflow_steps (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id   UUID REFERENCES workflow_instances(id),
  step_order    INT NOT NULL,
  required_role TEXT NOT NULL,          -- per RACI
  assignee      UUID REFERENCES users(id),
  status        TEXT DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','escalated')),
  decided_at    TIMESTAMPTZ,
  comments      TEXT,
  delegation_log JSONB  -- timestamp, original assignee, reason code (BRN-005)
);

-- Audit log (SEC-SHE-005: tamper-evident, 7-year)
CREATE TABLE audit_log (
  id            BIGSERIAL PRIMARY KEY,
  ts            TIMESTAMPTZ DEFAULT now(),
  user_id       UUID,
  action_type   TEXT NOT NULL,
  entity        TEXT NOT NULL,
  entity_id     UUID,
  prev_state    JSONB,
  new_state     JSONB,
  prev_hash     TEXT,
  hash          TEXT  -- sha256(prev_hash || ts || user || action || new_state)
);
-- Append-only: no UPDATE/DELETE grants on this table. Chain verified periodically.

-- Notifications (NFR-SHE-006: email + in-platform, retry with backoff)
CREATE TABLE notifications (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  recipient     UUID REFERENCES users(id),
  channel       TEXT CHECK (channel IN ('email','whatsapp','in_platform')),
  subject       TEXT,
  body          TEXT,
  status        TEXT DEFAULT 'pending' CHECK (status IN ('pending','sent','failed','retrying')),
  attempts      INT DEFAULT 0,
  next_retry_at TIMESTAMPTZ
);
```

### 3.2 Risk Scoring (Section 11.2) - computed in the database

- Inherent = Likelihood x Impact (1-25)
- Residual = Inherent / Control Effectiveness (1-5)
- Priority: residual >= 12 High (CRO dashboard), 8-11 Medium (SHE Manager queue), <= 7 Low (operational)
- Both scores are `GENERATED ALWAYS` columns: read-only, cannot be manually overridden (FNR-SHE-053)
- Residual >= 12 triggers a notification row + CRO dashboard flag (FNR-SHE-056), stays flagged until score reduced or CRO acknowledges with written rationale

---

## 4. Workflow Engine (Approval Chains as State Machines)

**Decision:** no BPMN tool. Each approval chain is a row in `workflow_instances` + ordered `workflow_steps`. A generic engine advances steps; only the role named in `required_role` may act on a step. This makes BRN-005 (no skipping, no downward delegation) structurally impossible rather than policed.

### 4.1 Example: Root Cause Report Approval (FNR-SHE-024)

```
SHEIMI: report_approval chain (RACI: CRO -> COO -> CEO -> Board Chair)
  step 1: CRO            approve -> step 2 | reject -> back to author
  step 2: COO            approve -> step 3 | reject -> step 1
  step 3: CEO            approve -> step 4 | reject -> step 2
  step 4: Board Chair    approve -> COMPLETE | reject -> step 3
```

Engine rules:
- `advance(workflow_id, user_id, decision)` checks: is user's role == step.required_role? If no, reject (403). If step already decided, reject (idempotent).
- Approver unavailable: cron checks staleness -> escalate to next authority, log delegation event with timestamp, original assignee, reason code (BRN-005).
- Every transition writes audit_log rows (prev/new state).

### 4.2 Chains Implemented Per Module

| Module | Chain | Steps (RACI) |
|---|---|---|
| SHEIMI | Investigation commissioning | CRO -> CEO |
| SHEIMI | Root cause report | CRO -> COO -> CEO -> Board Chair |
| SHECMV | Risk assessment -> PTW | SHE Officer submit -> SHE Manager approve |
| SHECCM | Resolution plan | SHE Officer -> Line Manager (field) -> SHE Manager |
| SHEIA | EIA report | SHE Manager approve -> EMA submit |
| SHEEPRP | Disaster plan | SHE Champion -> Senior Mgmt (resource adequacy check each step) |
| SHEER | Site Safe for Occupation | SHE Manager + HOD Security (dual sign-off) |
| SHE EC&SC | External comms | SHE Officer draft -> SHE HOD approve -> Corporate Comms |
| SHEAWPM | Annual workplan | SHE Manager -> Committee -> CRO -> COO |
| SHER | Reports | SHE Manager -> CRO -> Chart of Authority |

---

## 5. API Surface (SHE Core REST API)

All endpoints: REST + JSON, TLS 1.2+, rate-limited per consumer, IP allowlist for integrated systems (SEC-SHE-006). Auth: JWT (web) / signed user context (WhatsApp via gateway).

```
POST   /auth/verify-phone            # WhatsApp binding flow
POST   /auth/login                   # web SSO callback / session
GET    /users/me

GET    /risk-register                # filters: category, module, score range, direction, owner
POST   /risk-register                # FNR-SHE-052 (validation: mandatory fields)
POST   /risk-register/{id}/acknowledge   # CRO written acceptance (FNR-SHE-056)
GET    /key-issues                   # incl. overdue auto-escalation list

POST   /vendors                      # + certifications
POST   /ptw                          # gate: risk_assessment_approved must be true
POST   /ptw/{id}/suspend             # on cert lapse (BRN-013)
POST   /ptw/{id}/close               # closure checklist (FNR-SHE-004)

POST   /incidents                    # intake; critical -> sets statutory_deadline = +48h
POST   /incidents/{id}/notify        # dispatch initial notification (SHE Unit + Line Mgr)
POST   /incidents/{id}/investigation # commission team (workflow)
POST   /workflows/{id}/step          # advance approval chain (role-checked)
POST   /incidents/{id}/statutory     # render + submit NSSA/EMA/ZRP, track receipt

POST   /grievances                   # intake (multi-channel, FNR-SHE-007)
POST   /grievances/{id}/resolve      # BRN-010 gate: complainant notified_at required
POST   /grievances/{id}/close        # triggers BRN-003 triple (risk flag + drill + board item)

POST   /webhooks/shec-core           # Hermes -> Core inbound (actions proposed by agent)
POST   /webhooks/hermes              # Core -> Hermes outbound (events for notification)
```

**BRN-003 triple trigger** (grievance closed as residual risk) - one transaction in the Core:
1. `risk_register` row created/flagged for the asset
2. `mock drill` record created in SHEEPRP (targeted drill mandated)
3. Board report draft item queued in SHER
Then a single outbound webhook to Hermes: "grievance G-2026-001 closed as residual risk; risk flagged, drill scheduled, board item queued" for notification dispatch.

---

## 6. Hermes Integration Surface

### 6.1 Hermes Tools (Toolset: `she_platform`)

Hermes gets a restricted toolset that ONLY calls the Core API (no terminal, no file writes, no browser):

| Tool | Purpose | Auth |
|---|---|---|
| `she_intake_incident` | Guided NL intake -> POST /incidents | bound user |
| `she_check_ptw_status` | Read PTW state | bound user |
| `she_create_grievance` | Portal/WhatsApp grievance intake | complainant/external |
| `she_check_risk` | Risk register query | any active user |
| `she_draft_report` | Draft SHER report from metrics | SHE Officer+ |
| `she_escalate_case` | Propose escalation -> Core decides | role-checked |
| `she_lookup_deadline` | Statutory deadlines for a case | any active user |

The agent can *propose* anything; the Core's RBAC + gates are the final word. This is enforced by giving the `she_platform` toolset only these tools per platform config (`platform_toolsets`).

### 6.2 Hermes Cron (Deadline Engine)

| Job | Schedule | Action |
|---|---|---|
| Incident 24h/48h deadline | every 15 min | Query incidents where statutory_deadline approaching + not submitted -> notify SHE Officer + CRO (BRN-002) |
| Certification expiry | daily | 30-day pre-warning to SHE Officer + Procurement; on expiry suspend PTW eligibility (BRN-013) |
| Mock drill year-end | daily | 60-day warning to SHE Manager if no drill recorded (BRN-011) |
| Report overdue | daily | Flag overdue submissions -> SHE Manager + CRO; grace period exceeded -> COO (BRN-012) |
| Key Issues age | daily | Escalate unresolved beyond threshold -> SHE Manager + CRO (Section 3.2) |
| Stakeholder reminders | daily | 7 days before engagement target dates (SE-FR-003); 30d/60d escalation |
| Risk review prompts | daily | Review Date approaching -> prompt responsible owner (FNR-SHE-055) |

Cron jobs call the Core read APIs, decide delivery, and route via WhatsApp/email/in-platform per user preference.

### 6.3 Webhooks

- **Core -> Hermes:** event notifications (PTW expired, grievance escalated, statutory submission due, KPI threshold breach). Hermes drafts the human message and delivers to the right channel.
- **Hermes -> Core:** actions proposed through chat that require Core enforcement (approve step, log observation, create case).

### 6.4 Skills

One skill per module (`sheimi-intake`, `shecmv-ptw`, `sheccm-grievance`, ...) encoding the guided conversation: what to ask, what's mandatory, how to phrase the draft record. Skills keep the agent's behaviour consistent and auditable; they never bypass the Core.

---

## 7. Reconciliation With IMPLEMENTATION_PLAN.md

| Existing Plan | This Design |
|---|---|
| FastAPI + SQLite/PG dual-engine | Kept - it is the SHE Core |
| 10 module SPAs | Kept for the web app |
| Workflow engine as service layer | Formalised: `workflow_instances` + `workflow_steps` state machines |
| Risk Register as write-through cache | Kept, with generated (read-only) score columns |
| External integrations via webhook stubs | Kept; stubs become real in Phase 3 |
| No mobile app (PWA for offline) | **Revised:** WhatsApp covers field intake (NFR-SHE-008) for free - messages queue on the phone. PWA only needed for web users without WhatsApp |
| (absent) | **Added:** Hermes layer - gateway, cron deadline engine, skills, restricted toolset |
| (absent) | **Added:** phone_bindings identity flow, WhatsApp instance separation |
| (absent) | **Added:** Core -> Hermes + Hermes -> Core webhook pair |

**Phase impact:** Phase 0 grows the identity/binding + Hermes gateway setup (WhatsApp Cloud API pairing, toolset lockdown). Phases 1-5 delivery order in IMPLEMENTATION_PLAN.md is unchanged; each module additionally ships its Hermes skill + cron jobs + webhook handlers.

---

## 8. Open Questions / Decisions Needed

1. **CURA + Cassava AI Incident Module** (ESG-FR-007, governance KPIs): is the SHE platform replacing these or integrating with them? Scope impact is significant.
2. **WhatsApp bridge:** WhatsApp Cloud API (official, prod) vs personal Baileys bridge (pilot). Cloud API needs Meta Business verification + message template approval.
3. **Identity provider:** confirm the org's central IdM (Keycloak / Azure AD / other) for SEC-SHE-001 SSO/MFA.
4. **Deployment host:** VPS/cloud provider for 99.5% availability, backups, gateway as service.
5. **Statutory templates:** NSSA/EMA/ZRP report templates and submission channels (portal? email? physical?) - needed for the Statutory Reporting Engine.
6. **Board reporting format:** PDF/Word/Power BI feed - confirm Board infrastructure.

---

## 9. Suggested Next Build Steps

1. Phase 0 spine: schema DDL (Section 3), RBAC, audit log, workflow engine, notifications - pure FastAPI, unit-testable.
2. WhatsApp binding flow: verify-phone endpoint + gateway pairing + `she_platform` toolset lockdown.
3. Pilot slice: SHEIMI intake (WhatsApp NL -> structured incident record) + BRN-002 deadline cron + one approval chain (root cause report). This proves the whole pattern end to end.
4. Then follow IMPLEMENTATION_PLAN.md module order, each module adding skill + cron + webhooks.
