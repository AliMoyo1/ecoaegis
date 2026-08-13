"""Photo hazard classification for observations (guide A3).

Uses the vision-capable AI client and stores the structured result in the
observation's ai_metadata. The raw image is kept as an attachment so it can be
served by the generic attachment route.
"""
from __future__ import annotations

import base64
import mimetypes

from sheplatform.core.ai_client import ask_ai_vision
from sheplatform.modules.ai.service import _safe_json

_PROMPT = (
    "You are a safety, health and environment hazard classifier. "
    "Look at the uploaded photo and identify visible hazards, unsafe acts or "
    "unsafe conditions. Return ONLY a JSON object with the following keys, "
    "no markdown, no trailing prose:\n"
    "{\n"
    "  \"title\": \"short title of the observation\",\n"
    "  \"obs_type\": \"one of: hazard, near_miss, unsafe_act, unsafe_condition, good_practice\",\n"
    "  \"severity\": \"one of: low, medium, high, critical\",\n"
    "  \"description\": \"one or two sentences describing what the photo shows\",\n"
    "  \"controls\": [\"suggested control 1\", \"suggested control 2\"],\n"
    "  \"confidence\": 0.85\n"
    "}"
)


def _mime_from_filename(name: str) -> str:
    mt = mimetypes.guess_type(name or "")[0]
    return mt or "image/jpeg"


def _preview_data_uri(file_bytes: bytes, mime_type: str) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(file_bytes).decode()}"


async def classify_photo(file_bytes: bytes, filename: str = "photo.jpg") -> dict:
    """Return a structured hazard observation from a photo.

    The returned dict contains:
      - title, obs_type, severity, description, controls, confidence
      - raw_text: the raw model response
      - data_uri: a base64 data URI for previewing the image
    """
    mime_type = _mime_from_filename(filename)
    raw = await ask_ai_vision(
        _PROMPT, file_bytes, mime_type=mime_type, max_tokens=1500)
    parsed = _safe_json(raw)
    if not parsed:
        parsed = {
            "title": "Unclassified photo observation",
            "obs_type": "hazard",
            "severity": "medium",
            "description": raw[:500] if raw else "Could not parse AI response.",
            "controls": [],
            "confidence": 0.0,
        }
    allowed_obs = {"hazard", "near_miss", "unsafe_act", "unsafe_condition", "good_practice"}
    allowed_sev = {"low", "medium", "high", "critical"}
    if parsed.get("obs_type") not in allowed_obs:
        parsed["obs_type"] = "hazard"
    if parsed.get("severity") not in allowed_sev:
        parsed["severity"] = "medium"
    parsed["raw_text"] = raw
    parsed["data_uri"] = _preview_data_uri(file_bytes, mime_type)
    return parsed
