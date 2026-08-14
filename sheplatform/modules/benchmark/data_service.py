"""Site benchmarking service (competitor benchmark gap #8).

Compares sites on SHE performance: incidents, observations, inspections,
corrective actions, compliance status. Ranks sites so management can see
which locations need attention (like Mitti's 5-star benchmarking).
"""
from __future__ import annotations

from datetime import datetime, timezone


def site_benchmark(db, org_id: int | None = None) -> list[dict]:
    """Per-site metrics with a composite rank. Sorted worst-first (most incidents)."""
    if not org_id:
        return []  # fail closed: no tenant scope -> no data (audit S5)
    sites = [dict(r) for r in db.execute(
        "SELECT id, site_code, site_name, city, site_type FROM sites "
        "WHERE status = 'active' AND org_id = %s ORDER BY site_name",
        (org_id,)).fetchall()]

    out = []
    for s in sites:
        # match on free-text location too (incidents store location text)
        loc_like = f"%{s['site_name']}%"
        incident_count = db.execute(
            "SELECT COUNT(*) FROM incidents WHERE (location LIKE %s OR %s = '') "
            "AND status != 'closed' AND org_id = %s",
            (loc_like, loc_like, org_id)).fetchone()[0]
        observation_count = db.execute(
            "SELECT COUNT(*) FROM observations WHERE (location LIKE %s OR %s = '') "
            "AND status NOT IN ('closed') AND org_id = %s",
            (loc_like, loc_like, org_id)).fetchone()[0]
        inspection_count = db.execute(
            "SELECT COUNT(*) FROM inspections WHERE (site_location LIKE %s OR %s = '') "
            "AND status = 'overdue' AND org_id = %s",
            (loc_like, loc_like, org_id)).fetchone()[0]
        ca_count = db.execute(
            "SELECT COUNT(*) FROM corrective_actions ca JOIN incidents i ON "
            "ca.source_type = 'incident' AND ca.source_id = i.id "
            "WHERE i.location LIKE %s AND ca.status IN ('open','in_progress','overdue') "
            "AND ca.org_id = %s",
            (loc_like, org_id)).fetchone()[0]
        chem_count = db.execute(
            "SELECT COUNT(*) FROM chemicals WHERE site_id = %s AND org_id = %s",
            (s["id"], org_id)).fetchone()[0]

        # composite: incidents + observations weighted, minus completed inspections
        score = incident_count * 3 + observation_count * 2 + inspection_count * 2 + ca_count
        out.append({
            **s,
            "incidents": incident_count,
            "observations": observation_count,
            "overdue_inspections": inspection_count,
            "open_cas": ca_count,
            "chemicals": chem_count,
            "score": score,
        })

    out.sort(key=lambda x: (-x["score"], x["site_name"]))
    for i, row in enumerate(out, start=1):
        row["rank"] = i
        if row["score"] >= 6:
            row["band"] = "Red"
        elif row["score"] >= 3:
            row["band"] = "Amber"
        else:
            row["band"] = "Green"
    return out


def benchmark_summary(db, org_id: int | None = None) -> dict:
    rows = site_benchmark(db, org_id)
    return {
        "sites": rows,
        "total_sites": len(rows),
        "red_sites": sum(1 for r in rows if r["band"] == "Red"),
        "amber_sites": sum(1 for r in rows if r["band"] == "Amber"),
        "green_sites": sum(1 for r in rows if r["band"] == "Green"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
