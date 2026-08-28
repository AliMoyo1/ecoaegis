# EcoAegis Mapbox Phase 5 acceptance record

## Release position

- Assessment date: 2026-08-28
- Reconciled source baseline: `79cc37a989660e91baf613b48551ae8896c2e038`
- Assessment source: the current local Phase 5 worktree based on that commit
- Current safe default: `MAP_ENGINE=leaflet`
- Production Mapbox status: not enabled
- Release decision: conditional. Local security, retention, concurrency, performance, and provider-free browser checks pass. Production enablement remains blocked by the external and environment-specific gates recorded below.

This record distinguishes verified behavior from planned work. A pass means the stated command or check completed against the stated environment. It does not imply that a production token, billing account, owner, PostgreSQL environment, or manual assistive-technology review exists.

## Evidence legend

| State | Meaning |
|---|---|
| Pass | Verified against the current local worktree with evidence recorded here |
| Pending | Required work that can be completed when the named environment or test setup is available |
| External blocker | Requires Corporate, Finance, Security, Privacy, or deployment-owner input |
| Not applicable | Outside the accepted production scope |

## Security and privacy acceptance

| Gate | State | Evidence |
|---|---|---|
| Authenticated map HTML and operational APIs use `Cache-Control: private, no-store` | Pass | `tests/test_map_phase5_security.py` verifies `/map`, manifest, and layer responses |
| Strict security headers and CSP remain active | Pass | Tests assert frame denial, MIME sniffing denial, strict default/object/frame/script-attribute policy, and no `unsafe-eval` |
| Role without map access receives no page, layer, or token | Pass | Negative HTTP test with the employee role |
| Missing organization fails closed | Pass | Simulated corrupt legacy session receives no page nonce, no token, and no operational features |
| Budget denial releases no token | Pass | HTTP test reaches the hard limit, receives HTTP 429, and confirms token absence |
| Warning, critical, and blocked transitions are tamper-evident and recorded once | Pass | Audit-chain test records `map.provider.warning`, `map.provider.critical`, and `map.provider.blocked` exactly once |
| Admission evidence retention is automated | Pass | Daily UTC scheduler at 03:20 retains only the current and previous UTC billing months |
| Application source contains no provider credential | Pass | Source scan covers Python, templates, first-party JavaScript, and `.env.example` |
| Operational GeoJSON remains same-origin | Pass | Static source assertion plus browser resource-origin assertion |
| Missing-token provider path makes no Mapbox request | Pass | Chrome acceptance run selected Mapbox with an empty token, reached fail-closed continuity mode, and observed zero Mapbox requests |
| Production token is minimum-scope and exact-origin restricted | External blocker | Corporate must create the non-default public token and record its allowed origins, scopes, owner, rotation date, and revocation procedure |
| Live admitted renderer exposes the token only after authenticated admission | Pending | Requires the restricted non-production public token. Do not substitute a personal or unrestricted token |
| PostgreSQL warning, critical, rollover, stop, and retention checks | Pending | Requires a disposable PostgreSQL test database through `TEST_DATABASE_URL` |

Focused security and map regression command:

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_map_phase5_security.py tests/test_map_phase5_benchmark.py tests/test_map_provider_admission.py tests/test_map_layers.py -q
```

Recorded result: 54 passed, 7 existing framework deprecation warnings, 18.08 seconds.

Complete application regression command:

```powershell
./.venv/Scripts/python.exe -m pytest -q
```

Recorded result: 518 passed, 10 expected backend-specific skips on the local SQLite run, and 30 existing deprecation warnings in 533.15 seconds. The skips are not PostgreSQL acceptance evidence; the disposable PostgreSQL Phase 5 run remains pending.

## Performance and concurrency acceptance

The release profile ran against an isolated SQLite database. It refused existing database paths, created 100,000 incident rows, placed 10,000 records in the representative BBOX, issued 50 viewport queries, and submitted 50 concurrent admission attempts against a limit of 25.

| Measurement | Result | Initial target | State |
|---|---:|---:|---|
| Layer query p50 | 128.114 ms | Informational | Pass |
| Layer query p95 | 187.664 ms | Below 750 ms | Pass |
| Maximum query | 189.406 ms | Informational | Pass |
| Maximum serialized payload | 624,433 bytes | Below 2,000,000 bytes | Pass |
| Maximum returned features | 2,000 | No more than configured limit | Pass |
| Truncation state | True | Must be explicit when more than 2,000 match | Pass |
| Current allocated-memory delta | 2,500,093 bytes | Record and monitor | Pass for evidence, not a browser leak test |
| Peak traced memory | 5,485,648 bytes | Record and monitor | Pass for evidence, not a browser leak test |
| Concurrent admissions | 25 admitted, 25 denied | Exactly the limit | Pass |
| Admission errors | 0 | 0 | Pass |
| Counter oversubscription | False | False | Pass |

Durable machine-readable evidence is stored in `docs/evidence/map_phase5_release_benchmark_2026-08-28.json`.

Release benchmark command:

```powershell
./.venv/Scripts/python.exe scripts/map_phase5_benchmark.py `
  --profile release `
  --output docs/evidence/map_phase5_release_benchmark_2026-08-28.json
```

Additional performance gates:

| Gate | State | Reason |
|---|---|---|
| PostgreSQL 16 indexed BBOX query plans | Pass, earlier evidence | `docs/MAP_QUERY_PLAN_EVIDENCE.md` records the isolated 20,000-row-per-table run from 2026-08-22 |
| Current Phase 5 PostgreSQL concurrency run | Pending | No disposable PostgreSQL instance was available in this local acceptance run |
| Facility-summary p95 below 500 ms | Pending | The release harness measures the incident layer, not facility-summary requests |
| Five and 25 concurrent browser users | Pending | Requires a production-like load environment and approved provider test account |
| Long-session browser heap stability | Pending | Server-side tracemalloc is recorded; browser heap growth after repeated style and layer activity still needs an admitted provider session |
| Slow-provider renderer behavior | Pending | Requires the restricted staging token or an approved deterministic provider stub |

## Browser and accessibility acceptance

The reproducible harness is `scripts/map_phase5_browser_acceptance.js`. It uses a newly seeded disposable SQLite database, local static assets, Mapbox selected, and no public token. This verifies the fail-closed path without provider traffic or cost.

Recorded environment:

- Google Chrome 152.0.7977.64
- Windows 11
- Desktop viewport 1600 by 1000
- Mobile viewport 390 by 844
- Light and dark themes
- `prefers-reduced-motion: reduce`
- forced-colors active

Verified checks:

- exactly one main landmark and one page-level heading
- no duplicate IDs
- visible images have alternative text
- visible form controls and buttons have accessible names
- skip link becomes visible and moves focus to main content
- keyboard focus progresses through distinct controls with visible indicators
- mobile navigation opens, closes with Escape, restores focus, and closes on navigation
- selected critical text meets the tested 4.5:1 normal-text and 3:1 large-text contrast thresholds in light and dark themes
- reduced motion pauses the decorative login video
- forced-colors mode retains the theme control and map navigation
- no horizontal overflow in tested desktop, mobile, or forced-colors states
- missing-token status is explicit, continuity records remain available, and no Mapbox canvas is constructed
- operational manifest and layer resources remain same-origin
- no unexpected response, page, console, CSP, or network error

Machine-readable browser evidence is stored in `docs/evidence/map_phase5_browser_acceptance_2026-08-28.json`. Screenshots were retained as local test artifacts and are intentionally not added to the product source tree.

Browser gates still pending:

| Gate | State | Reason |
|---|---|---|
| Live admitted Mapbox Standard renderer | Pending | Requires a restricted staging public token |
| Standard to Satellite style restoration | Pending | Requires an admitted renderer |
| Native cluster expansion and feature drawer | Pending | Requires an admitted renderer with representative layer data |
| WebGL/provider outage after renderer creation | Pending | Requires an admitted renderer or approved deterministic stub |
| Edge and Firefox matrix | Pending | Chrome is the only browser automated in this evidence run |
| NVDA or equivalent screen-reader walkthrough | Pending | Requires manual assistive-technology review by a named tester |

## Cost and operational acceptance

| Gate | State | Evidence or blocker |
|---|---|---|
| Local atomic monthly counter | Pass | Focused tests and 50-attempt concurrency benchmark |
| Warning at 150,000 | Pass in code and isolated tests | Threshold transition is recorded once |
| Critical escalation at 175,000 | Pass in code and isolated tests | Threshold transition is recorded once |
| Automatic stop at 180,000 | Pass in code and isolated tests | Admission fails closed before token release |
| Provider admission retention | Pass | Daily scheduler and retention test |
| Emergency provider-disable procedure | Pass as documented | See `docs/MAPBOX_OPERATIONS_RUNBOOK.md` |
| Exact production restart command and service owner | External blocker | Deployment owner has not recorded the production service wrapper |
| Billing account and payment owner | External blocker | Corporate or Finance must provide names and account reference |
| Technical token owner | External blocker | Must be named before any production token exists |
| Tax, support, other Mapbox products, and invoice-currency treatment | External blocker | Finance reconciliation is required to prove the complete invoice stays below US$800 |
| Provider-console reconciliation | External blocker | Named reviewer, cadence, evidence location, and account access are required |

## Production enablement decision

Do not set production `MAP_ENGINE=mapbox` until every item below is complete:

1. Corporate records the billing account, payment owner, technical token owner, Finance approver, and SHE operational owner.
2. Finance confirms tax, support, currency, and every other Mapbox product remain inside the US$800 complete-invoice ceiling.
3. Security creates a non-default public token with minimum scopes and exact production origins.
4. The disposable PostgreSQL suite and Phase 5 concurrency cases pass.
5. The admitted staging browser matrix passes Standard, Satellite, cluster, layer, drawer, provider-outage, and WebGL-failure scenarios.
6. Manual screen-reader and final keyboard review are signed by a named tester.
7. The deployment owner adds the exact restart, rollback, health-check, log, and escalation commands to the operations runbook.
8. An authorized release approver explicitly approves production enablement.

Until then, Leaflet remains the production-safe engine flag and Mapbox remains a tested but unadmitted provider path.
