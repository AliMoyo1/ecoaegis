# EcoAegis map query-plan evidence

## Verification context

- Date: 2026-08-22
- Database: PostgreSQL 16 in an isolated local test container
- Dataset: 20,000 synthetic sites, 20,000 synthetic incidents, and 20,000 synthetic permits in one organization
- BBOX: longitude 30 to 32 and latitude -19 to -16
- Request limit: 500, queried as 501 for truncation detection
- Commands used `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)` after `ANALYZE`

No production data or credentials were used.

## Facilities

The facilities query used `idx_sites_org_lat_lng` with the organization and all four coordinate bounds in the index condition.

- Returned: 6 rows
- Execution time: 0.504 ms
- Plan shape: index scan, small in-memory sort, limit

## Incidents

The first query shape used one `CASE` expression for direct coordinates and linked-site fallback. PostgreSQL scanned 20,001 incidents and 20,000 sites, removed 19,994 rows after the join, and completed in 17.940 ms.

The builder was changed to two explicit branches:

1. incidents with a complete direct coordinate pair
2. incidents missing either coordinate, using an organization-matched site fallback

The revised query used all three initial map indexes:

- `idx_incidents_org_lat_lng` for direct incident points
- `idx_sites_org_lat_lng` for candidate fallback sites
- `idx_incidents_org_site` for fallback incidents linked to those sites

The revised plan returned 7 rows in 0.497 ms. It avoided the full-table scan and reduced shared-buffer hits from 690 to 39 in this representative run.

## Permits and linked-source index decision

Before a linked-source index, the permit plan used `idx_sites_org_lat_lng` to find six candidate sites but sequentially scanned all 20,000 permits. It returned 6 rows in 5.906 ms. The permit scan itself took about 3.624 ms.

This is representative of the same organization-plus-site relationship used by inspections, environmental projects, emergency events, assets, and observations. The following indexes were therefore admitted after evidence:

- `idx_permits_org_site`
- `idx_inspections_org_site`
- `idx_eia_projects_org_site`
- `idx_emergency_events_org_site`
- `idx_assets_org_site`
- `idx_observations_org_site`

After `idx_permits_org_site` was added, the same permit query changed to two index scans with a nested loop. Execution time fell from 5.906 ms to 0.320 ms in the representative run.

These indexes are created after retrofit columns during `init_db()`, so upgrades from databases that predate `site_id` or coordinates remain valid.

## Current conclusion

The evidence supports the BBOX index and incident query shape. It also supports organization-plus-site indexes for the six tables with the same linked-source access pattern. No broader speculative indexes were added for contractor inductions, CAPA, or risks.
