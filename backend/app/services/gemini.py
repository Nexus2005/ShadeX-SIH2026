"""Gemini API client (structured-output assist). Fails gracefully and never required."""
import json
import logging
import re
import time
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger("resilient.gemini")

_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiUnavailable(Exception):
    pass


_cache: dict[str, Any] = {}


def _client() -> httpx.Client:
    settings = get_settings()
    return httpx.Client(
        timeout=settings.GEMINI_TIMEOUT_SECONDS,
        headers={"x-goog-api-key": settings.GEMINI_API_KEY},
    )


def generate_json(prompt: str, schema: dict[str, Any] | None = None, max_retries: int = 2) -> dict[str, Any]:
    """Call generateContent with responseMimeType=application/json. Raises GeminiUnavailable on failure."""
    settings = get_settings()
    if not settings.gemini_enabled:
        raise GeminiUnavailable("GEMINI_API_KEY not configured")

    body: dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json",
        },
    }
    if schema:
        body["generationConfig"]["responseSchema"] = schema

    url = f"{_API_BASE}/models/{settings.GEMINI_MODEL}:generateContent"
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with _client() as client:
                resp = client.post(url, json=body)
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = GeminiUnavailable(f"Gemini HTTP {resp.status_code}")
            else:
                resp.raise_for_status()
                data = resp.json()
                content = data["candidates"][0]["content"]["parts"][-1]["text"]
                return json.loads(_strip_fence(content))
        except GeminiUnavailable:
            pass
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
        if attempt < max_retries:
            time.sleep(1.5 * (attempt + 1))
    raise GeminiUnavailable(str(last_error))


def _strip_fence(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    return match.group(1) if match else text


def classify_document(text: str, categories: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Returns {predicted_category, confidence, rationale, scores} or None on failure."""
    settings = get_settings()
    if not settings.gemini_enabled or not text.strip():
        return None
    catalog = "\n".join(
        f"- code={c['code']} name={c['name']}" + (f" description={c.get('description')}" if c.get("description") else "")
        for c in categories
    )
    prompt = (
        "You are classifying an institutional regulatory document for AICTE scrutiny. "
        "Choose exactly one category code from the catalog. Respond with JSON: "
        '{"predicted_category": "<code|null>", "confidence": 0.0-1.0, "rationale": "<=120 chars", '
        '"scores": {} optional per-category scores}.\n\n'
        f"CATALOG:\n{catalog}\n\nDOCUMENT TEXT (first 6000 chars):\n{text[:6000]}"
    )
    try:
        result = generate_json(prompt)
        code = result.get("predicted_category")
        if code and not any(c["code"] == code for c in categories):
            code = None
        payload = {
            "predicted_category": code,
            "confidence": float(result.get("confidence") or 0.0),
            "rationale": result.get("rationale", ""),
            "scores": result.get("scores", {}),
        }
        return payload
    except GeminiUnavailable as exc:
        logger.info("gemini classify unavailable: %s", exc)
        return None


def normalize_field(label: str, value_type: str, page_text: str) -> dict[str, Any] | None:
    """Structured extraction help for one expected field. Returns {value, confidence, rationale} or None."""
    settings = get_settings()
    if not settings.gemini_enabled or not page_text.strip():
        return None
    prompt = (
        f"From the document text below, find the value for the field '{label}' (type: {value_type}). "
        "Rules: use only verbatim values present in the text; for dates return YYYY-MM-DD; "
        "for numbers return digits only; never guess if absent.\n"
        'Respond JSON: {"found": true|false, "value": "<verbatim|"">", "confidence": 0.0-1.0, '
        '"evidence": "<the surrounding line>" }\n\n'
        f"TEXT:\n{page_text[:5000]}"
    )
    try:
        return generate_json(prompt)
    except GeminiUnavailable as exc:
        logger.info("gemini field extraction unavailable: %s", exc)
        return None


def summarize_findings(application_title: str, findings: list[dict[str, Any]]) -> str | None:
    if not findings:
        return None
    lines = "\n".join(f"- [{f.get('severity')}] {f.get('title')}" for f in findings[:25])
    prompt = (
        "Write a neutral, factual 4-sentence executive summary for a regulatory reviewer based only on "
        f"the findings listed. Application: {application_title}.\n{lines}\n"
        "Do not accuse anyone of fraud; use 'potential indicators' language."
    )
    try:
        with _client() as client:
            resp = client.post(
                f"{_API_BASE}/models/{get_settings().GEMINI_MODEL}:generateContent",
                json={"contents": [{"parts": [{"text": prompt}]}]},
            )
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][-1]["text"].strip()
    except Exception as exc:  # noqa: BLE001
        logger.info("gemini summary unavailable: %s", exc)
        return None
