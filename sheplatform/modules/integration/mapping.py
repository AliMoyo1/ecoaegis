"""ThemisIQ integration: field mapping + sync threshold (spec Section 9, 5).

Pure functions - no network, unit-testable. All PII-free: only risk metadata.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

# SHE risk_category -> ThemisIQ category (spec 9.1)
CATEGORY_MAP = {
    "operational": "operational",
    "financial": "financial",
    "regulatory": "regulatory",
    "strategic": "strategic",
    # fallbacks for any SHE-specific categories
    "hse": "operational",
    "compliance": "regulatory",
    "legal": "regulatory",
    "reputational": "strategic",
}

# SHE status -> ThemisIQ status (spec 9.2)
STATUS_MAP = {
    "open": "open",
    "under_review": "open",
    "monitoring": "mitigated",
    "mitigated": "mitigated",
    "closed": "mitigated",
}

# SHE managerial_response -> ThemisIQ treatment (spec 9.3)
TREATMENT_MAP = {
    "accept": "accept",
    "mitigate": "mitigate",
    "transfer": "transfer",
    "avoid": "avoid",
    "reduce": "mitigate",
}

# Corporate materiality bar (spec Section 5)
CORPORATE_RESIDUAL_MIN = 12.0
CORPORATE_CATEGORIES = {"regulatory", "strategic"}


def map_category(she_category: str) -> str:
    return CATEGORY_MAP.get(she_category or "", "operational")


def map_status(she_status: str) -> str:
    return STATUS_MAP.get(she_status or "", "open")


def map_treatment(managerial_response: str | None) -> str:
    return TREATMENT_MAP.get((managerial_response or "").lower(), "mitigate")


def is_corporate(risk: dict) -> bool:
    """Sync threshold (spec Section 5): residual >= 12, regulatory/strategic, or manual flag."""
    if risk.get("corporate_flag") or risk.get("is_corporate"):
        return True
    if risk.get("risk_category") in CORPORATE_CATEGORIES:
        return True
    try:
        residual = float(risk.get("residual_score") or 0)
    except (TypeError, ValueError):
        residual = 0.0
    return residual >= CORPORATE_RESIDUAL_MIN


def _iso_or_none(value) -> str | None:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        return str(value)
    except Exception:
        return None


def _risk_narrative(risk: dict) -> str:
    """Sanitised hazard/impact narrative (spec 5: no PII crosses the boundary).

    Strips deterministic PII patterns (emails, phone numbers, national ID
    numbers). Free-text names cannot be stripped reliably without false
    positives, so the SHE data model keeps personal data out of the risk
    narrative fields at creation time.
    """
    text = str(risk.get("hazard_description") or "")[:800]
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[email]", text)
    text = re.sub(r"(\+?2?6?3)?\s?0?7[3-9]\s?\d{3}\s?\d{4}", "[phone]", text)
    text = re.sub(r"\b\d{2}-\d{6,7}[A-Z]\d{2}\b", "[id]", text)  # ZW national ID
    parts = [text]
    if risk.get("source_type"):
        parts.append(f"Source: {risk['source_type']}")
    return " | ".join(parts)[:1000]


def map_she_to_themis(risk: dict) -> dict:
    """Build the RiskCreate payload (spec 6.3). Pure, deterministic, PII-free."""
    residual = None
    ce = None
    try:
        residual = float(risk.get("residual_score")) if risk.get("residual_score") is not None else None
    except (TypeError, ValueError):
        pass
    try:
        inherent = float(risk.get("inherent_score")) if risk.get("inherent_score") is not None else None
        ce = risk.get("control_effectiveness")
    except (TypeError, ValueError):
        pass

    # Spec 6.4: residual detail travels in treatment_plan as a human-readable note
    treatment_plan_parts = []
    if risk.get("existing_controls"):
        treatment_plan_parts.append(f"Controls: {str(risk['existing_controls'])[:300]}")
    if ce is not None:
        treatment_plan_parts.append(f"Control effectiveness {ce}/5.")
    if residual is not None and inherent is not None:
        treatment_plan_parts.append(f"Residual score {residual} (inherent {inherent} / CE {ce}).")
    elif residual is not None:
        treatment_plan_parts.append(f"Residual score {residual}.")

    return {
        "title": str(risk.get("hazard_description", ""))[:500],
        "description": _risk_narrative(risk),
        "source_module": "SHE",
        "source_entity_type": "she_risk",
        "source_entity_id": int(risk["id"]),
        "category": map_category(risk.get("risk_category")),
        "likelihood": int(risk.get("likelihood") or 1),
        "impact": int(risk.get("impact") or 1),
        "treatment": map_treatment(risk.get("managerial_response")),
        "treatment_plan": " ".join(treatment_plan_parts).strip(),
        "status": map_status(risk.get("status")),
        "review_date": _iso_or_none(risk.get("review_date")),
    }


def hash_body(body: dict) -> str:
    """Canonical SHA-256 of the mapped payload (spec 8.3). Same input -> same hash."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_themis_origin(risk: dict) -> bool:
    """Loop guard (spec 8.1): never echo a ThemisIQ-origin risk back."""
    return (risk.get("origin_system") or "").lower() == "themisiq"
