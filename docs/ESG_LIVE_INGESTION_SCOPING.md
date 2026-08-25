# ESG Live Data Ingestion — Scoping Note

**Status:** scoping only (not built). **Relates to:** ESG-FR-002, AR-FR-002, FNR-SHE-060, NFR-SHE-007.
**Date:** 2026-08-16.

## Why this is a note, not code
The BRS requires the ESG KPI module to "support automated data collection from DPA Operations, Fleet Management, Finance, and HR systems" (ESG-FR-002) with "data auto-populates from source systems without manual entry for standard metrics." That is a **live integration against external enterprise systems** which do not exist in this environment to integrate against, and whose interface contracts the BRS itself defers to solution design (§7: "detailed integration architecture is to be specified and agreed upon during the solution design phase"). So this cannot be *built* here; it can only be *scoped*.

## What exists today (the manual/semi-automated path)
The ESG KPI module already covers the non-live path end to end:
- **Structured manual entry + validation** (`esg_kpi.record_kpi_entry`): per-KPI actual vs target, automatic RAG, anomaly flag, and FNR-SHE-063 auto-incident on a non-zero zero-target KPI (spillage, LTI, fatality).
- **CSV ingest** (`esg_kpi.csv_service`): mapped column-to-KPI upload with preview, anomaly/duplicate detection, and commit — for periodic bulk loads from a source-system export.
- **API-key ingest** (`esg_kpi.csv_service.record_api_kpi_payload`, `X-ESG-API-Key`): an authenticated machine endpoint an ops system can already POST monthly payloads to.
- **Outbound dispatch stubs** (`external_integration`): typed targets incl. ERP/LMS already exist for the reverse direction.
- **Report insertion** (`reporting.compile_annual_sustainability`, this branch): ESG KPI data now auto-inserts into the annual sustainability report draft.

So the gap is specifically *inbound live feeds*, not the KPI model, validation, storage, or reporting.

## What a live feed would require (per source system)
For each of DPA Operations, Fleet Management, Finance, HR:
1. **Interface contract** — agreed API (REST/JSON per NFR-SHE-007) or scheduled export; auth (OAuth2/API key/mTLS); the field-to-KPI mapping (reuse the existing `esg_csv_mappings` model).
2. **Scheduled pull or webhook push** — a per-source connector that maps the payload to `record_kpi_entry` calls. The `external_integration` `system_type` enum already reserves `erp`/`lms`/`custom`; add source-specific connectors there.
3. **Reconciliation** — reuse the existing CSV preview/anomaly/duplicate logic before commit; do not auto-commit unreviewed anomalous values (existing controls).
4. **Provenance + retention** — record source system + pull timestamp on each entry (an `esg_kpi_entries.source` column would be the one small schema add).
5. **Failure handling** — retry + alert on feed failure (NFR-SHE-006 pattern; `webhook_deliveries`/retry infra already exists for outbound and can be mirrored).

## Recommended sequencing
- **Now:** keep API-key + CSV ingest as the supported path; document the per-KPI mapping for each source system so an ops owner can push monthly.
- **At solution design:** pick ONE source system (likely Finance or DPA, highest-volume) as a pilot connector; prove the pull → map → reconcile → `record_kpi_entry` loop end to end; then replicate per source.
- **Schema:** the only anticipated change is an optional `source` provenance column on `esg_kpi_entries`; everything else reuses existing mapping/validation/reporting.

## Bottom line
Live ingestion is a **connector-per-source integration task blocked on external systems + agreed contracts**, not missing platform capability. The platform side (KPI model, validation, ingest endpoints, reconciliation, reporting insertion) is in place; a pilot connector is the right first build once a source system and its interface are available.
