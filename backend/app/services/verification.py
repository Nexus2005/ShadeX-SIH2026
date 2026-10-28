"""Verification adapters. This instance ships ONLY simulated providers (clearly labelled).

Real DigiLocker/issuer integrations require authorized access — the adapter interface
is kept separate so an authorized connector can be added without touching the engine.
"""
import logging
import time
import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Application,
    Document,
    ExtractedField,
    ReferenceRecord,
    VerificationAttempt,
    VerificationProvider,
)
from app.security import CurrentUser

logger = logging.getLogger("resilient.verification")

# What to compare, per reference record_type: (reference-data key, extracted field name).
# Extracted names must match the document category's expected_fields, otherwise the
# comparison silently degrades into comparing the reference with itself.
COMPARE_MAP: dict[str, list[tuple[str, str]]] = {
    "fire_noc": [("noc_number", "noc_number"), ("issued_on", "issued_on"), ("valid_to", "valid_until"), ("institution_name", "institution_name")],
    "aicte_record": [("aicte_pid", "aicte_pid"), ("institution_name", "institution_name")],
    "accreditation": [("accreditation_no", "document_no"), ("level", "level"), ("valid_to", "valid_until")],
    "land_record": [("survey_no", "survey_no"), ("owner_name", "owner_name"), ("area_sq_m", "area_sq_m")],
    "affiliation": [("affiliation_no", "document_no"), ("institution_name", "institution_name"), ("valid_to", "valid_until")],
}

# Legacy record types written by earlier seeds of this prototype.
_LEGACY_RECORD_TYPES = {"reference": "fire_noc", "institution": "aicte_record"}


def eligible_providers(db: Session, document_category: str | None) -> list[VerificationProvider]:
    providers = db.scalars(
        select(VerificationProvider).where(VerificationProvider.is_enabled.is_(True))
    ).all()
    result = []
    for p in providers:
        if p.availability not in ("available", "not_configured"):
            continue
        if not p.supported_document_categories or (document_category and document_category in p.supported_document_categories):
            result.append(p)
    fallback = [p for p in result if document_category is None or document_category in (p.supported_document_categories or [])]
    return fallback or result


def _active_extracted(db: Session, document: Document) -> dict[str, ExtractedField]:
    rows = db.scalars(
        select(ExtractedField).where(
            ExtractedField.document_id == document.id,
            ExtractedField.is_current.is_(True),
        )
    ).all()
    return {r.field_name: (r.corrected_value or r.normalized_value or r.raw_value) for r in rows if (r.corrected_value or r.normalized_value or r.raw_value)}


def _find_reference(db: Session, provider: VerificationProvider, reference_no: str | None, application: Application):
    stmt = select(ReferenceRecord).where(ReferenceRecord.provider_code == provider.code)
    if reference_no:
        found = db.scalars(stmt.where(ReferenceRecord.reference_no == reference_no)).first()
        if found:
            return found
    if application:
        candidates = db.scalars(
            stmt.where(ReferenceRecord.institution_id == application.institution_id)
        ).all()
        if reference_no is None:
            return candidates[0] if candidates else None
        for candidate in candidates:
            if str(candidate.data.get("noc_number", "")) == reference_no:
                return candidate
    return None


def verify_document(
    db: Session,
    application: Application,
    document: Document,
    provider: VerificationProvider,
    actor: CurrentUser | None,
) -> VerificationAttempt:
    started = time.perf_counter()
    extracted = _active_extracted(db, document)
    reference_no = extracted.get("document_no") or extracted.get("noc_number") or extracted.get("affiliation_no") or extracted.get("accreditation_no")
    request_summary: dict[str, Any] = {
        "provider_code": provider.code,
        "provider_type": provider.provider_type,
        "document_id": str(document.id),
        "reference_no": reference_no,
    }
    status = "manual_review_required"
    response: dict[str, Any] = {}
    compared: list[dict[str, Any]] = []

    if provider.availability != "available":
        status = "unavailable" if provider.availability != "not_configured" else "not_supported"
        response = {"detail": f"Provider '{provider.code}' is {provider.availability.replace('_', ' ')}; no verification attempt was made against the source."}
    else:
        reference = _find_reference(db, provider, reference_no, application)
        if reference is None:
            status = "manual_review_required"
            response = {"detail": "No synthetic reference record matched; the simulated provider could not confirm the document."}
        else:
            reference_type = _LEGACY_RECORD_TYPES.get(reference.record_type, reference.record_type)
            reference_valid_to = reference.valid_to or _as_date(reference.data.get("valid_to"))
            expired = bool(reference_valid_to and reference_valid_to < date.today())
            if expired:
                status = "expired"
                response = {
                    "detail": "Reference record shows the instrument was valid but has lapsed.",
                    "reference": reference.data,
                    "valid_to": reference_valid_to.isoformat(),
                }
            else:
                compare_map = COMPARE_MAP.get(reference_type)
                if not compare_map:
                    status = "not_supported"
                    response = {
                        "detail": f"No comparison rules are defined for record type '{reference.record_type}'.",
                        "reference": reference.data,
                    }
                else:
                    mismatches: list[str] = []
                    not_comparable: list[str] = []
                    for ref_key, field_name in compare_map:
                        ref_value = reference.data.get(ref_key)
                        doc_value = extracted.get(field_name)
                        if ref_value is None:
                            continue
                        if doc_value is None:
                            # Missing extraction is not evidence of a mismatch, but it is also
                            # not a pass: the reviewer gets an explicit inconclusive outcome.
                            not_comparable.append(field_name)
                            compared.append(
                                {
                                    "field": field_name,
                                    "reference_value": str(ref_value),
                                    "document_value": None,
                                    "match": None,
                                }
                            )
                            continue
                        match = _same(ref_value, doc_value)
                        compared.append(
                            {
                                "field": field_name,
                                "reference_value": str(ref_value),
                                "document_value": str(doc_value),
                                "match": match,
                            }
                        )
                        if not match:
                            mismatches.append(field_name)
                    if mismatches:
                        status = "mismatch"
                    elif not_comparable:
                        status = "manual_review_required"
                    else:
                        status = "verified"
                    response = {
                        "detail": "Simulated issuing-authority response for demonstration (not a live government source).",
                        "reference": reference.data,
                        "field_comparisons": compared,
                        "mismatched_fields": mismatches,
                        "not_comparable_fields": not_comparable,
                    }
    latency_ms = int((time.perf_counter() - started) * 1000) + 40
    attempt = VerificationAttempt(
        application_id=application.id,
        document_id=document.id,
        provider_code=provider.code,
        reference_no=reference_no,
        status=status,
        is_simulated=provider.provider_type == "simulated",
        request_summary=request_summary,
        response_payload=response,
        compared_fields=compared,
        latency_ms=latency_ms,
        requested_by=actor.id if actor else None,
        responded_at=None,
    )
    db.add(attempt)
    db.flush()
    attempt.responded_at = attempt.requested_at
    return attempt


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    sa = str(a).strip().upper()
    sb = str(b).strip().upper()
    if sa == sb:
        return True
    date_a, date_b = _as_date(sa), _as_date(sb)
    if date_a is not None and date_b is not None:
        return date_a == date_b
    try:
        return float(sa.replace(",", "")) == float(sb.replace(",", ""))
    except ValueError:
        return False


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    from datetime import datetime

    text = str(value).strip().replace("/", "-").replace(".", "-")[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def run_application_verification(
    db: Session, application: Application, actor: CurrentUser | None
) -> list[VerificationAttempt]:
    documents = db.scalars(
        select(Document).where(
            Document.application_id == application.id,
            Document.is_deleted.is_(False),
            Document.status == "processed",
        )
    ).all()
    attempts: list[VerificationAttempt] = []
    for document in documents:
        category = document.detected_category_code or document.declared_category_code
        providers = eligible_providers(db, category)
        if not providers:
            continue
        chosen = providers[0]
        attempts.append(verify_document(db, application, document, chosen, actor))
        for remaining in providers[1:1]:
            attempts.append(verify_document(db, application, document, remaining, actor))
    return attempts
