"""Document classification: keyword rules first, Gemini assist optional."""
import logging
from typing import Any

from app.config import get_settings
from app.models import DocumentCategory
from app.services import gemini

logger = logging.getLogger("resilient.classify")


def get_model_id() -> str | None:
    settings = get_settings()
    return settings.GEMINI_MODEL if settings.gemini_enabled else None


def classify_by_keywords(all_text: str, categories: list[DocumentCategory]) -> dict[str, Any]:
    lowered = all_text.lower()
    scores: dict[str, float] = {}
    best_code: str | None = None
    best_score = 0.0
    for category in categories:
        score = 0.0
        for kw in category.classification_keywords or []:
            kw_l = str(kw).lower()
            if kw_l and kw_l in lowered:
                weight = 2.0 if " " in kw_l else 1.0
                score += weight
        if category.expected_fields:
            present = sum(1 for f in category.expected_fields if str(f.get("name", "")).lower() in lowered)
            score += present * 0.6
        if category.is_infrastructure and category.code not in scores:
            pass
        scores[category.code] = score
        if score > best_score:
            best_score = score
            best_code = category.code
    if best_code and best_score <= 0.0:
        best_code = None
    confidence = min(0.95, best_score / 6.0) if best_code else 0.0
    return {
        "predicted_category": best_code,
        "confidence": round(confidence, 4),
        "method": "keyword_rules",
        "model_id": None,
        "scores": {k: round(v, 2) for k, v in scores.items() if v > 0},
        "rationale": None,
    }


def classify_document(
    all_text: str,
    categories: list[DocumentCategory],
    declared_code: str | None,
) -> dict[str, Any]:
    """Precedence: keyword rules > gemini assist > declared > unknown(None)."""
    rule_result = classify_by_keywords(all_text, categories)
    gemini_result = gemini.classify_document(
        all_text, [{"code": c.code, "name": c.name, "description": c.description} for c in categories]
    )

    if gemini_result and gemini_result["predicted_category"]:
        g_conf = 0.5 + min(0.45, gemini_result["confidence"])
        if rule_result["predicted_category"] is None or rule_result["confidence"] < 0.3 or g_conf >= rule_result["confidence"]:
            combined = dict(rule_result)
            combined.update(
                {
                    "predicted_category": gemini_result["predicted_category"],
                    "confidence": round(g_conf, 4),
                    "method": "gemini+keyword_rules",
                    "model_id": get_model_id(),
                    "scores": {**rule_result["scores"], **({"gemini": g_conf} if g_conf else {})},
                    "rationale": gemini_result.get("rationale"),
                }
            )
            return combined

    if rule_result["predicted_category"]:
        return rule_result

    if declared_code:
        return {
            "predicted_category": declared_code,
            "confidence": 0.4,
            "method": "declared",
            "model_id": None,
            "scores": rule_result["scores"],
            "rationale": "No text signals matched; using institution-declared category",
        }
    return rule_result
