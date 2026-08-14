"""Leading-indicator site scoring + grounded explanation (guide C3).

Moves from lagging counts (modules/benchmark: raw incident/observation
counts) to leading indicators: a near-miss *ratio*, overdue CAs, and a real
inspection pass rate. No ML - a weighted score from numbers the platform
already has, same "honest and explainable" bar benchmark.py already set,
just built from different, forward-looking signals.

Training gaps are deliberately NOT a scored component: training_needs has
no site attribution anywhere in this schema (org-wide only), so folding it
into a per-site score would mean inventing a linkage that isn't there -
same call as excluding risks from the C1 map.
"""
from __future__ import annotations

from sheplatform.core.ai_client import ask_ai


def _site_near_miss_ratio(db, site_name: str, org_id: int) -> tuple[float, int]:
    loc_like = f"%{site_name}%"
    total = db.execute(
        "SELECT COUNT(*) FROM incidents WHERE location LIKE %s AND org_id = %s",
        (loc_like, org_id)).fetchone()[0]
    if not total:
        return 0.0, 0
    near_miss = db.execute(
        "SELECT COUNT(*) FROM incidents WHERE location LIKE %s AND org_id = %s AND incident_type = 'near_miss'",
        (loc_like, org_id)).fetchone()[0]
    return near_miss / total, total


def _site_overdue_cas(db, site_name: str, org_id: int) -> int:
    loc_like = f"%{site_name}%"
    return db.execute(
        "SELECT COUNT(*) FROM corrective_actions ca JOIN incidents i ON "
        "ca.source_type = 'incident' AND ca.source_id = i.id "
        "WHERE i.location LIKE %s AND ca.status IN ('open','in_progress','overdue') "
        "AND ca.org_id = %s",
        (loc_like, org_id)).fetchone()[0]


def _site_inspection_pass_rate(db, site_name: str, org_id: int) -> float | None:
    loc_like = f"%{site_name}%"
    row = db.execute(
        "SELECT r.result, COUNT(*) AS n FROM inspection_results r "
        "JOIN inspections i ON i.id = r.inspection_id "
        "WHERE i.site_location LIKE %s AND i.org_id = %s AND r.result IN ('pass','fail') "
        "GROUP BY r.result", (loc_like, org_id)).fetchall()
    counts = {r["result"]: r["n"] for r in row}
    total = counts.get("pass", 0) + counts.get("fail", 0)
    if not total:
        return None  # no completed inspection checks - not the same as a clean record
    return round(counts.get("pass", 0) / total * 100, 1)


def per_site_scores(db, org_id: int | None) -> list[dict]:
    """Ranked worst-first. Fails closed: no org, no rows."""
    if not org_id:
        return []
    sites = db.execute(
        "SELECT id, site_code, site_name, site_type FROM sites "
        "WHERE org_id = %s AND status = 'active' ORDER BY site_name", (org_id,)).fetchall()

    out = []
    for s in sites:
        site = dict(s)
        near_miss_ratio, incident_count = _site_near_miss_ratio(db, site["site_name"], org_id)
        overdue_cas = _site_overdue_cas(db, site["site_name"], org_id)
        pass_rate = _site_inspection_pass_rate(db, site["site_name"], org_id)

        near_miss_component = round(near_miss_ratio * 10)  # 0-10
        inspection_risk = 10 if pass_rate is None else round((100 - pass_rate) / 10)  # 0-10; no data scores as high-risk

        score = near_miss_component * 3 + overdue_cas * 3 + inspection_risk * 2
        band = "Red" if score >= 20 else "Amber" if score >= 10 else "Green"

        out.append({
            **site,
            "incident_count": incident_count,
            "near_miss_ratio": round(near_miss_ratio, 2),
            "overdue_cas": overdue_cas,
            "inspection_pass_rate": pass_rate,
            "score": score,
            "band": band,
        })

    out.sort(key=lambda x: (-x["score"], x["site_name"]))
    for i, row in enumerate(out, start=1):
        row["rank"] = i
    return out


async def explain_site(db, site_id: int, org_id: int | None) -> dict:
    """Grounded, one-sentence rationale for why a site needs attention -
    strictly from the numbers already computed, nothing invented."""
    sites = per_site_scores(db, org_id)
    site = next((s for s in sites if s["id"] == site_id), None)
    if site is None:
        return {"ok": False, "message": "site not found or has no active-org data"}

    pass_rate_text = f"{site['inspection_pass_rate']}%" if site["inspection_pass_rate"] is not None else "no completed inspections on record"
    prompt = (
        "You are summarising a workplace-safety leading-indicator score for one site. "
        "Using ONLY the numbers below, write one short, plain sentence explaining why this "
        "site scored the way it did. Do not invent any fact not given here.\n\n"
        f"Site: {site['site_name']}\n"
        f"Near-miss ratio: {site['near_miss_ratio']} (near-misses / total incidents, {site['incident_count']} incidents on record)\n"
        f"Open/overdue corrective actions: {site['overdue_cas']}\n"
        f"Inspection pass rate: {pass_rate_text}\n"
        f"Overall band: {site['band']}\n"
    )
    explanation = await ask_ai(prompt, max_tokens=150)
    return {"ok": True, "site": site, "explanation": explanation.strip()}
