# EcoAegis Mapbox operations and emergency-disable runbook

## Runbook metadata

| Field | Value |
|---|---|
| Service | EcoAegis Command Map |
| First issued | 2026-08-28 |
| Scope | Provider admission, cost controls, monitoring, continuity, disable, and rollback |
| Safe default | `MAP_ENGINE=leaflet` |
| Production Mapbox state | Not enabled |
| Review cadence | Before enablement, monthly after enablement, and after any pricing or deployment change |

## Named ownership gate

Production Mapbox enablement is blocked until the following fields contain real names and approved contact routes. Do not use a shared mailbox as the only emergency contact.

| Responsibility | Required owner | Current state |
|---|---|---|
| EcoAegis service owner | Approves application changes and rollback | Unassigned |
| SHE operational owner | Accepts degraded basemap and continuity mode | Unassigned |
| Technical token owner | Creates, restricts, rotates, and revokes the public token | Unassigned |
| Billing account owner | Controls the Mapbox account and provider-console access | Unassigned |
| Finance approver | Confirms the complete invoice remains below US$800 | Unassigned |
| Security and Privacy reviewer | Approves origin restrictions, telemetry disclosure, and incident handling | Unassigned |
| Deployment owner | Maintains the exact production service and restart commands | Unassigned |

## Safety rules

1. Never paste the Mapbox public token into Git, issue trackers, chat, screenshots, audit values, application logs, or shell history.
2. The public token is not application authorization. EcoAegis session, role, organization, CSRF, nonce, and budget checks remain mandatory.
3. Never send operational GeoJSON, feature properties, organization IDs, user IDs, audit data, or private attachments to Mapbox.
4. Never enable Geocoding, Search, Directions, Tilesets, telemetry add-ons, or another billable product through this runbook.
5. Never use public OpenStreetMap tiles as a silent emergency fallback.
6. Never increase the admission limit to hide repeated map initialization or inefficient page behavior.
7. A provider problem must not block authenticated operational records. Use the continuity list or Leaflet rollback.

## Required configuration

| Variable | Production rule |
|---|---|
| `MAP_ENGINE` | `leaflet` until approved; `mapbox` only during an authorized enablement window |
| `MAPBOX_PUBLIC_TOKEN` | Non-default public token from the approved secret store, minimum scope, exact origins |
| `MAPBOX_GL_VERSION` | Exactly `3.28.1` until a separately reviewed upgrade |
| `MAPBOX_STYLE_STANDARD` | Approved `mapbox://styles/...` Standard style |
| `MAPBOX_STYLE_SATELLITE` | Approved `mapbox://styles/...` Standard Satellite style |
| `MAP_PROVIDER_WARNING_LOADS` | `150000` unless Finance approves a lower value |
| `MAP_PROVIDER_CRITICAL_LOADS` | `175000` unless Finance approves a lower value |
| `MAP_PROVIDER_MONTHLY_LIMIT` | No more than `180000`; lower it when other invoice items reduce headroom |
| `MAP_PROVIDER_NONCE_TTL_SECONDS` | `300`, within the enforced 60 to 900 second range |
| `GEOCODER_PROVIDER` | `none` |

## Preconditions for staging enablement

Confirm all items before supplying a staging token:

- working tree and target commit are recorded
- full SQLite suite passes
- disposable PostgreSQL suite passes
- vendored Mapbox assets and checksums match the reviewed 3.28.1 bundle
- token is public, non-default, minimum-scope, and restricted to the exact staging origin
- staging account is isolated from production billing where practical
- warning, critical, and stop thresholds are recorded
- provider-console reviewer and evidence location are named
- rollback owner is present during the change window
- no unrelated EcoAegis service or another application shares the change

## Local rehearsal

The following starts EcoAegis locally. It is not a production service command.

```powershell
$env:MAP_ENGINE = 'mapbox'
# Inject MAPBOX_PUBLIC_TOKEN from the approved local secret mechanism.
./.venv/Scripts/python.exe -m uvicorn sheplatform.main:app --host 127.0.0.1 --port 8080
```

Verify health from another terminal:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/health
```

Expected body:

```json
{"status":"ok","app":"ecoaegis","version":"0.1.0"}
```

Sign in through the approved local account and verify:

1. `/map` renders `data-engine="mapbox"`.
2. The provider status becomes `Secure basemap ready` only after admission.
3. The budget status is visible only to the authorized settings role.
4. Operational manifest and layer requests use `/map/api/...` on the EcoAegis origin.
5. Standard and Satellite style switching creates no second map object or admission.
6. Reloading creates one new page nonce and at most one new admission.
7. Disabling the token or reaching the cap shows continuity records without a provider map.

## Production enablement procedure

The production service wrapper, host, and restart command are not recorded in this repository. This is a blocking gate. The deployment owner must add the exact approved command here before the first production enablement. Do not infer a Windows service, scheduled task, Docker Compose service, or Uvicorn command.

When that gate is resolved:

1. Open an approved change record with commit, environment, owners, window, health checks, rollback trigger, and Finance approval.
2. Record the current `MAP_ENGINE`, threshold values, deployed commit, and service status.
3. Confirm the restricted token exists in the approved deployment secret store and nowhere else.
4. Set `MAP_ENGINE=mapbox` in the approved deployment environment.
5. Run the exact production restart or rolling-reload command recorded by the deployment owner.
6. Verify `/health`, sign-in, map admission, same-origin layer requests, budget state, continuity mode, and logs.
7. Confirm the Mapbox console records the expected test initialization and no unexpected product usage.
8. Observe the service for the approved change window before closing the change record.

## Routine monitoring

### Daily technical check

1. Verify `/health` returns HTTP 200.
2. Review application errors for provider admission, scheduler, WebGL, CSP, and layer failures.
3. Confirm logs do not contain a token or operational feature payload.
4. Check the authorized budget panel for admitted loads and state.
5. Confirm continuity mode remains usable if provider initialization is unavailable.

### Monthly billing reconciliation

Complete this at least weekly near the warning threshold and monthly otherwise:

1. Record EcoAegis admitted loads for the current UTC billing month.
2. Record provider-console Map Loads for Web and every other enabled Mapbox product.
3. Explain timing lag, retries, shared-account traffic, or material variance.
4. Recalculate the projected complete invoice including tax, support, currency conversion, and auxiliary products.
5. Lower the local limit before forecast exposure reaches the corporate ceiling.
6. Store the approved reconciliation in the named evidence location.
7. Escalate unowned or unexplained usage immediately.

### Audit events

The application records privacy-minimal threshold transitions in the tamper-evident audit chain:

- `map.provider.warning`
- `map.provider.critical`
- `map.provider.blocked`

These events contain the provider, UTC billing month, and before/after admission counts. They must not contain tokens, user-entered filters, feature details, or attachments.

### Admission-row retention

The `map_provider_admission_retention` job runs daily at 03:20 UTC. It retains opaque admission rows for the current and previous UTC billing months. Monthly aggregate usage and threshold audit evidence remain separate.

If the scheduler logs a failure:

1. do not delete rows manually;
2. record the error and database health;
3. confirm the scheduler is registered once;
4. run the tested retention function only in an approved maintenance context;
5. verify current and prior month rows remain before closing the incident.

## Threshold response

### Warning at 150,000 admitted loads

1. Notify the billing, Finance, service, and technical token owners.
2. Reconcile local admissions against the Mapbox console.
3. Identify other account products or applications.
4. Reforecast the complete invoice.
5. Freeze nonessential provider experiments.
6. Lower the critical and stop thresholds if remaining headroom is smaller than planned.

### Critical at 175,000 admitted loads

1. Open an operational incident or priority change.
2. Prepare immediate `MAP_ENGINE=leaflet` disablement.
3. Confirm the continuity owner and user communication.
4. Reconcile billing and investigate duplicate initialization.
5. Do not increase the limit without written Finance and service-owner approval.

### Stop at 180,000 admitted loads

The application denies new provider sessions before releasing a token. Expected behavior:

- HTTP 429 from `/map/api/provider-session`
- no token in the response
- no Mapbox object constructed
- visible `Basemap monthly limit reached` status
- authenticated operational continuity list remains available

Keep the stop active until a new UTC billing month or an approved lower-risk response. Do not edit counters to resume service.

## Emergency provider disable

### Triggers

Disable Mapbox when any of the following occurs:

- projected complete invoice may exceed US$800
- unexplained provider usage or token abuse
- token restriction failure or suspected credential exposure
- provider terms, Privacy, Legal, or Security approval is withdrawn
- persistent provider outage or material WebGL failure
- CSP must be weakened to keep the renderer running
- repeated map initialization bypasses the intended one-map-per-page design
- operational data is observed leaving the EcoAegis origin

### Procedure

1. Record the trigger, UTC time, affected environment, deployed commit, and approving incident lead.
2. Set `MAP_ENGINE=leaflet` in the approved deployment environment.
3. Remove or disable the token in the deployment secret store if exposure or abuse is suspected.
4. Run the exact production restart command maintained by the deployment owner.
5. Verify `/health` returns HTTP 200.
6. Sign in and confirm `/map` renders `data-engine="leaflet"`.
7. Confirm no request is sent to Mapbox domains.
8. Confirm operational markers and coordinate administration remain available on the Leaflet or honest blank-map path.
9. Confirm no public OSM fallback was silently introduced.
10. Notify SHE operations, Security and Privacy, Finance, and the billing owner.
11. Preserve relevant logs, audit entries, billing screenshots, and change evidence without recording the token.

If the exact production restart command is still absent from this runbook, Mapbox must remain disabled. Do not improvise a production restart during an incident.

## Rollback verification

The rollback is complete only when all checks pass:

- `MAP_ENGINE=leaflet` is visible in the effective deployment environment
- `/health` is healthy
- authenticated `/map` loads
- page markup reports the Leaflet engine
- no Mapbox JavaScript, style, tile, event, or telemetry request occurs
- map APIs remain authenticated, organization-scoped, and private/no-store
- manual coordinate, GPS, and import controls remain authorized and usable
- provider budget denial cannot prevent operational data access
- the incident or change record contains the disable time, owner, evidence, and follow-up action

## Troubleshooting

| Symptom | Likely cause | Safe response |
|---|---|---|
| HTTP 409 from provider session | `MAP_ENGINE` is disabled | Confirm the intended engine. Do not force admission |
| HTTP 503, `Mapbox is not configured` | Public token absent | Keep continuity mode. Have the token owner fix the approved secret store |
| HTTP 429, denied | Monthly admission limit reached | Follow the stop-threshold procedure. Do not raise the limit |
| Basemap unsupported | WebGL 2 unavailable or major performance caveat | Use continuity mode or Leaflet rollback |
| Basemap degraded after start | Provider, network, style, or browser failure | Preserve operational data, review CSP/network logs, and disable if persistent |
| Operational data unavailable | EcoAegis API, database, authorization, or BBOX failure | Treat as an application incident. Provider rollback alone may not fix it |
| Counter differs from provider console | Provider statistics delay, shared usage, retries, or another product | Reconcile all account usage and lower the cap if unexplained |
| Repeated warning/critical audit event | Possible transition or counter defect | Stop enablement, preserve evidence, and run focused admission tests |
| Retention scheduler failure | Database or scheduler error | Do not manually purge. Restore scheduler health and rerun approved retention verification |
| Token appears in logs or source | Exposure | Disable Mapbox, revoke token, preserve evidence, rotate, and open a security incident |

## Escalation record template

Record the following in the approved incident or change system:

- UTC detection and response times
- environment and deployed commit
- local admitted-load count and state
- provider-console usage by product
- projected complete invoice and assumptions
- user-visible impact and continuity status
- token exposure status without recording the token
- commands run and exact results
- owners notified and decisions made
- rollback completion evidence
- corrective action and target date

## Change history

| Date | Change | Evidence |
|---|---|---|
| 2026-08-28 | Initial Phase 5 runbook, cost thresholds, retention, continuity, and emergency-disable procedure | Current Phase 5 local worktree and `docs/MAPBOX_PHASE5_ACCEPTANCE.md` |
