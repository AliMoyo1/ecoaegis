# Security Hardening — Deferred Items Scoping

**Status:** scoping only. These three coverage-pass gaps need organisation/infrastructure decisions and cannot be built in-app without them. Built items (audit tamper-evidence NFR-004, IP allowlist SEC-006, retention policy NFR-003) shipped in the same track.
**Date:** 2026-08-16.

## SEC-SHE-001 — SSO / central identity management
**Current:** local session auth (`core/auth.py`, bcrypt) + enforced MFA (pyotp). RBAC by capability (`core/rbac.py`).
**Gap:** the BRS requires authentication via the org's central IdP with SSO.
**Blocked on:** which IdP and protocol (SAML 2.0 vs OIDC/OAuth2) and the IdP's metadata/client registration — an org decision.
**Scope when decided:**
1. Add an OIDC/SAML client (e.g. `authlib` for OIDC) behind a `AUTH_MODE` setting (`local` | `oidc` | `saml`); keep local auth as a fallback/break-glass.
2. On successful IdP assertion, map the IdP subject/email to a `users` row (auto-provision or pre-provisioned), start the existing session; keep RBAC/capabilities app-side (map IdP groups -> role_key).
3. MFA becomes the IdP's responsibility under SSO (or keep app MFA as step-up for incident/risk/statutory views per SEC-001).
4. Effort ~5-8 days incl. metadata exchange + a test IdP. No data-model change beyond an optional `users.external_subject` column.

## SEC-SHE-004 — Encryption at rest (AES-256)
**Current:** not implemented in-app (correctly — it is a storage-layer control).
**Gap:** SHE/Risk/vendor data must be AES-256 at rest.
**Blocked on:** the production database/host and its encryption facility.
**Scope when decided (all deployment, not app code):**
- **PostgreSQL:** transparent data encryption via the storage layer — an encrypted volume (LUKS / cloud provider disk encryption) and/or `pgcrypto` for specific columns if field-level is required. Managed PG (RDS/Cloud SQL) offers AES-256 at rest as a checkbox.
- **Backups:** encrypt backups with the same standard; manage keys in the secrets vault (SEC-007).
- App change: none required for volume/disk encryption; only column-level `pgcrypto` would touch schema/queries (avoid unless a specific field mandates it).

## SEC-SHE-007 — Secrets management vault
**Current:** config from environment / gitignored `.env` (`config.py`); no hardcoded secrets in source (good), but no vault.
**Gap:** service credentials, API keys, and DB strings must live in a secrets vault.
**Blocked on:** which vault (HashiCorp Vault, cloud KMS/Secrets Manager) — an infra decision.
**Scope when decided:**
1. Introduce a secrets provider seam: `config.py` reads secrets via a `get_secret(name)` that resolves from the vault in prod and falls back to env in dev.
2. Move `SECRET_KEY`, `DATABASE_URL`, all `*_API_KEY`/`*_AUTH_TOKEN`, and the map/AI provider tokens behind it.
3. Rotate the moved secrets; add a startup check that fails closed if a required secret is absent in prod.
4. Effort ~3-5 days incl. deployment wiring; no schema change.

## Recommendation
The two most material for a go-live sign-off are **SSO (SEC-001)** and **secrets vault (SEC-007)** — both need a single org decision (which IdP, which vault) to unblock, then are a few days each. **Encryption at rest (SEC-004)** is a deployment checkbox once the production DB/host is chosen. All three are unblocked by decisions, not by missing platform capability.
