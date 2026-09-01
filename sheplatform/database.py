"""Database layer: dual-engine (PostgreSQL prod / SQLite dev), raw SQL, ThemisIQ pattern.

Guide 5.2: get_db() wrapper, init_db() runs all DDL, get_db_background() for
scheduler jobs. DDL written for PostgreSQL; _to_sqlite_schema() rewrites the
PostgreSQL-specific types when DATABASE_URL is empty.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading

from sheplatform.config import settings

_local = threading.local()

# ---------------------------------------------------------------------------
# Schema DDL (PostgreSQL-first, guide Section 4)
# ---------------------------------------------------------------------------

SCHEMA = [
    # ---- Shared tables (4.1) ----
    """
    CREATE TABLE IF NOT EXISTS organisations (
        id              SERIAL PRIMARY KEY,
        name            TEXT NOT NULL,
        slug            TEXT UNIQUE NOT NULL,
        schema_name     TEXT UNIQUE,
        logo_url        TEXT,
        settings        JSONB DEFAULT '{}',
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS users (
        id              SERIAL PRIMARY KEY,
        email           TEXT UNIQUE NOT NULL,
        password_hash   TEXT NOT NULL,
        first_name      TEXT NOT NULL,
        last_name       TEXT NOT NULL,
        phone           TEXT,
        role_key        TEXT NOT NULL DEFAULT 'employee',
        org_id          INTEGER NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
        is_active       BOOLEAN DEFAULT TRUE,
        must_change_password BOOLEAN DEFAULT FALSE,
        mfa_secret      TEXT,
        mfa_enabled     BOOLEAN DEFAULT FALSE,
        last_login      TIMESTAMPTZ,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id              SERIAL PRIMARY KEY,
        user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
        token_hash      TEXT UNIQUE NOT NULL,
        ip_address      TEXT,
        user_agent      TEXT,
        mfa_verified    BOOLEAN DEFAULT FALSE,
        expires_at      TIMESTAMPTZ NOT NULL,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS mfa_backup_codes (
        id              SERIAL PRIMARY KEY,
        user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
        code_hash       TEXT NOT NULL,
        used            BOOLEAN DEFAULT FALSE,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS login_attempts (
        id              SERIAL PRIMARY KEY,
        identifier      TEXT NOT NULL,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_login_attempts_identifier ON login_attempts(identifier, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id              SERIAL PRIMARY KEY,
        user_id         INTEGER,
        org_id          INTEGER,
        action          TEXT NOT NULL,
        entity_type     TEXT,
        entity_id       INTEGER,
        old_value       JSONB,
        new_value       JSONB,
        ip_address      TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        chain_ts        TEXT,
        prev_hash       TEXT,
        record_hash     TEXT
    )""",
    # NFR-SHE-003: configurable per-record-type minimum retention (years).
    """
    CREATE TABLE IF NOT EXISTS retention_policies (
        id              SERIAL PRIMARY KEY,
        record_type     TEXT UNIQUE NOT NULL,
        retention_years INTEGER NOT NULL,
        description     TEXT,
        updated_by      INTEGER REFERENCES users(id),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS events (
        id              SERIAL PRIMARY KEY,
        event_type      TEXT NOT NULL,
        source_module   TEXT NOT NULL,
        entity_type     TEXT,
        entity_id       INTEGER,
        payload         JSONB DEFAULT '{}',
        processed       BOOLEAN DEFAULT FALSE,
        created_by      INTEGER,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id              SERIAL PRIMARY KEY,
        user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
        title           TEXT NOT NULL,
        body            TEXT,
        link            TEXT,
        read            BOOLEAN DEFAULT FALSE,
        channel         TEXT DEFAULT 'in_app',
        delivery_status TEXT DEFAULT 'pending',
        retry_count     INTEGER DEFAULT 0,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS email_reminders (
        id              SERIAL PRIMARY KEY,
        recipient_email TEXT NOT NULL,
        subject         TEXT NOT NULL,
        body_html       TEXT NOT NULL,
        send_at         TIMESTAMPTZ NOT NULL,
        sent            BOOLEAN DEFAULT FALSE,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS settings (
        key             TEXT PRIMARY KEY,
        value           TEXT,
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    # ---- Incidents (4.2, SHEIMI) ----
    """
    CREATE TABLE IF NOT EXISTS incidents (
        id              SERIAL PRIMARY KEY,
        incident_ref    TEXT UNIQUE NOT NULL,
        idempotency_key TEXT UNIQUE,
        title           TEXT NOT NULL,
        description     TEXT,
        severity        TEXT NOT NULL CHECK (severity IN ('critical','high','medium','low')),
        status          TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','investigation','corrective_action','under_review','closed')),
        incident_type   TEXT CHECK (incident_type IN ('accident','near_miss','environmental','vehicle','medical','fatality')),
        location        TEXT,
        site_id         INTEGER,
        latitude        NUMERIC,
        longitude       NUMERIC,
        occurred_at     TIMESTAMPTZ NOT NULL,
        reported_at     TIMESTAMPTZ DEFAULT NOW(),
        reported_by     INTEGER REFERENCES users(id),
        assigned_to     INTEGER REFERENCES users(id),
        investigation_team JSONB DEFAULT '[]',
        root_cause      TEXT,
        immediate_cause TEXT,
        contributing_factors TEXT,
        nssa_notified   BOOLEAN DEFAULT FALSE,
        nssa_notified_at TIMESTAMPTZ,
        ema_notified    BOOLEAN DEFAULT FALSE,
        ema_notified_at TIMESTAMPTZ,
        zrp_notified    BOOLEAN DEFAULT FALSE,
        zrp_notified_at TIMESTAMPTZ,
        statutory_deadline TIMESTAMPTZ,
        closed_at       TIMESTAMPTZ,
        closed_by       INTEGER REFERENCES users(id),
        org_id          INTEGER REFERENCES organisations(id),
        ai_metadata     JSONB DEFAULT '{}',
        immediate_actions TEXT,
        estimated_cost  NUMERIC,
        witnesses       JSONB DEFAULT '[]',
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_incidents_org ON incidents(org_id)
    """,
    """
    -- B5: incident intake depth. injured_type/body_part/injury_type are free
    -- text (not enums) since real-world descriptions vary too much for a
    -- fixed list to stay useful; org_id is denormalised from the parent
    -- incident so LTIFR/statutory queries never need a join to filter by
    -- tenant (guide golden rule 3: fail closed on tenancy).
    CREATE TABLE IF NOT EXISTS incident_injuries (
        id              SERIAL PRIMARY KEY,
        incident_id     INTEGER REFERENCES incidents(id) ON DELETE CASCADE,
        injured_name    TEXT,
        injured_type    TEXT CHECK (injured_type IN ('employee','contractor','public','other')),
        body_part       TEXT,
        injury_type     TEXT,
        lost_time_days  INTEGER DEFAULT 0,
        medical_treatment TEXT,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_incident_injuries_incident ON incident_injuries(incident_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_incident_injuries_org ON incident_injuries(org_id)
    """,
    """
    -- FTS5 for similar-incident retrieval (A4). In PostgreSQL we use a regular
    -- table populated by triggers; in SQLite dev mode this is rewritten to a
    -- VIRTUAL TABLE below.
    CREATE TABLE IF NOT EXISTS incidents_fts (
        incident_id INTEGER PRIMARY KEY,
        title       TEXT,
        description TEXT,
        incident_type TEXT,
        severity    TEXT,
        content     TEXT
    )""",
    """
    CREATE TABLE IF NOT EXISTS incident_timeline (
        id              SERIAL PRIMARY KEY,
        incident_id     INTEGER REFERENCES incidents(id) ON DELETE CASCADE,
        event_text      TEXT NOT NULL,
        event_type      TEXT DEFAULT 'update',
        occurred_at     TIMESTAMPTZ NOT NULL,
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS corrective_actions (
        id              SERIAL PRIMARY KEY,
        action_ref      TEXT UNIQUE NOT NULL,
        source_type     TEXT NOT NULL CHECK (source_type IN ('incident','audit','inspection','grievance','drill','report')),
        source_id       INTEGER NOT NULL,
        title           TEXT NOT NULL,
        description     TEXT,
        priority        TEXT DEFAULT 'medium' CHECK (priority IN ('critical','high','medium','low')),
        status          TEXT DEFAULT 'open' CHECK (status IN ('open','in_progress','overdue','completed','verified')),
        assigned_to     INTEGER REFERENCES users(id),
        due_date        TIMESTAMPTZ,
        completed_at    TIMESTAMPTZ,
        verified_by     INTEGER REFERENCES users(id),
        verified_at     TIMESTAMPTZ,
        escalated       BOOLEAN DEFAULT FALSE,
        escalated_at    TIMESTAMPTZ,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    # ---- Risk Register (4.3) ----
    """
    CREATE TABLE IF NOT EXISTS risks (
        id              SERIAL PRIMARY KEY,
        risk_ref        TEXT UNIQUE NOT NULL,
        process_function TEXT,
        pcn_owner_function TEXT,
        major_process   TEXT,
        process_owner   TEXT,
        process_objective TEXT,
        hazard_description TEXT NOT NULL,
        risk_impact     TEXT,
        risk_category   TEXT NOT NULL CHECK (risk_category IN ('operational','financial','regulatory','strategic')),
        existing_controls TEXT,
        likelihood      INTEGER NOT NULL CHECK (likelihood BETWEEN 1 AND 5),
        impact          INTEGER NOT NULL CHECK (impact BETWEEN 1 AND 5),
        inherent_score  INTEGER GENERATED ALWAYS AS (likelihood * impact) STORED,
        control_effectiveness INTEGER DEFAULT 1 CHECK (control_effectiveness BETWEEN 1 AND 5),
        residual_score  NUMERIC GENERATED ALWAYS AS (
            CAST(likelihood * impact AS NUMERIC) / NULLIF(control_effectiveness, 0)
        ) STORED,
        managerial_response TEXT CHECK (managerial_response IN ('accept','mitigate','transfer','avoid')),
        risk_direction  TEXT DEFAULT 'stable' CHECK (risk_direction IN ('increasing','stable','decreasing')),
        status          TEXT DEFAULT 'open' CHECK (status IN ('open','under_review','monitoring','mitigated')),
        responsible_business TEXT,
        responsible_risk TEXT,
        review_date     TIMESTAMPTZ,
        status_update   TEXT,
        source_reference TEXT,
        source_date     TIMESTAMPTZ,
        statutory_instrument TEXT,
        is_compliant    BOOLEAN,
        origin_module   TEXT,
        source_type     TEXT,
        source_id       INTEGER,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_risks_category ON risks(risk_category)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_risks_status ON risks(status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_risks_source ON risks(source_type, source_id)
    """,
    # ---- ThemisIQ sync tables (SHE_THEMISIQ_INTEGRATION.md Section 11) ----
    """
    CREATE TABLE IF NOT EXISTS risk_sync_map (
        id              SERIAL PRIMARY KEY,
        she_risk_id     INTEGER REFERENCES risks(id) ON DELETE CASCADE,
        themis_risk_id  INTEGER NOT NULL,
        last_sync_hash  TEXT,
        last_synced_at  TIMESTAMPTZ,
        sync_error      TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_risk_sync_she ON risk_sync_map(she_risk_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS themis_sync_queue (
        id              SERIAL PRIMARY KEY,
        she_risk_id     INTEGER REFERENCES risks(id) ON DELETE CASCADE,
        operation       TEXT NOT NULL,
        attempts        INTEGER DEFAULT 0,
        last_error      TEXT,
        next_retry_at   TIMESTAMPTZ,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    # ---- Approval chains (guide 5.6, BRN-SHE-005) ----
    """
    CREATE TABLE IF NOT EXISTS approval_chains (
        id              SERIAL PRIMARY KEY,
        entity_type     TEXT NOT NULL,
        entity_id       INTEGER NOT NULL,
        status          TEXT DEFAULT 'active' CHECK (status IN ('active','completed','rejected')),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_approval_chains_entity ON approval_chains(entity_type, entity_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS approval_chain_steps (
        id              SERIAL PRIMARY KEY,
        chain_id        INTEGER REFERENCES approval_chains(id) ON DELETE CASCADE,
        step_order      INTEGER NOT NULL,
        role_required   TEXT NOT NULL,
        sla_hours       INTEGER DEFAULT 48,
        sla_deadline    TIMESTAMPTZ,
        status          TEXT DEFAULT 'waiting' CHECK (status IN ('waiting','pending','approved','rejected','escalated')),
        decided_by      INTEGER REFERENCES users(id),
        decided_at      TIMESTAMPTZ,
        comments        TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_approval_steps_chain ON approval_chain_steps(chain_id, step_order)
    """,
    # ---- Outbound webhooks (guide 24 pattern) ----
    """
    CREATE TABLE IF NOT EXISTS webhooks (
        id              SERIAL PRIMARY KEY,
        url             TEXT NOT NULL,
        secret          TEXT NOT NULL,
        event_type      TEXT NOT NULL,
        active          BOOLEAN DEFAULT TRUE,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS webhook_logs (
        id              SERIAL PRIMARY KEY,
        url             TEXT NOT NULL,
        payload         JSONB DEFAULT '{}',
        status_code     INTEGER,
        success         BOOLEAN DEFAULT FALSE,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    # ---- Canonical sites (created before operational site relationships) ----
    """
    CREATE TABLE IF NOT EXISTS sites (
        id              SERIAL PRIMARY KEY,
        site_code       TEXT UNIQUE NOT NULL,
        site_name       TEXT NOT NULL,
        city            TEXT,
        region          TEXT,
        site_type       TEXT DEFAULT 'facility' CHECK (site_type IN ('facility','tower','retail','warehouse','office')),
        status          TEXT DEFAULT 'active' CHECK (status IN ('active','inactive')),
        latitude        NUMERIC,
        longitude       NUMERIC,
        coordinate_source TEXT CHECK (coordinate_source IN ('manual','device_gps','imported','geocoder')),
        coordinate_accuracy_m NUMERIC,
        coordinates_updated_at TIMESTAMPTZ,
        coordinates_updated_by INTEGER REFERENCES users(id),
        geocode_provider TEXT,
        geocode_place_id TEXT,
        org_id          INTEGER REFERENCES organisations(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    # ---- Vendor Compliance (4.4, SHECMV) ----
    """
    CREATE TABLE IF NOT EXISTS vendors (
        id              SERIAL PRIMARY KEY,
        vendor_ref      TEXT UNIQUE NOT NULL,
        company_name    TEXT NOT NULL,
        contact_person  TEXT,
        email           TEXT,
        phone           TEXT,
        insurance_expiry TIMESTAMPTZ,
        insurance_document TEXT,
        risk_profile    TEXT DEFAULT 'medium' CHECK (risk_profile IN ('high','medium','low')),
        ptw_eligible    BOOLEAN DEFAULT TRUE,
        certification_status TEXT DEFAULT 'valid' CHECK (certification_status IN ('valid','expiring','expired','suspended')),
        status          TEXT DEFAULT 'active' CHECK (status IN ('active','suspended','offboarded')),
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS vendor_certifications (
        id              SERIAL PRIMARY KEY,
        vendor_id       INTEGER REFERENCES vendors(id) ON DELETE CASCADE,
        cert_name       TEXT NOT NULL,
        cert_number     TEXT,
        issued_date     TIMESTAMPTZ,
        expiry_date     TIMESTAMPTZ NOT NULL,
        document_path   TEXT,
        status          TEXT DEFAULT 'valid' CHECK (status IN ('valid','expiring','expired')),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS contractor_inductions (
        id              SERIAL PRIMARY KEY,
        vendor_id       INTEGER REFERENCES vendors(id) ON DELETE CASCADE,
        site_id         INTEGER REFERENCES sites(id),
        induction_date  TIMESTAMPTZ,
        induction_type  TEXT CHECK (induction_type IN ('general','site_specific','high_risk','refresher')),
        valid_until     TIMESTAMPTZ,
        trainer_id      INTEGER REFERENCES users(id),
        status          TEXT DEFAULT 'pending' CHECK (status IN ('pending','valid','expired')),
        notes           TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_vendor_cert_expiry ON vendor_certifications(expiry_date)
    """,
    """
    CREATE TABLE IF NOT EXISTS risk_assessments (
        id              SERIAL PRIMARY KEY,
        vendor_id       INTEGER REFERENCES vendors(id) ON DELETE CASCADE,
        assessment_ref  TEXT UNIQUE NOT NULL,
        scope_of_work   TEXT NOT NULL,
        workforce_size  INTEGER,
        equipment_list  TEXT,
        site_conditions TEXT,
        risk_rating     TEXT CHECK (risk_rating IN ('high','medium','low')),
        status          TEXT DEFAULT 'draft' CHECK (status IN ('draft','submitted','approved','rejected')),
        approved_by     INTEGER REFERENCES users(id),
        approved_at     TIMESTAMPTZ,
        rejection_reason TEXT,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    # ---- Permit to Work (4.5) ----
    """
    CREATE TABLE IF NOT EXISTS permits (
        id              SERIAL PRIMARY KEY,
        permit_ref      TEXT UNIQUE NOT NULL,
        permit_type     TEXT NOT NULL CHECK (permit_type IN ('hot_work','confined_space','electrical','height','excavation','general')),
        title           TEXT NOT NULL,
        description     TEXT,
        vendor_id       INTEGER REFERENCES vendors(id),
        risk_assessment_id INTEGER REFERENCES risk_assessments(id),
        site_location   TEXT,
        site_id         INTEGER REFERENCES sites(id),
        scope_boundary  TEXT,
        valid_from      TIMESTAMPTZ,
        valid_until     TIMESTAMPTZ,
        status          TEXT DEFAULT 'draft' CHECK (status IN ('draft','pending_approval','approved','active','expired','revoked','closed')),
        she_officer_id  INTEGER REFERENCES users(id),
        closure_checklist JSONB,
        site_restored   BOOLEAN DEFAULT FALSE,
        closed_at       TIMESTAMPTZ,
        closed_by       INTEGER REFERENCES users(id),
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_permits_status ON permits(status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_permits_vendor ON permits(vendor_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_permits_expiry ON permits(valid_until)
    """,
    """
    CREATE TABLE IF NOT EXISTS permit_approvals (
        id              SERIAL PRIMARY KEY,
        permit_id       INTEGER REFERENCES permits(id) ON DELETE CASCADE,
        step_order      INTEGER NOT NULL,
        role_required   TEXT NOT NULL,
        approver_id     INTEGER REFERENCES users(id),
        status          TEXT DEFAULT 'waiting' CHECK (status IN ('waiting','pending','approved','rejected')),
        decision_at     TIMESTAMPTZ,
        comments        TEXT,
        sla_deadline    TIMESTAMPTZ,
        escalated       BOOLEAN DEFAULT FALSE,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_permit_approvals ON permit_approvals(permit_id, step_order)
    """,
    # ---- Community Complaints (4.6, SHECCM) ----
    """
    CREATE TABLE IF NOT EXISTS grievances (
        id              SERIAL PRIMARY KEY,
        case_ref        TEXT UNIQUE NOT NULL,
        complainant_name TEXT,
        complainant_contact TEXT,
        source_channel  TEXT CHECK (source_channel IN ('portal','phone','email','walk_in','media','regulator','ngo')),
        description     TEXT NOT NULL,
        classification  TEXT,
        severity        TEXT DEFAULT 'medium' CHECK (severity IN ('critical','high','medium','low')),
        status          TEXT DEFAULT 'open' CHECK (status IN ('open','investigating','negotiation','resolved','residual_risk','closed')),
        investigating_officer INTEGER REFERENCES users(id),
        business_impact TEXT,
        community_impact TEXT,
        resolution_plan TEXT,
        resolution_outcome TEXT,
        complainant_notified BOOLEAN DEFAULT FALSE,
        complainant_notified_at TIMESTAMPTZ,
        notified_by      INTEGER REFERENCES users(id),
        notification_method TEXT,
        is_residual_risk BOOLEAN DEFAULT FALSE,
        asset_id        TEXT,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS grievance_actions (
        id              SERIAL PRIMARY KEY,
        grievance_id    INTEGER REFERENCES grievances(id) ON DELETE CASCADE,
        action_text     TEXT NOT NULL,
        assigned_to     INTEGER REFERENCES users(id),
        status          TEXT DEFAULT 'open' CHECK (status IN ('open','in_progress','resolved')),
        due_date        TIMESTAMPTZ,
        completed_at    TIMESTAMPTZ,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS grievance_engagement (
        id              SERIAL PRIMARY KEY,
        grievance_id    INTEGER REFERENCES grievances(id) ON DELETE CASCADE,
        engagement_type TEXT,
        participants    TEXT,
        notes           TEXT,
        outcome         TEXT,
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    # ---- EIA (4.7, SHEIA) ----
    """
    CREATE TABLE IF NOT EXISTS eia_projects (
        id              SERIAL PRIMARY KEY,
        project_ref     TEXT UNIQUE NOT NULL,
        project_name    TEXT NOT NULL,
        department      TEXT,
        project_type    TEXT,
        location        TEXT,
        site_id         INTEGER REFERENCES sites(id),
        eia_required    BOOLEAN,
        screening_completed BOOLEAN DEFAULT FALSE,
        screening_result TEXT CHECK (screening_result IN ('required','not_required','pending')),
        status          TEXT DEFAULT 'screening' CHECK (status IN (
            'screening','prospectus','consultant_sourcing','assessment','review',
            'submitted_ema','approved','rejected','monitoring','closed'
        )),
        prospectus_path TEXT,
        consultant_id   INTEGER,
        eia_report_path TEXT,
        ema_submission_date TIMESTAMPTZ,
        ema_submission_ref TEXT,
        ema_decision    TEXT CHECK (ema_decision IN ('approved','rejected','conditional','pending')),
        ema_decision_date TIMESTAMPTZ,
        blocked         BOOLEAN DEFAULT FALSE,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS eia_consultants (
        id              SERIAL PRIMARY KEY,
        name            TEXT NOT NULL,
        company         TEXT,
        ema_accreditation_number TEXT,
        ema_accreditation_verified BOOLEAN DEFAULT FALSE,
        accreditation_verified_by INTEGER REFERENCES users(id),
        accreditation_verified_at TIMESTAMPTZ,
        procurement_ref TEXT,
        status          TEXT DEFAULT 'pending' CHECK (status IN ('pending','verified','rejected','active','inactive')),
        org_id          INTEGER REFERENCES organisations(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS eia_observations (
        id              SERIAL PRIMARY KEY,
        project_id      INTEGER REFERENCES eia_projects(id) ON DELETE CASCADE,
        observation     TEXT NOT NULL,
        status          TEXT DEFAULT 'open' CHECK (status IN ('open','resolved','closed')),
        logged_by       INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    # ---- Emergency (4.8, SHEEPRP + SHEER) ----
    """
    CREATE TABLE IF NOT EXISTS emergency_plans (
        id              SERIAL PRIMARY KEY,
        plan_ref        TEXT UNIQUE NOT NULL,
        title           TEXT NOT NULL,
        version         INTEGER DEFAULT 1,
        status          TEXT DEFAULT 'draft' CHECK (status IN ('draft','review','approved','active','superseded')),
        human_resources JSONB DEFAULT '{}',
        budget          NUMERIC,
        equipment       JSONB DEFAULT '[]',
        facilities      JSONB DEFAULT '[]',
        approved_by     INTEGER REFERENCES users(id),
        approved_at     TIMESTAMPTZ,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS emergency_teams (
        id              SERIAL PRIMARY KEY,
        plan_id         INTEGER REFERENCES emergency_plans(id) ON DELETE CASCADE,
        team_type       TEXT NOT NULL CHECK (team_type IN ('imt','ecmt')),
        member_name     TEXT NOT NULL,
        role            TEXT NOT NULL,
        contact_phone   TEXT,
        contact_email   TEXT,
        user_id         INTEGER REFERENCES users(id),
        appointed_at    TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS mock_drills (
        id              SERIAL PRIMARY KEY,
        drill_ref       TEXT UNIQUE NOT NULL,
        plan_id         INTEGER REFERENCES emergency_plans(id),
        drill_type      TEXT NOT NULL,
        scheduled_date  TIMESTAMPTZ NOT NULL,
        actual_date     TIMESTAMPTZ,
        status          TEXT DEFAULT 'scheduled' CHECK (status IN ('scheduled','in_progress','completed','cancelled')),
        participants    JSONB DEFAULT '[]',
        observations    TEXT,
        feedback        TEXT,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS drill_improvements (
        id              SERIAL PRIMARY KEY,
        drill_id        INTEGER REFERENCES mock_drills(id) ON DELETE CASCADE,
        description     TEXT NOT NULL,
        status          TEXT DEFAULT 'open' CHECK (status IN ('open','in_progress','completed')),
        assigned_to     INTEGER REFERENCES users(id),
        completed_at    TIMESTAMPTZ,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS emergency_events (
        id              SERIAL PRIMARY KEY,
        event_ref       TEXT UNIQUE NOT NULL,
        title           TEXT NOT NULL,
        description     TEXT,
        severity        TEXT NOT NULL CHECK (severity IN ('critical','high','medium','low')),
        status          TEXT DEFAULT 'active' CHECK (status IN ('active','contained','post_crisis','closed')),
        site_location   TEXT,
        site_id         INTEGER REFERENCES sites(id),
        strategic_direction TEXT,
        directing_authority INTEGER REFERENCES users(id),
        site_safe_certificate BOOLEAN DEFAULT FALSE,
        site_certified_by JSONB,
        relocation_required BOOLEAN DEFAULT FALSE,
        relocation_status TEXT,
        root_cause      TEXT,
        nssa_reported   BOOLEAN DEFAULT FALSE,
        ema_reported    BOOLEAN DEFAULT FALSE,
        zrp_reported    BOOLEAN DEFAULT FALSE,
        insurance_reported BOOLEAN DEFAULT FALSE,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    # ---- Training (4.9, SHET&A) ----
    """
    CREATE TABLE IF NOT EXISTS training_needs (
        id              SERIAL PRIMARY KEY,
        need_ref        TEXT UNIQUE NOT NULL,
        title           TEXT NOT NULL,
        description     TEXT,
        source_trigger  TEXT CHECK (source_trigger IN ('audit','incident','change_management','training_plan','gap_analysis')),
        source_id       INTEGER,
        delivery_method TEXT CHECK (delivery_method IN ('internal','outsourced','pending')),
        procurement_ref TEXT,
        status          TEXT DEFAULT 'identified' CHECK (status IN ('identified','scheduled','in_progress','completed','cancelled')),
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS training_sessions (
        id              SERIAL PRIMARY KEY,
        session_ref     TEXT UNIQUE NOT NULL,
        need_id         INTEGER REFERENCES training_needs(id),
        title           TEXT NOT NULL,
        trainer         TEXT,
        external_agency_id INTEGER,
        scheduled_date  TIMESTAMPTZ NOT NULL,
        location        TEXT,
        status          TEXT DEFAULT 'scheduled' CHECK (status IN ('scheduled','in_progress','completed','cancelled')),
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS training_attendance (
        id              SERIAL PRIMARY KEY,
        session_id      INTEGER REFERENCES training_sessions(id) ON DELETE CASCADE,
        user_id         INTEGER REFERENCES users(id),
        attended        BOOLEAN DEFAULT FALSE,
        competency_score NUMERIC,
        evaluation      TEXT,
        certificate_path TEXT,
        refresher_due   TIMESTAMPTZ,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_training_refresher ON training_attendance(refresher_due)
    """,
    """
    CREATE TABLE IF NOT EXISTS competency_matrix (
        id              SERIAL PRIMARY KEY,
        user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
        competency_name TEXT NOT NULL,
        level           TEXT CHECK (level IN ('basic','intermediate','advanced','expert')),
        certified       BOOLEAN DEFAULT FALSE,
        expiry_date     TIMESTAMPTZ,
        source_session_id INTEGER REFERENCES training_sessions(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    # ---- Reporting (4.10, SHER) ----
    """
    CREATE TABLE IF NOT EXISTS she_reports (
        id              SERIAL PRIMARY KEY,
        report_ref      TEXT UNIQUE NOT NULL,
        report_type     TEXT NOT NULL CHECK (report_type IN ('weekly_operational','monthly_management','project','board','nssa','ema','annual_sustainability')),
        title           TEXT NOT NULL,
        period_start    TIMESTAMPTZ,
        period_end      TIMESTAMPTZ,
        status          TEXT DEFAULT 'draft' CHECK (status IN ('draft','review','approved','rejected','submitted','overdue')),
        content         JSONB DEFAULT '{}',
        document_path   TEXT,
        submission_deadline TIMESTAMPTZ,
        submitted_at    TIMESTAMPTZ,
        external_ref    TEXT,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS report_approvals (
        id              SERIAL PRIMARY KEY,
        report_id       INTEGER REFERENCES she_reports(id) ON DELETE CASCADE,
        step_order      INTEGER NOT NULL,
        role_required   TEXT NOT NULL,
        approver_id     INTEGER REFERENCES users(id),
        status          TEXT DEFAULT 'waiting' CHECK (status IN ('waiting','pending','approved','rejected')),
        comments        TEXT,
        decision_at     TIMESTAMPTZ,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS report_action_items (
        id              SERIAL PRIMARY KEY,
        report_id       INTEGER REFERENCES she_reports(id) ON DELETE CASCADE,
        action_text     TEXT NOT NULL,
        assigned_to     INTEGER REFERENCES users(id),
        due_date        TIMESTAMPTZ,
        status          TEXT DEFAULT 'open' CHECK (status IN ('open','in_progress','completed')),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS key_issues (
        id              SERIAL PRIMARY KEY,
        title           TEXT NOT NULL,
        description     TEXT,
        severity        TEXT DEFAULT 'medium',
        status          TEXT DEFAULT 'open' CHECK (status IN ('open','in_progress','resolved','escalated')),
        age_days        INTEGER DEFAULT 0,
        escalation_threshold INTEGER DEFAULT 30,
        escalated       BOOLEAN DEFAULT FALSE,
        source_report_id INTEGER REFERENCES she_reports(id),
        assigned_to     INTEGER REFERENCES users(id),
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS statutory_report_templates (
        id              SERIAL PRIMARY KEY,
        template_key    TEXT UNIQUE NOT NULL,
        authority       TEXT NOT NULL CHECK (authority IN ('nssa','ema','zrp','labour','custom')),
        title           TEXT NOT NULL,
        description     TEXT,
        period_type     TEXT NOT NULL CHECK (period_type IN ('monthly','quarterly','annual','incident')),
        fields          JSONB DEFAULT '[]' NOT NULL,
        default_content JSONB DEFAULT '{}',
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS statutory_reports (
        id              SERIAL PRIMARY KEY,
        report_ref      TEXT UNIQUE NOT NULL,
        template_key    TEXT NOT NULL REFERENCES statutory_report_templates(template_key),
        authority       TEXT NOT NULL CHECK (authority IN ('nssa','ema','zrp','labour','custom')),
        title           TEXT NOT NULL,
        period_start    TIMESTAMPTZ,
        period_end      TIMESTAMPTZ,
        status          TEXT DEFAULT 'draft' CHECK (status IN ('draft','locked','submitted','acknowledged','overdue','rejected')),
        data            JSONB DEFAULT '{}',
        rendered_text   TEXT,
        submitted_at    TIMESTAMPTZ,
        submitted_by    INTEGER REFERENCES users(id),
        external_ref    TEXT,
        lock_version    INTEGER DEFAULT 1,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_statutory_reports_org ON statutory_reports(org_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_statutory_reports_status ON statutory_reports(status, period_end)
    """,
    """
    CREATE TABLE IF NOT EXISTS statutory_report_submissions (
        id              SERIAL PRIMARY KEY,
        report_id       INTEGER NOT NULL REFERENCES statutory_reports(id) ON DELETE CASCADE,
        channel         TEXT NOT NULL CHECK (channel IN ('email','portal','manual','api')),
        recipient       TEXT,
        tracking_ref    TEXT,
        status          TEXT DEFAULT 'pending' CHECK (status IN ('pending','delivered','acknowledged','failed')),
        payload         JSONB DEFAULT '{}',
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS statutory_report_audit (
        id              SERIAL PRIMARY KEY,
        report_id       INTEGER NOT NULL REFERENCES statutory_reports(id) ON DELETE CASCADE,
        action          TEXT NOT NULL,
        actor_id        INTEGER REFERENCES users(id),
        old_data        JSONB,
        new_data        JSONB,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    # ---- ESG KPI (4.11) ----
    """
    CREATE TABLE IF NOT EXISTS esg_kpis (
        id              SERIAL PRIMARY KEY,
        kpi_code        TEXT UNIQUE NOT NULL,
        category        TEXT NOT NULL CHECK (category IN ('environmental','social','governance')),
        subcategory     TEXT,
        name            TEXT NOT NULL,
        unit            TEXT NOT NULL,
        frequency       TEXT DEFAULT 'monthly' CHECK (frequency IN ('monthly','quarterly','annual')),
        responsible_unit TEXT,
        data_source     TEXT,
        target_fy26     NUMERIC,
        target_fy27     NUMERIC,
        threshold_type  TEXT DEFAULT 'max' CHECK (threshold_type IN ('max','min','exact','range')),
        alert_threshold NUMERIC,
        sdg_mapping     TEXT,
        is_active       BOOLEAN DEFAULT TRUE,
        org_id          INTEGER REFERENCES organisations(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS esg_kpi_entries (
        id              SERIAL PRIMARY KEY,
        kpi_id          INTEGER REFERENCES esg_kpis(id) ON DELETE CASCADE,
        period          TEXT NOT NULL,
        actual_value    NUMERIC,
        target_value    NUMERIC,
        variance        NUMERIC,
        rag_status      TEXT CHECK (rag_status IN ('red','amber','green')),
        notes           TEXT,
        validated       BOOLEAN DEFAULT FALSE,
        validated_by    INTEGER REFERENCES users(id),
        linked_incident_id INTEGER,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_esg_entries ON esg_kpi_entries(kpi_id, period)
    """,
    """
    CREATE TABLE IF NOT EXISTS esg_csv_mappings (
        id              SERIAL PRIMARY KEY,
        mapping_name    TEXT NOT NULL,
        kpi_id          INTEGER NOT NULL REFERENCES esg_kpis(id) ON DELETE CASCADE,
        source_column   TEXT NOT NULL,
        transform       TEXT DEFAULT 'value',
        is_active       BOOLEAN DEFAULT TRUE,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(mapping_name, source_column)
    )""",
    """
    CREATE TABLE IF NOT EXISTS esg_csv_uploads (
        id              SERIAL PRIMARY KEY,
        file_name       TEXT NOT NULL,
        mapping_name    TEXT,
        status          TEXT DEFAULT 'pending' CHECK (status IN ('pending','reconciled','committed','failed')),
        rows_total      INTEGER DEFAULT 0,
        rows_valid      INTEGER DEFAULT 0,
        rows_anomalous  INTEGER DEFAULT 0,
        rows_duplicate  INTEGER DEFAULT 0,
        committed_at    TIMESTAMPTZ,
        committed_by    INTEGER REFERENCES users(id),
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS esg_csv_rows (
        id              SERIAL PRIMARY KEY,
        upload_id       INTEGER NOT NULL REFERENCES esg_csv_uploads(id) ON DELETE CASCADE,
        row_number      INTEGER NOT NULL,
        period          TEXT,
        source_column   TEXT,
        raw_value       TEXT,
        mapped_kpi_id   INTEGER REFERENCES esg_kpis(id),
        actual_value    NUMERIC,
        status          TEXT DEFAULT 'pending' CHECK (status IN ('pending','valid','anomalous','duplicate','committed')),
        anomaly_reason  TEXT,
        committed_entry_id INTEGER REFERENCES esg_kpi_entries(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_csv_rows_upload ON esg_csv_rows(upload_id, status)
    """,
    """
    CREATE TABLE IF NOT EXISTS esg_api_keys (
        id              SERIAL PRIMARY KEY,
        name            TEXT NOT NULL,
        key_hash        TEXT UNIQUE NOT NULL,
        scopes          JSONB DEFAULT '["esg.ingest"]',
        is_active       BOOLEAN DEFAULT TRUE,
        last_used_at    TIMESTAMPTZ,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    # ---- Asset register + telemetry (guide C4) ----
    """
    CREATE TABLE IF NOT EXISTS assets (
        id                      SERIAL PRIMARY KEY,
        asset_ref               TEXT UNIQUE NOT NULL,
        name                    TEXT NOT NULL,
        asset_type              TEXT NOT NULL CHECK (asset_type IN ('generator','vehicle','tower_equipment','other')),
        site_id                 INTEGER REFERENCES sites(id),
        install_date            TIMESTAMPTZ,
        service_interval_hours  NUMERIC,
        total_run_hours         NUMERIC DEFAULT 0,
        hours_at_last_service   NUMERIC DEFAULT 0,
        last_serviced_at        TIMESTAMPTZ,
        esg_kpi_code            TEXT,
        status                  TEXT DEFAULT 'active' CHECK (status IN ('active','maintenance','decommissioned')),
        org_id                  INTEGER REFERENCES organisations(id),
        created_by              INTEGER REFERENCES users(id),
        created_at              TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_assets_org ON assets(org_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_readings (
        id              SERIAL PRIMARY KEY,
        asset_id        INTEGER REFERENCES assets(id) ON DELETE CASCADE,
        run_hours       NUMERIC,
        fuel_level_pct  NUMERIC,
        recorded_at     TIMESTAMPTZ NOT NULL,
        is_anomaly      BOOLEAN DEFAULT FALSE,
        anomaly_reason  TEXT,
        org_id          INTEGER REFERENCES organisations(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_asset_readings_asset ON asset_readings(asset_id, recorded_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_maintenance_tasks (
        id              SERIAL PRIMARY KEY,
        asset_id        INTEGER REFERENCES assets(id) ON DELETE CASCADE,
        title           TEXT NOT NULL,
        reason          TEXT,
        status          TEXT DEFAULT 'open' CHECK (status IN ('open','completed')),
        completed_at    TIMESTAMPTZ,
        completed_by    INTEGER REFERENCES users(id),
        org_id          INTEGER REFERENCES organisations(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_asset_maintenance_asset ON asset_maintenance_tasks(asset_id, status)
    """,
    """
    CREATE TABLE IF NOT EXISTS asset_api_keys (
        id              SERIAL PRIMARY KEY,
        name            TEXT NOT NULL,
        key_hash        TEXT UNIQUE NOT NULL,
        scopes          JSONB DEFAULT '["assets.telemetry"]',
        is_active       BOOLEAN DEFAULT TRUE,
        last_used_at    TIMESTAMPTZ,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    # ---- Stakeholder (4.12) ----
    """
    CREATE TABLE IF NOT EXISTS stakeholders (
        id              SERIAL PRIMARY KEY,
        name            TEXT NOT NULL,
        category        TEXT CHECK (category IN ('regulator','community','emergency_authority','consultant','government','business_association','academic','other')),
        contact_person  TEXT,
        phone           TEXT,
        email           TEXT,
        engagement_method TEXT,
        org_id          INTEGER REFERENCES organisations(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS stakeholder_engagements (
        id              SERIAL PRIMARY KEY,
        engagement_ref  TEXT UNIQUE NOT NULL,
        stakeholder_id  INTEGER REFERENCES stakeholders(id) ON DELETE CASCADE,
        engagement_issue TEXT NOT NULL,
        she_objectives  TEXT,
        stakeholder_objectives TEXT,
        risk_description TEXT,
        risk_mitigation TEXT,
        target_date     TIMESTAMPTZ,
        frequency       TEXT,
        responsible_person INTEGER REFERENCES users(id),
        current_position TEXT,
        status          TEXT DEFAULT 'active' CHECK (status IN ('active','completed','escalated','overdue')),
        linked_module   TEXT,
        linked_grievance_id INTEGER,
        q1_feedback     TEXT,
        q2_feedback     TEXT,
        q3_feedback     TEXT,
        q4_feedback     TEXT,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    # ---- Evidence vault (4.13) ----
    """
    CREATE TABLE IF NOT EXISTS evidence (
        id              SERIAL PRIMARY KEY,
        file_name       TEXT NOT NULL,
        original_name   TEXT NOT NULL,
        file_path       TEXT NOT NULL,
        file_size       INTEGER,
        mime_type       TEXT,
        file_hash       TEXT,
        entity_type     TEXT NOT NULL,
        entity_id       INTEGER NOT NULL,
        tags            JSONB DEFAULT '[]',
        expiry_date     TIMESTAMPTZ,
        org_id          INTEGER REFERENCES organisations(id),
        uploaded_by     INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_evidence_entity ON evidence(entity_type, entity_id)
    """,
    # ---- Workplan + external comms + inspections (4.14) ----
    """
    CREATE TABLE IF NOT EXISTS annual_workplan (
        id              SERIAL PRIMARY KEY,
        plan_ref        TEXT UNIQUE NOT NULL,
        fiscal_year     TEXT NOT NULL,
        status          TEXT DEFAULT 'draft' CHECK (status IN ('draft','committee_review','approved','active','closed')),
        preventive_pct  NUMERIC,
        approved_by     INTEGER REFERENCES users(id),
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS workplan_tasks (
        id              SERIAL PRIMARY KEY,
        workplan_id     INTEGER REFERENCES annual_workplan(id) ON DELETE CASCADE,
        title           TEXT NOT NULL,
        control_type    TEXT NOT NULL CHECK (control_type IN ('preventive','detective')),
        cost_estimate   NUMERIC,
        milestone_date  TIMESTAMPTZ,
        status          TEXT DEFAULT 'pending' CHECK (status IN ('pending','in_progress','completed','adjusted')),
        assigned_to     INTEGER REFERENCES users(id),
        adjustment_reason TEXT,
        adjustment_approved_by INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS external_communications (
        id              SERIAL PRIMARY KEY,
        comms_ref       TEXT UNIQUE NOT NULL,
        concern_id      INTEGER,
        concern_description TEXT NOT NULL,
        awareness_plan  TEXT,
        target_segment  TEXT,
        communication_brief TEXT,
        medium          TEXT CHECK (medium IN ('digital','email','workshop','face_to_face','letter')),
        frequency       TEXT,
        status          TEXT DEFAULT 'draft' CHECK (status IN ('draft','hod_review','approved','dispatched','effectiveness_review','closed')),
        hod_approved    BOOLEAN DEFAULT FALSE,
        hod_approved_by INTEGER REFERENCES users(id),
        hod_approved_at TIMESTAMPTZ,
        hod_comments    TEXT,
        dispatched_at   TIMESTAMPTZ,
        dispatch_confirmation BOOLEAN DEFAULT FALSE,
        effectiveness_assessment TEXT,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS inspections (
        id              SERIAL PRIMARY KEY,
        inspection_ref  TEXT UNIQUE NOT NULL,
        title           TEXT NOT NULL,
        inspection_type TEXT,
        site_location   TEXT,
        site_id         INTEGER REFERENCES sites(id),
        scheduled_date  TIMESTAMPTZ,
        completed_date  TIMESTAMPTZ,
        status          TEXT DEFAULT 'scheduled' CHECK (status IN ('scheduled','in_progress','completed','overdue')),
        findings        TEXT,
        inspector_id    INTEGER REFERENCES users(id),
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS inspection_checklists (
        id              SERIAL PRIMARY KEY,
        name            TEXT NOT NULL,
        inspection_type TEXT,
        item            TEXT NOT NULL,
        sort_order      INTEGER DEFAULT 0,
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS inspection_results (
        id              SERIAL PRIMARY KEY,
        inspection_id   INTEGER REFERENCES inspections(id) ON DELETE CASCADE,
        checklist_item  TEXT NOT NULL,
        result          TEXT NOT NULL CHECK (result IN ('pass','fail','na')),
        comment         TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS map_usage_metrics (
        id              SERIAL PRIMARY KEY,
        event_type      TEXT NOT NULL CHECK (event_type IN ('map_session','layer_request','coordinate_save','coordinate_clear','provider_failure','import_preview','import_commit')),
        layer_name      TEXT CHECK (layer_name IN ('incidents','sites')),
        feature_count   INTEGER DEFAULT 0,
        unlocated_count INTEGER,
        duration_ms     NUMERIC,
        truncated       BOOLEAN DEFAULT FALSE,
        coordinate_source TEXT CHECK (coordinate_source IN ('manual','device_gps','imported','geocoder')),
        org_id          INTEGER NOT NULL REFERENCES organisations(id),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_map_usage_metrics_org_created
    ON map_usage_metrics(org_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_map_usage_metrics_event
    ON map_usage_metrics(event_type, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS map_provider_monthly_usage (
        provider            TEXT NOT NULL,
        billing_month_utc   TEXT NOT NULL,
        admitted_loads      INTEGER NOT NULL DEFAULT 0 CHECK (admitted_loads >= 0),
        warning_recorded_at TIMESTAMPTZ,
        critical_recorded_at TIMESTAMPTZ,
        blocked_recorded_at TIMESTAMPTZ,
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (provider, billing_month_utc)
    )""",
    """
    CREATE TABLE IF NOT EXISTS map_provider_admissions (
        admission_id        TEXT PRIMARY KEY,
        provider            TEXT NOT NULL,
        billing_month_utc   TEXT NOT NULL,
        org_id              INTEGER NOT NULL REFERENCES organisations(id),
        decision            TEXT NOT NULL CHECK (decision IN ('admitted','denied')),
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_map_provider_admissions_month
    ON map_provider_admissions(provider, billing_month_utc, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS site_coordinate_imports (
        id                  SERIAL PRIMARY KEY,
        batch_ref           TEXT UNIQUE NOT NULL,
        status              TEXT NOT NULL DEFAULT 'previewed' CHECK (status IN ('previewed','committed','cancelled')),
        total_rows          INTEGER NOT NULL DEFAULT 0,
        valid_rows          INTEGER NOT NULL DEFAULT 0,
        invalid_rows        INTEGER NOT NULL DEFAULT 0,
        conflict_rows       INTEGER NOT NULL DEFAULT 0,
        overwrite_approved  BOOLEAN DEFAULT FALSE,
        org_id              INTEGER NOT NULL REFERENCES organisations(id),
        created_by          INTEGER NOT NULL REFERENCES users(id),
        created_at          TIMESTAMPTZ DEFAULT NOW(),
        committed_at        TIMESTAMPTZ
    )""",
    """
    CREATE TABLE IF NOT EXISTS site_coordinate_import_rows (
        id                    SERIAL PRIMARY KEY,
        import_id             INTEGER NOT NULL REFERENCES site_coordinate_imports(id) ON DELETE CASCADE,
        row_number            INTEGER NOT NULL,
        site_id               INTEGER REFERENCES sites(id),
        site_code             TEXT,
        latitude              NUMERIC,
        longitude             NUMERIC,
        coordinate_accuracy_m NUMERIC,
        status                TEXT NOT NULL CHECK (status IN ('valid','invalid','conflict')),
        error                 TEXT,
        UNIQUE(import_id, row_number)
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_site_coordinate_imports_org
    ON site_coordinate_imports(org_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_site_coordinate_import_rows_batch
    ON site_coordinate_import_rows(import_id, row_number)
    """,
    """
    CREATE TABLE IF NOT EXISTS site_resolution_decisions (
        id                    SERIAL PRIMARY KEY,
        record_type           TEXT NOT NULL CHECK (record_type IN ('permit','inspection','eia','emergency')),
        record_id             INTEGER NOT NULL,
        original_text         TEXT,
        decision              TEXT NOT NULL CHECK (decision IN ('resolved','skipped','site_created')),
        resolved_site_id      INTEGER REFERENCES sites(id),
        decision_note         TEXT,
        org_id                INTEGER NOT NULL REFERENCES organisations(id),
        reviewed_by           INTEGER NOT NULL REFERENCES users(id),
        reviewed_at           TIMESTAMPTZ NOT NULL,
        created_at            TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (org_id, record_type, record_id)
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_site_resolution_decisions_queue
    ON site_resolution_decisions(org_id, decision, reviewed_at)
    """,
    # ---- Lone worker / man-down (guide C2) ----
    """
    CREATE TABLE IF NOT EXISTS lone_worker_checkins (
        id                          SERIAL PRIMARY KEY,
        session_ref                 TEXT UNIQUE NOT NULL,
        worker_id                   INTEGER REFERENCES users(id),
        expected_duration_minutes   INTEGER NOT NULL,
        location                    TEXT,
        latitude                    NUMERIC,
        longitude                   NUMERIC,
        nominated_contact_name      TEXT,
        nominated_contact_phone     TEXT,
        started_at                  TIMESTAMPTZ DEFAULT NOW(),
        expected_checkin_at         TIMESTAMPTZ NOT NULL,
        last_checkin_at             TIMESTAMPTZ,
        status                      TEXT DEFAULT 'active' CHECK (status IN ('active','checked_in','escalated','cancelled')),
        escalated_at                TIMESTAMPTZ,
        org_id                      INTEGER REFERENCES organisations(id),
        created_at                  TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_lone_worker_checkins_org ON lone_worker_checkins(org_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_lone_worker_checkins_status ON lone_worker_checkins(status, expected_checkin_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS observations (
        id              SERIAL PRIMARY KEY,
        obs_ref         TEXT UNIQUE NOT NULL,
        idempotency_key TEXT UNIQUE,
        obs_type        TEXT NOT NULL CHECK (obs_type IN ('hazard','near_miss','unsafe_act','unsafe_condition','good_practice')),
        title           TEXT NOT NULL,
        description     TEXT,
        location        TEXT,
        photo_path      TEXT,
        ai_metadata     JSONB DEFAULT '{}',
        severity        TEXT DEFAULT 'low' CHECK (severity IN ('low','medium','high','critical')),
        status          TEXT DEFAULT 'open' CHECK (status IN ('open','acknowledged','corrective_action','closed')),
        site_id         INTEGER REFERENCES sites(id),
        reported_by     INTEGER REFERENCES users(id),
        org_id          INTEGER REFERENCES organisations(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS documents (
        id              SERIAL PRIMARY KEY,
        doc_ref         TEXT UNIQUE NOT NULL,
        title           TEXT NOT NULL,
        doc_type        TEXT NOT NULL CHECK (doc_type IN ('sop','policy','guideline','form','template','regulation')),
        version         TEXT DEFAULT '1.0',
        description     TEXT,
        file_path       TEXT,
        content_text    TEXT,
        status          TEXT DEFAULT 'draft' CHECK (status IN ('draft','in_review','approved','superseded','archived')),
        approved_by     INTEGER REFERENCES users(id),
        approved_at     TIMESTAMPTZ,
        review_due_date TIMESTAMPTZ,
        supersedes      INTEGER REFERENCES documents(id),
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS document_acknowledgements (
        id              SERIAL PRIMARY KEY,
        document_id     INTEGER REFERENCES documents(id) ON DELETE CASCADE,
        user_id         INTEGER REFERENCES users(id),
        acknowledged_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (document_id, user_id)
    )""",
    # ---- Document Q&A retrieval index (guide C3), mirrors incidents_fts ----
    """
    CREATE TABLE IF NOT EXISTS documents_fts (
        document_id INTEGER PRIMARY KEY,
        title       TEXT,
        description TEXT,
        content     TEXT
    )""",
    """
    CREATE TABLE IF NOT EXISTS compliance_obligations (
        id              SERIAL PRIMARY KEY,
        obligation_ref  TEXT UNIQUE NOT NULL,
        regulation      TEXT NOT NULL,
        obligation      TEXT NOT NULL,
        regulator       TEXT NOT NULL,
        owner_id        INTEGER REFERENCES users(id),
        frequency       TEXT CHECK (frequency IN ('annual','semi_annual','quarterly','monthly','event_based','continuous')),
        next_due_date   TIMESTAMPTZ,
        status          TEXT DEFAULT 'active' CHECK (status IN ('active','overdue','compliant','waived')),
        evidence_path   TEXT,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS attachments (
        id            SERIAL PRIMARY KEY,
        entity_type   TEXT NOT NULL,
        entity_id     INTEGER NOT NULL,
        file_name     TEXT NOT NULL,
        original_name TEXT NOT NULL,
        mime_type     TEXT,
        size_bytes    INTEGER,
        sha256        TEXT,
        kind          TEXT DEFAULT 'file',
        ai_labels     JSONB,
        org_id        INTEGER NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
        uploaded_by   INTEGER REFERENCES users(id),
        created_at    TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_attachments_entity ON attachments(entity_type, entity_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS chemicals (
        id              SERIAL PRIMARY KEY,
        chem_ref        TEXT UNIQUE NOT NULL,
        name            TEXT NOT NULL,
        cas_number      TEXT,
        supplier        TEXT,
        hazard_class    TEXT,
        pictogram       TEXT,
        sds_path        TEXT,
        sds_attachment_id INTEGER REFERENCES attachments(id),
        sds_review_date TIMESTAMPTZ,
        sds_status      TEXT DEFAULT 'current' CHECK (sds_status IN ('current','expiring','expired','draft')),
        sds_extracted   JSONB DEFAULT '{}',
        quantity_units  TEXT,
        storage_location TEXT,
        site_id         INTEGER REFERENCES sites(id),
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    # ---- B5 External integrations + portal submissions (ARCHITECTURE.md Sec 5, 6) ----
    """
    CREATE TABLE IF NOT EXISTS integration_endpoints (
        id              SERIAL PRIMARY KEY,
        endpoint_key    TEXT UNIQUE NOT NULL,
        name            TEXT NOT NULL,
        system_type     TEXT NOT NULL CHECK (system_type IN ('themisiq','erp','lms','ema_portal','nssa_portal','zrp_portal','board','comms','custom')),
        direction       TEXT NOT NULL DEFAULT 'outbound' CHECK (direction IN ('inbound','outbound','bidirectional')),
        base_url        TEXT,
        auth_type       TEXT NOT NULL DEFAULT 'api_key' CHECK (auth_type IN ('api_key','oauth2','hmac','none')),
        auth_config     JSONB DEFAULT '{}',
        headers         JSONB DEFAULT '{}',
        timeout_seconds INTEGER DEFAULT 30,
        rate_limit_per_minute INTEGER DEFAULT 60,
        active          BOOLEAN DEFAULT TRUE,
        org_id          INTEGER REFERENCES organisations(id),
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_integration_endpoints_key ON integration_endpoints(endpoint_key, org_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS integration_logs (
        id              SERIAL PRIMARY KEY,
        endpoint_key    TEXT NOT NULL,
        direction       TEXT NOT NULL CHECK (direction IN ('inbound','outbound')),
        idempotency_key TEXT,
        request_payload JSONB DEFAULT '{}',
        response_payload JSONB DEFAULT '{}',
        status_code     INTEGER,
        success         BOOLEAN DEFAULT FALSE,
        error_message   TEXT,
        duration_ms     INTEGER,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_integration_logs_endpoint ON integration_logs(endpoint_key, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_integration_logs_idempotency ON integration_logs(idempotency_key)
    """,
    """
    CREATE TABLE IF NOT EXISTS themisiq_links (
        id                  SERIAL PRIMARY KEY,
        she_entity_type     TEXT NOT NULL,
        she_entity_id       INTEGER NOT NULL,
        themis_entity_type  TEXT NOT NULL,
        themis_entity_id    INTEGER NOT NULL,
        relationship        TEXT NOT NULL DEFAULT 'related' CHECK (relationship IN ('derived_from','triggers','related','mirrors')),
        direction           TEXT NOT NULL DEFAULT 'she_to_themis' CHECK (direction IN ('she_to_themis','themis_to_she','bidirectional')),
        last_sync_hash      TEXT,
        last_synced_at      TIMESTAMPTZ,
        sync_error          TEXT,
        created_at          TIMESTAMPTZ DEFAULT NOW(),
        updated_at          TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_themisiq_links ON themisiq_links(she_entity_type, she_entity_id, themis_entity_type, themis_entity_id, relationship)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_themisiq_links_she ON themisiq_links(she_entity_type, she_entity_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_themisiq_links_themis ON themisiq_links(themis_entity_type, themis_entity_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS integration_queue (
        id              SERIAL PRIMARY KEY,
        endpoint_key    TEXT NOT NULL,
        entity_type     TEXT NOT NULL,
        entity_id       INTEGER NOT NULL,
        operation       TEXT NOT NULL DEFAULT 'push' CHECK (operation IN ('push','pull','sync','submit')),
        payload         JSONB DEFAULT '{}',
        idempotency_key TEXT,
        status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','failed','completed','cancelled')),
        attempts        INTEGER DEFAULT 0,
        last_error      TEXT,
        next_retry_at   TIMESTAMPTZ,
        created_by      INTEGER REFERENCES users(id),
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_integration_queue_status ON integration_queue(status, next_retry_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_integration_queue_entity ON integration_queue(entity_type, entity_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS submission_channels (
        id              SERIAL PRIMARY KEY,
        channel_key     TEXT UNIQUE NOT NULL,
        name            TEXT NOT NULL,
        authority       TEXT NOT NULL,
        channel_type    TEXT NOT NULL CHECK (channel_type IN ('portal','email','api','manual')),
        endpoint_id     INTEGER REFERENCES integration_endpoints(id),
        recipient_email TEXT,
        submission_url  TEXT,
        org_id          INTEGER REFERENCES organisations(id),
        active          BOOLEAN DEFAULT TRUE,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE TABLE IF NOT EXISTS submission_deliveries (
        id              SERIAL PRIMARY KEY,
        report_id       INTEGER NOT NULL REFERENCES statutory_reports(id) ON DELETE CASCADE,
        channel_id      INTEGER REFERENCES submission_channels(id),
        channel_key     TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','queued','sent','delivered','acknowledged','failed','rejected')),
        tracking_ref    TEXT,
        dispatched_at   TIMESTAMPTZ,
        acknowledged_at TIMESTAMPTZ,
        response_payload JSONB DEFAULT '{}',
        error_message   TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE INDEX IF NOT EXISTS idx_submission_deliveries_report ON submission_deliveries(report_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_submission_deliveries_status ON submission_deliveries(status, channel_key)
    """,
    """
    CREATE TABLE IF NOT EXISTS integration_secrets (
        id              SERIAL PRIMARY KEY,
        endpoint_id     INTEGER NOT NULL REFERENCES integration_endpoints(id) ON DELETE CASCADE,
        secret_name     TEXT NOT NULL,
        secret_value    TEXT NOT NULL,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )""",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_integration_secrets_endpoint_name ON integration_secrets(endpoint_id, secret_name)
    """,
]

# Column-level additions required by B2 SDS extraction
# Tamper-evidence hash chain on the audit log (NFR-SHE-004). Existing table,
# so retrofit onto upgraded deployments (legacy rows keep NULL hashes and sit
# before the chain's genesis; new rows chain from the last hashed row).
AUDIT_HASH_COLUMNS = [
    "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS chain_ts TEXT",
    "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS prev_hash TEXT",
    "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS record_hash TEXT",
]

SDS_COLUMNS = [
    "ALTER TABLE chemicals ADD COLUMN IF NOT EXISTS sds_attachment_id INTEGER REFERENCES attachments(id)",
    "ALTER TABLE chemicals ADD COLUMN IF NOT EXISTS sds_review_date TIMESTAMPTZ",
    "ALTER TABLE chemicals ADD COLUMN IF NOT EXISTS sds_status TEXT DEFAULT 'current' CHECK (sds_status IN ('current','expiring','expired','draft'))",
    "ALTER TABLE chemicals ADD COLUMN IF NOT EXISTS sds_extracted JSONB DEFAULT '{}'",
]

# Column-level additions required by the integration spec (11.1)
RISK_ORIGIN_COLUMNS = [
    "ALTER TABLE risks ADD COLUMN IF NOT EXISTS origin_system TEXT DEFAULT 'she'",
    "ALTER TABLE risks ADD COLUMN IF NOT EXISTS themis_mirror_id INTEGER",
]

ESG_CSV_COLUMNS = [
    "ALTER TABLE esg_kpi_entries ADD COLUMN IF NOT EXISTS source_upload_id INTEGER REFERENCES esg_csv_uploads(id)",
    "ALTER TABLE esg_kpi_entries ADD COLUMN IF NOT EXISTS source_row_id INTEGER REFERENCES esg_csv_rows(id)",
]

STATUTORY_REPORT_COLUMNS = [
    "ALTER TABLE statutory_reports ADD COLUMN IF NOT EXISTS lock_version INTEGER DEFAULT 1",
]

# Column-level additions required by B5 external integrations
B5_MIGRATION_COLUMNS = [
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS themis_event_id INTEGER",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS nssa_submitted BOOLEAN DEFAULT FALSE",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ema_submitted BOOLEAN DEFAULT FALSE",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS zrp_submitted BOOLEAN DEFAULT FALSE",
]

IDEMPOTENCY_COLUMNS = [
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS idempotency_key TEXT UNIQUE",
    "ALTER TABLE observations ADD COLUMN IF NOT EXISTS idempotency_key TEXT UNIQUE",
]

# Column-level additions required by B5 incident intake depth (missed when
# B5 shipped: CREATE TABLE IF NOT EXISTS only creates incidents fresh, it
# does not retrofit columns onto a database where incidents already existed)
INCIDENT_DEPTH_COLUMNS = [
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS immediate_actions TEXT",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS estimated_cost NUMERIC",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS witnesses JSONB DEFAULT '[]'",
]

# Column-level additions required by C1 geographic map
SITE_COORD_COLUMNS = [
    "ALTER TABLE sites ADD COLUMN IF NOT EXISTS latitude NUMERIC",
    "ALTER TABLE sites ADD COLUMN IF NOT EXISTS longitude NUMERIC",
    "ALTER TABLE sites ADD COLUMN IF NOT EXISTS coordinate_source TEXT",
    "ALTER TABLE sites ADD COLUMN IF NOT EXISTS coordinate_accuracy_m NUMERIC",
    "ALTER TABLE sites ADD COLUMN IF NOT EXISTS coordinates_updated_at TIMESTAMPTZ",
    "ALTER TABLE sites ADD COLUMN IF NOT EXISTS coordinates_updated_by INTEGER REFERENCES users(id)",
    "ALTER TABLE sites ADD COLUMN IF NOT EXISTS geocode_provider TEXT",
    "ALTER TABLE sites ADD COLUMN IF NOT EXISTS geocode_place_id TEXT",
]

# Phase 2 canonical-site relationships. These ALTER statements are required
# even though fresh CREATE TABLE definitions contain the columns: existing
# deployments keep their old table shape when CREATE TABLE IF NOT EXISTS runs.
SITE_RELATION_COLUMNS = [
    ("permits", "ALTER TABLE permits ADD COLUMN IF NOT EXISTS site_id INTEGER REFERENCES sites(id)"),
    ("inspections", "ALTER TABLE inspections ADD COLUMN IF NOT EXISTS site_id INTEGER REFERENCES sites(id)"),
    ("eia_projects", "ALTER TABLE eia_projects ADD COLUMN IF NOT EXISTS site_id INTEGER REFERENCES sites(id)"),
    ("emergency_events", "ALTER TABLE emergency_events ADD COLUMN IF NOT EXISTS site_id INTEGER REFERENCES sites(id)"),
]

# Created after retrofit columns so upgrading a pre-map database cannot fail
# by trying to index columns that init_db() has not added yet.
GEOSPATIAL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sites_org_lat_lng ON sites(org_id, latitude, longitude)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_org_lat_lng ON incidents(org_id, latitude, longitude)",
    "CREATE INDEX IF NOT EXISTS idx_incidents_org_site ON incidents(org_id, site_id)",
    # PostgreSQL 16 evidence with 20,000 source rows showed the linked-source
    # side scanning all rows for a six-site BBOX. These matching relationships
    # share that query shape and also back facility intelligence counts.
    "CREATE INDEX IF NOT EXISTS idx_permits_org_site ON permits(org_id, site_id)",
    "CREATE INDEX IF NOT EXISTS idx_inspections_org_site ON inspections(org_id, site_id)",
    "CREATE INDEX IF NOT EXISTS idx_eia_projects_org_site ON eia_projects(org_id, site_id)",
    "CREATE INDEX IF NOT EXISTS idx_emergency_events_org_site ON emergency_events(org_id, site_id)",
    "CREATE INDEX IF NOT EXISTS idx_assets_org_site ON assets(org_id, site_id)",
    "CREATE INDEX IF NOT EXISTS idx_observations_org_site ON observations(org_id, site_id)",
]

# Column-level additions required by C3 document Q&A
DOCUMENT_CONTENT_COLUMNS = [
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_text TEXT",
]


# ---------------------------------------------------------------------------
# SQLite rewrite of PostgreSQL types
# ---------------------------------------------------------------------------
_SQLITE_REWRITES = [
    (r"SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT"),
    (r"TIMESTAMPTZ", "TEXT"),
    (r"JSONB", "TEXT"),
    (r"NUMERIC", "REAL"),
    (r"BOOLEAN", "INTEGER"),
    (r"GENERATED ALWAYS AS \(likelihood \* impact\) STORED", "GENERATED ALWAYS AS (likelihood * impact) STORED"),
    (
        r"GENERATED ALWAYS AS \(\s*CAST\(likelihood \* impact AS NUMERIC\) / NULLIF\(control_effectiveness, 0\)\s*\) STORED",
        "GENERATED ALWAYS AS (CAST(likelihood * impact AS REAL) / NULLIF(control_effectiveness, 0)) STORED",
    ),
    (r"DEFAULT NOW\(\)", "DEFAULT (datetime('now'))"),
    (
        r"CREATE TABLE IF NOT EXISTS incidents_fts \(\s*incident_id INTEGER PRIMARY KEY,\s*title\s+TEXT,\s*description\s+TEXT,\s*incident_type\s+TEXT,\s*severity\s+TEXT,\s*content\s+TEXT\s*\)",
        "CREATE VIRTUAL TABLE IF NOT EXISTS incidents_fts USING fts5(incident_id UNINDEXED, title, description, incident_type, severity, content)"
    ),
    (
        r"CREATE TABLE IF NOT EXISTS documents_fts \(\s*document_id INTEGER PRIMARY KEY,\s*title\s+TEXT,\s*description\s+TEXT,\s*content\s+TEXT\s*\)",
        "CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(document_id UNINDEXED, title, description, content)"
    ),
    # SQLite supports ON DELETE CASCADE natively (with PRAGMA foreign_keys=ON),
    # so do NOT strip it - child rows must cascade (guide 4: FK rules).
]


def _to_sqlite_schema(ddl: str) -> str:
    out = ddl
    for pattern, repl in _SQLITE_REWRITES:
        out = re.sub(pattern, repl, out)
    return out


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------
_PG_CASTERS_REGISTERED = False


def _register_pg_casters() -> None:
    """Make psycopg2 return SQLite-compatible Python types (process-global).

    The whole app was written against SQLite semantics: timestamp columns are
    ISO strings and NUMERIC is float. Postgres would instead hand back
    datetime and Decimal objects, breaking shared data-service code that does
    datetime.fromisoformat(row[...]), json.dumps(dict(row)), and float maths.
    Registering two type casters at the driver boundary fixes all of that in
    one place, so module code stays backend-agnostic.
    """
    global _PG_CASTERS_REGISTERED
    if _PG_CASTERS_REGISTERED:
        return
    import psycopg2.extras
    from psycopg2 import extensions

    # JSONB/JSON columns are declared as JSONB on Postgres but the app treats
    # them as TEXT everywhere (json.dumps on write, json.loads on read, which
    # is what SQLite requires). psycopg2 would otherwise auto-parse JSONB into
    # dict/list on read, breaking those json.loads() calls. loads=identity
    # makes the driver hand back the raw JSON string, matching the TEXT path.
    psycopg2.extras.register_default_jsonb(loads=lambda s: s, globally=True)
    psycopg2.extras.register_default_json(loads=lambda s: s, globally=True)

    dec2float = extensions.new_type(
        extensions.DECIMAL.values, "DEC2FLOAT",
        lambda v, cur: float(v) if v is not None else None)
    extensions.register_type(dec2float)
    # timestamp (OID 1114) and timestamptz (OID 1184) -> ISO-ish string, with
    # the space separator normalised to 'T' so it matches Python isoformat
    # (what the app writes) and datetime.fromisoformat() parses it back.
    ts2str = extensions.new_type(
        (1114, 1184), "TS2STR",
        lambda v, cur: v.replace(" ", "T") if v is not None else None)
    extensions.register_type(ts2str)
    _PG_CASTERS_REGISTERED = True


def _connect() -> object:
    if settings.is_postgres():
        import psycopg2.extras
        from psycopg2.pool import ThreadedConnectionPool

        _register_pg_casters()
        pool = getattr(_local, "pg_pool", None)
        if pool is None:
            # DictCursor (not RealDictCursor): its rows support BOTH integer and
            # string indexing, matching sqlite3.Row exactly. Every data_service
            # reads rows as row["col"]/dict(row), and ~19 call sites also do
            # integer access (fetchone()[0] on COUNT queries); RealDictCursor is
            # key-only and would KeyError on those. Set on the pool so every
            # pooled connection's default cursor is a DictCursor.
            pool = ThreadedConnectionPool(
                1, 20, settings.DATABASE_URL,
                cursor_factory=psycopg2.extras.DictCursor)
            _local.pg_pool = pool
        conn = pool.getconn()
        return _PgConn(conn, pool)
    else:
        conn = sqlite3.connect(settings.DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return _SqliteConn(conn)


class _SqliteConn:
    """Minimal wrapper presenting the same interface as the PG wrapper.

    Converts PostgreSQL %s placeholders to SQLite ? placeholders at the
    wrapper boundary (guide 4: "A wrapper rewrites for SQLite in dev mode").
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        return self._conn.execute(sql.replace("%s", "?"), params or [])

    def executescript(self, sql):
        return self._conn.executescript(sql)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if exc[0]:
            self.rollback()
        else:
            self.commit()
        self.close()
        return False


class _PgConn:
    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool

    def execute(self, sql, params=None):
        cur = self._conn.cursor()
        # Passing an empty tuple makes psycopg2 apply its ``%`` interpolation
        # even when the statement has no placeholders. Literal SQL patterns
        # such as ``LIKE 'map.provider.%'`` then raise IndexError. Omit the
        # parameter argument entirely when the caller supplied none.
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(sql, params)
        return cur

    def executescript(self, sql):
        return self._conn.cursor().execute(sql)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._pool.putconn(self._conn)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if exc[0]:
            self.rollback()
        else:
            self.commit()
        self.close()
        return False


def resolve_org(db, org_id: int | None, user_id: int | None) -> int | None:
    """Tenant resolution helper: explicit org wins, else inherit from the creator.

    Audit S5: keeps data tied to a tenant even when callers (event handlers,
    tests) omit org_id - fails closed downstream instead of leaking.
    """
    if org_id:
        return org_id
    if user_id:
        row = db.execute("SELECT org_id FROM users WHERE id = %s", (user_id,)).fetchone()
        if row and row["org_id"]:
            return row["org_id"]
    return None


def get_db() -> object:
    return _connect()


def get_db_background() -> object:
    """Short-timeout connection for scheduler jobs (guide 5.2)."""
    if settings.is_postgres():
        return _connect()
    conn = sqlite3.connect(settings.DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return _SqliteConn(conn)


def init_db() -> None:
    """Create all tables. Runs DDL; PostgreSQL keeps it as-is, SQLite gets rewritten."""
    with _connect() as db:
        for ddl in SCHEMA:
            stmt = ddl if settings.is_postgres() else _to_sqlite_schema(ddl)
            db.execute(stmt)
        for col in AUDIT_HASH_COLUMNS:
            if settings.is_postgres():
                db.execute(col)
            else:
                cols_audit = {r[1] for r in db.execute("PRAGMA table_info(audit_log)").fetchall()}
                for name in ("chain_ts", "prev_hash", "record_hash"):
                    if name not in cols_audit:
                        db.execute(f"ALTER TABLE audit_log ADD COLUMN {name} TEXT")
        for col in RISK_ORIGIN_COLUMNS:
            if settings.is_postgres():
                db.execute(col)
            else:
                # SQLite lacks ADD COLUMN IF NOT EXISTS: check column existence first
                cols = {r[1] for r in db.execute("PRAGMA table_info(risks)").fetchall()}
                if "origin_system" not in cols:
                    db.execute("ALTER TABLE risks ADD COLUMN origin_system TEXT DEFAULT 'she'")
                if "themis_mirror_id" not in cols:
                    db.execute("ALTER TABLE risks ADD COLUMN themis_mirror_id INTEGER")
        for col in IDEMPOTENCY_COLUMNS:
            if settings.is_postgres():
                db.execute(col)
            else:
                cols_inc = {r[1] for r in db.execute("PRAGMA table_info(incidents)").fetchall()}
                if "idempotency_key" not in cols_inc:
                    db.execute("ALTER TABLE incidents ADD COLUMN idempotency_key TEXT")
                    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_incidents_idempotency ON incidents(idempotency_key)")
                cols_obs = {r[1] for r in db.execute("PRAGMA table_info(observations)").fetchall()}
                if "idempotency_key" not in cols_obs:
                    db.execute("ALTER TABLE observations ADD COLUMN idempotency_key TEXT")
                    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_observations_idempotency ON observations(idempotency_key)")
        for col in SDS_COLUMNS:
            if settings.is_postgres():
                db.execute(col)
            else:
                cols_chem = {r[1] for r in db.execute("PRAGMA table_info(chemicals)").fetchall()}
                if "sds_attachment_id" not in cols_chem:
                    db.execute("ALTER TABLE chemicals ADD COLUMN sds_attachment_id INTEGER REFERENCES attachments(id)")
                if "sds_review_date" not in cols_chem:
                    db.execute("ALTER TABLE chemicals ADD COLUMN sds_review_date TEXT")
                if "sds_status" not in cols_chem:
                    db.execute("ALTER TABLE chemicals ADD COLUMN sds_status TEXT DEFAULT 'current' CHECK (sds_status IN ('current','expiring','expired','draft'))")
                if "sds_extracted" not in cols_chem:
                    db.execute("ALTER TABLE chemicals ADD COLUMN sds_extracted TEXT DEFAULT '{}'")
        for col in STATUTORY_REPORT_COLUMNS:
            if settings.is_postgres():
                db.execute(col)
            else:
                cols_sr = {r[1] for r in db.execute("PRAGMA table_info(statutory_reports)").fetchall()}
                if "lock_version" not in cols_sr:
                    db.execute("ALTER TABLE statutory_reports ADD COLUMN lock_version INTEGER DEFAULT 1")
        for col in B5_MIGRATION_COLUMNS:
            if settings.is_postgres():
                db.execute(col)
            else:
                cols_inc = {r[1] for r in db.execute("PRAGMA table_info(incidents)").fetchall()}
                if "themis_event_id" not in cols_inc:
                    db.execute("ALTER TABLE incidents ADD COLUMN themis_event_id INTEGER")
                if "nssa_submitted" not in cols_inc:
                    db.execute("ALTER TABLE incidents ADD COLUMN nssa_submitted INTEGER DEFAULT 0")
                if "ema_submitted" not in cols_inc:
                    db.execute("ALTER TABLE incidents ADD COLUMN ema_submitted INTEGER DEFAULT 0")
                if "zrp_submitted" not in cols_inc:
                    db.execute("ALTER TABLE incidents ADD COLUMN zrp_submitted INTEGER DEFAULT 0")
        for col in ESG_CSV_COLUMNS:
            if settings.is_postgres():
                db.execute(col)
            else:
                cols_entries = {r[1] for r in db.execute("PRAGMA table_info(esg_kpi_entries)").fetchall()}
                if "source_upload_id" not in cols_entries:
                    db.execute("ALTER TABLE esg_kpi_entries ADD COLUMN source_upload_id INTEGER REFERENCES esg_csv_uploads(id)")
                if "source_row_id" not in cols_entries:
                    db.execute("ALTER TABLE esg_kpi_entries ADD COLUMN source_row_id INTEGER REFERENCES esg_csv_rows(id)")
        for col in INCIDENT_DEPTH_COLUMNS:
            if settings.is_postgres():
                db.execute(col)
            else:
                cols_inc = {r[1] for r in db.execute("PRAGMA table_info(incidents)").fetchall()}
                if "immediate_actions" not in cols_inc:
                    db.execute("ALTER TABLE incidents ADD COLUMN immediate_actions TEXT")
                if "estimated_cost" not in cols_inc:
                    db.execute("ALTER TABLE incidents ADD COLUMN estimated_cost REAL")
                if "witnesses" not in cols_inc:
                    db.execute("ALTER TABLE incidents ADD COLUMN witnesses TEXT DEFAULT '[]'")
        for col in SITE_COORD_COLUMNS:
            if settings.is_postgres():
                db.execute(col)
            else:
                cols_sites = {r[1] for r in db.execute("PRAGMA table_info(sites)").fetchall()}
                if "latitude" not in cols_sites:
                    db.execute("ALTER TABLE sites ADD COLUMN latitude REAL")
                if "longitude" not in cols_sites:
                    db.execute("ALTER TABLE sites ADD COLUMN longitude REAL")
                if "coordinate_source" not in cols_sites:
                    db.execute("ALTER TABLE sites ADD COLUMN coordinate_source TEXT")
                if "coordinate_accuracy_m" not in cols_sites:
                    db.execute("ALTER TABLE sites ADD COLUMN coordinate_accuracy_m REAL")
                if "coordinates_updated_at" not in cols_sites:
                    db.execute("ALTER TABLE sites ADD COLUMN coordinates_updated_at TEXT")
                if "coordinates_updated_by" not in cols_sites:
                    db.execute("ALTER TABLE sites ADD COLUMN coordinates_updated_by INTEGER REFERENCES users(id)")
                if "geocode_provider" not in cols_sites:
                    db.execute("ALTER TABLE sites ADD COLUMN geocode_provider TEXT")
                if "geocode_place_id" not in cols_sites:
                    db.execute("ALTER TABLE sites ADD COLUMN geocode_place_id TEXT")
        for table, col in SITE_RELATION_COLUMNS:
            if settings.is_postgres():
                db.execute(col)
            else:
                cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
                if "site_id" not in cols:
                    db.execute(
                        f"ALTER TABLE {table} ADD COLUMN site_id INTEGER REFERENCES sites(id)"
                    )
        for index in GEOSPATIAL_INDEXES:
            db.execute(index)
        for col in DOCUMENT_CONTENT_COLUMNS:
            if settings.is_postgres():
                db.execute(col)
            else:
                cols_docs = {r[1] for r in db.execute("PRAGMA table_info(documents)").fetchall()}
                if "content_text" not in cols_docs:
                    db.execute("ALTER TABLE documents ADD COLUMN content_text TEXT")
