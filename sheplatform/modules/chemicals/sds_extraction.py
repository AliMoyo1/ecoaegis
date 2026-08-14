"""SDS extraction service (guide B2).

Upload a Safety Data Sheet PDF, extract structured fields, and let the user
review before applying. Uses pdfplumber for digital PDFs and falls back to
vision AI for scanned/image PDFs.
"""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path

from sheplatform.config import settings
from sheplatform.core.ai_client import ask_ai, ask_ai_vision

logger = logging.getLogger("sheplatform.chemicals.sds")

SDS_PROMPT = """Extract structured safety-data-sheet fields from the text below.
Return valid JSON only, with no markdown formatting.
Use null for any field that is not present in the text. Do not invent values.

Fields:
- product_name (string)
- manufacturer (string)
- cas_number (string)
- ghs_signal_word (string: "Danger" or "Warning" or null)
- ghs_hazard_statements (list of strings, the H-codes and phrases, e.g. "H225: Highly flammable liquid and vapour")
- ghs_pictograms (list of strings, e.g. "flame", "corrosion", "skull")
- ppe_required (list of strings, e.g. "gloves", "goggles", "respirator")
- storage_conditions (string)
- first_aid_measures (string or list of strings)
- review_period_years (number or null)

SDS text:
"""

SDS_VISION_PROMPT = "Extract the same JSON fields from this SDS page image. Return valid JSON only."


def _pdf_bytes_to_images(pdf_bytes: bytes) -> list[tuple[bytes, str]]:
    """Render PDF pages to PNG bytes using pdf2image if available, else empty."""
    try:
        from pdf2image import convert_from_bytes  # type: ignore
    except Exception:
        logger.warning("pdf2image not available; cannot vision-process scanned PDF")
        return []

    try:
        pages = convert_from_bytes(pdf_bytes, dpi=150)
    except Exception as exc:
        logger.warning("pdf2image conversion failed: %s", exc)
        return []

    images = []
    for page in pages[:3]:  # first 3 pages usually enough for headers
        buf = io.BytesIO()
        page.save(buf, format="PNG")
        images.append((buf.getvalue(), "image/png"))
    return images


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    import pdfplumber

    text_parts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages[:8]:  # cap pages to keep token use sane
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""
            text_parts.append(page_text)
    return "\n\n".join(text_parts)


def _is_text_meaningful(text: str) -> bool:
    cleaned = " ".join(text.split())
    return len(cleaned) >= 50


def _extract_sections(text: str) -> str:
    """Pull likely relevant sections to reduce tokens."""
    markers = [
        "SECTION 1", "Section 1", "Identification",
        "SECTION 2", "Section 2", "Hazard",
        "SECTION 3", "Section 3", "Composition",
        "SECTION 4", "Section 4", "First aid",
        "SECTION 5", "Section 5", "Firefighting",
        "SECTION 6", "Section 6", "Accidental release",
        "SECTION 7", "Section 7", "Handling and storage",
        "SECTION 8", "Section 8", "Exposure controls",
    ]
    lower = text.lower()
    slices = []
    for i, marker in enumerate(markers):
        idx = lower.find(marker.lower())
        if idx == -1:
            continue
        end = len(text)
        if i + 1 < len(markers):
            nxt = lower.find(markers[i + 1].lower(), idx)
            if nxt != -1:
                end = nxt
        slices.append(text[idx:end])
    # If no section markers found, fall back to first 8000 chars
    if not slices:
        return text[:8000]
    return "\n\n".join(slices)[:12000]


async def extract_sds_from_pdf(pdf_bytes: bytes) -> dict:
    """Extract structured SDS data from PDF bytes. Returns a dict with a
    top-level 'raw_json' string and parsed 'fields'. Never raises.
    """
    try:
        text = _extract_text_from_pdf(pdf_bytes)
        source = "pdfplumber"

        if not _is_text_meaningful(text):
            logger.info("PDF text is sparse; trying vision fallback")
            source = "vision"
            images = _pdf_bytes_to_images(pdf_bytes)
            if not images:
                return {
                    "ok": False,
                    "source": "none",
                    "error": "No extractable text and pdf2image not installed; scanned SDS not readable",
                    "fields": {},
                    "raw_json": None,
                }
            vision_texts = []
            for img_bytes, mime in images:
                answer = await ask_ai_vision(
                    SDS_VISION_PROMPT, img_bytes, mime_type=mime,
                    system=SDS_PROMPT, max_tokens=2000)
                vision_texts.append(answer)
            text = "\n\n".join(vision_texts)
        else:
            text = _extract_sections(text)

        full_prompt = SDS_PROMPT + text
        raw_json = await ask_ai(full_prompt, system=None, max_tokens=2000)

        # Strip markdown fences if the model ignored instructions
        if raw_json.startswith("```"):
            raw_json = raw_json.strip("`").strip()
            if raw_json.lower().startswith("json"):
                raw_json = raw_json[4:].strip()

        try:
            fields = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.warning("SDS JSON parse failed: %s; raw: %s", exc, raw_json[:200])
            return {
                "ok": False,
                "source": source,
                "error": "AI response was not valid JSON",
                "fields": {},
                "raw_json": raw_json,
            }

        return {
            "ok": True,
            "source": source,
            "fields": fields,
            "raw_json": raw_json,
        }
    except Exception as exc:
        logger.exception("SDS extraction failed")
        return {
            "ok": False,
            "source": "error",
            "error": str(exc),
            "fields": {},
            "raw_json": None,
        }


def apply_sds_fields(chemical: dict, fields: dict) -> dict:
    """Map extracted fields onto a chemical record dict. Returns updates only.
    Does not write to the DB.
    """
    updates = {}

    product_name = fields.get("product_name") or fields.get("product")
    if product_name and not chemical.get("name"):
        updates["name"] = product_name

    manufacturer = fields.get("manufacturer") or fields.get("supplier")
    if manufacturer and not chemical.get("supplier"):
        updates["supplier"] = manufacturer

    cas = fields.get("cas_number")
    if cas and not chemical.get("cas_number"):
        updates["cas_number"] = cas

    hazard = fields.get("ghs_signal_word") or fields.get("signal_word")
    if hazard and not chemical.get("hazard_class"):
        # We intentionally leave hazard_class to human review because it drives emergency logic
        updates["hazard_class_hint"] = hazard

    ppe = fields.get("ppe_required") or []
    storage = fields.get("storage_conditions") or fields.get("storage")
    first_aid = fields.get("first_aid_measures")
    pictograms = fields.get("ghs_pictograms") or []

    extracted = {}
    if ppe:
        extracted["ppe_required"] = ppe if isinstance(ppe, list) else [ppe]
    if storage:
        extracted["storage_conditions"] = storage
    if first_aid:
        extracted["first_aid_measures"] = first_aid
    if pictograms:
        extracted["ghs_pictograms"] = pictograms if isinstance(pictograms, list) else [pictograms]

    if extracted:
        existing = json.loads(chemical.get("sds_extracted") or "{}")
        existing.update(extracted)
        updates["sds_extracted"] = existing

    return updates
