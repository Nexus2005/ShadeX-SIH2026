"""Deterministic, versioned rule engine.

Rule definitions are JSON stored in rule_versions.definition:
  {"evaluator": "<type>", ...params...}

Outcomes: pass | fail | needs_review | not_applicable | unable_to_evaluate.
No LLM participates in rule outcomes. Errors inside an evaluator become
unable_to_evaluate with the error surfaced — never a silent pass.
"""
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser as dtparser

from sqlalchemy import select

from app.models import (
    Application,
    Document,
    ExtractedField,
    Finding,
    RuleEvaluation,
    RuleVersion,
    ValidationRun,
)
from app.services import verification

logger = logging.getLogger("resilient.rule_engine")

IST = ZoneInfo("Asia/Kolkata")


def _as_of() -> date:
    return datetime.now(IST).date()


@dataclass
class EvalContext:
    application: Application
    as_of: date
    session: Any = None
    documents: list[Document] = field(default_factory=list)
    active_fields: list[ExtractedField] = field(default_factory=list)
    fields_by_document: dict[str, list[ExtractedField]] = field(default_factory=dict)


@dataclass
class RuleOutcome:
    outcome: str
    explanation: str
    inputs: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    finding: dict[str, Any] | None = None


# ---------------------------------------------------------------- helpers

def _value(ctx: EvalContext, field_name: str) -> tuple[str | None, ExtractedField | None]:
    for f in ctx.active_fields:
        if f.field_name == field_name:
            chosen = f.corrected_value if f.corrected_value else f.normalized_value
            return (chosen if chosen is not None else f.raw_value), f
    declared = ctx.application.declared_data or {}
    if field_name in declared and declared[field_name] not in (None, ""):
        return str(declared[field_name]), None
    return None, None


def _doc_fields(ctx: EvalContext, document: Document) -> dict[str, ExtractedField]:
    return {
        f.field_name: f for f in ctx.fields_by_document.get(str(document.id), []) if f.is_current
    }


def _ev_doc(document_id, version_id=None, page=None, field_row=None, role="primary", label=None) -> dict[str, Any]:
    ev = {
        "document_id": str(document_id) if document_id else None,
        "document_version_id": str(version_id) if version_id else None,
        "page_no": page,
        "extracted_field_id": str(field_row.id) if field_row is not None else None,
        "role": role,
        "label": label,
    }
    return {k: v for k, v in ev.items() if v is not None}


def _ev_field(field_row: ExtractedField, role="primary") -> dict[str, Any]:
    return _ev_doc(field_row.document_id, field_row.document_version_id, field_row.page_no, field_row, role)


def _coerce_date(value: str) -> date | None:
    cleaned = re.sub(r"[^0-9A-Za-z:/\- ]", " ", str(value)).strip()
    if not cleaned:
        return None
    parsed = dtparser.parse(cleaned, dayfirst=True, fuzzy=True)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(IST).replace(tzinfo=None)
    return parsed.date()


def _numeric(value: str) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("₹", "").strip())
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------- evaluators

def _eval_application_field_min(ctx: EvalContext, rv: RuleVersion) -> RuleOutcome:
    d = rv.definition
    field_name = d.get("field_name")
    minimum = d.get("min_value")
    value, source = _value(ctx, field_name)
    if value is None:
        return RuleOutcome(
            "unable_to_evaluate",
            f"Required field '{field_name}' was not present in the application data or extracted document fields.",
            inputs={"field": field_name, "min": minimum},
        )
    numeric = _numeric(value)
    if numeric is None:
        return RuleOutcome(
            "needs_review",
            f"Field '{field_name}' value '{value}' is not numeric; manual verification required.",
            inputs={"field": field_name, "value": str(value)},
            evidence=[_ev_field(source)] if source else [],
        )
    if numeric >= float(minimum):
        return RuleOutcome(
            "pass",
            f"{field_name}={numeric:g} meets the minimum of {minimum:g}.",
            inputs={"field": field_name, "value": numeric, "min": minimum},
            evidence=[_ev_field(source)] if source else [],
        )
    return RuleOutcome(
        "fail",
        f"{field_name}={numeric:g} is below the required minimum of {minimum:g}.",
        inputs={"field": field_name, "value": numeric, "min": minimum},
        evidence=[_ev_field(source)] if source else [],
    )


def _eval_document_expiry(ctx: EvalContext, rv: RuleVersion) -> RuleOutcome:
    d = rv.definition
    doc_cat = d.get("document_category")
    expiry_field_name = d.get("expiry_field", "valid_until")
    grace_days = int(d.get("grace_days", 0))
    candidates = [
        doc
        for doc in ctx.documents
        if (doc.detected_category_code == doc_cat or doc.declared_category_code == doc_cat) and not doc.is_deleted
    ]
    inputs = {"document_category": doc_cat, "grace_days": grace_days, "submitted": len(candidates)}
    if not candidates:
        return RuleOutcome(
            "unable_to_evaluate",
            f"No document of category '{doc_cat}' was submitted, so expiry cannot be evaluated.",
            inputs=inputs,
        )
    expired: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    uncertain = 0
    for doc in candidates:
        fields = _doc_fields(ctx, doc)
        frow = fields.get(expiry_field_name)
        value = None
        if frow:
            value = frow.corrected_value or frow.normalized_value or frow.raw_value
        try:
            expiry = _coerce_date(value) if value else None
        except (ValueError, ValueError, OverflowError):
            expiry = None
        if expiry is None:
            uncertain += 1
            continue
        if ctx.as_of > expiry and (ctx.as_of - expiry).days > grace_days:
            expired.append({"document": str(doc.id), "expired_on": str(expiry), "days_late": (ctx.as_of - expiry).days})
            evidence.append(_ev_field(frow))
    if expired:
        return RuleOutcome(
            "fail",
            f"{len(expired)} of {len(candidates)} '{doc_cat}' document(s) expired before the evaluation date.",
            inputs={**inputs, "expired": expired, "uncertain_dates": uncertain},
            evidence=evidence,
            finding={
                "finding_code": "DOC_EXPIRED",
                "category": "expiry",
                "title": f"Expired {doc_cat} document",
                "description": "A submitted certificate expired before the evaluation date according to its own expiry field.",
                "expected_value": "Valid as of evaluation date",
                "observed_value": ", ".join(sorted({e["expired_on"] for e in expired})),
                "recommended_action": "Request a renewed certificate; may continue in pending review.",
            },
        )
    return RuleOutcome(
        "pass",
        f"No '{doc_cat}' document is expired "
        + (f"(dates uncertain on {uncertain} document(s)); " if uncertain else "")
        + "per the configured expiry rule.",
        inputs=inputs,
        evidence=evidence,
    )


def _eval_required_document(ctx: EvalContext, rv: RuleVersion) -> RuleOutcome:
    d = rv.definition
    doc_cat = d.get("document_category")
    present = [
        doc
        for doc in ctx.documents
        if (doc.detected_category_code == doc_cat or doc.declared_category_code == doc_cat)
        and not doc.is_deleted
        and doc.status not in ("failed", "unreadable")
    ]
    if present:
        return RuleOutcome(
            "pass",
            f"Required document '{doc_cat}' is present ({len(present)} file(s) submitted).",
            inputs={"document_category": doc_cat, "count": len(present)},
            evidence=[_ev_doc(p.id, p.current_version_id) for p in present],
        )
    return RuleOutcome(
        "fail",
        f"Required document '{doc_cat}' is missing.",
        inputs={"document_category": doc_cat, "count": 0},
        evidence=[],
        finding={
            "finding_code": "DOC_MISSING",
            "category": "completeness",
            "title": f"Missing required document: {doc_cat}",
            "description": "The applicable checklist requires this document category, but none was submitted.",
            "expected_value": doc_cat,
            "observed_value": "not submitted",
            "recommended_action": "Rectification window: institution uploads the document, or reviewer requests clarification.",
        },
    )


def _eval_identifier_match(ctx: EvalContext, rv: RuleVersion) -> RuleOutcome:
    d = rv.definition
    ident_field = d.get("identifier_field", "aicte_pid")
    values: list[tuple[str, str, ExtractedField | None]] = []
    for f in ctx.active_fields:
        if f.field_name == ident_field:
            chosen = f.corrected_value or f.normalized_value or f.raw_value
            if chosen:
                values.append((str(chosen).strip().upper(), "extracted", f))
    declared = ctx.application.declared_data or {}
    if explic := str(declared.get(ident_field, "") or "").strip().upper():
        values.append((explic, "declared", None))
    inputs = {"identifier_field": ident_field, "observations": [{"value": v, "source": s} for v, s, _ in values]}
    distinct = sorted({v for v, _, _ in values})
    if len(distinct) > 1:
        return RuleOutcome(
            "fail",
            f"Identifier '{ident_field}' has conflicting values: {' vs '.join(distinct)}.",
            inputs=inputs,
            evidence=[_ev_field(f) for _, _, f in values if f is not None],
            finding={
                "finding_code": "IDENTIFIER_CONFLICT",
                "category": "consistency",
                "title": f"Conflicting {ident_field} across sources",
                "description": "The identifier appears with different values in different sources; critical identifiers require an exact match.",
                "expected_value": distinct[0],
                "observed_value": ", ".join(distinct),
                "recommended_action": "Request a corrected document or verify the discrepancy manually.",
            },
        )
    if not distinct:
        return RuleOutcome(
            "unable_to_evaluate",
            f"Identifier '{ident_field}' was not found in any source.",
            inputs=inputs,
        )
    return RuleOutcome(
        "pass",
        f"Identifier '{ident_field}' agrees across all sources where present.",
        inputs=inputs,
        evidence=[_ev_field(f) for _, _, f in values if f is not None],
    )


def _eval_verification_status(ctx: EvalContext, rv: RuleVersion) -> RuleOutcome:
    from sqlalchemy import select

    from app.models import VerificationAttempt

    d = rv.definition
    provider_code = d.get("provider_code")
    rows = (
        ctx.session.scalars(
            select(VerificationAttempt)
            .where(VerificationAttempt.application_id == ctx.application.id)
            .order_by(VerificationAttempt.requested_at.desc())
        ).all()
        if ctx.session is not None
        else []
    )
    relevant = [a for a in rows if provider_code in (None, a.provider_code)]
    inputs = {"provider_code": provider_code, "attempts": len(relevant)}
    if not relevant:
        return RuleOutcome(
            "needs_review",
            "No verification attempt recorded yet for this application; run source verification.",
            inputs=inputs,
        )
    bad = [a for a in relevant if a.status in ("mismatch", "expired")]
    inconclusive = [a for a in relevant if a.status in ("unavailable", "not_supported", "manual_review_required", "pending")]
    if bad:
        first = bad[0]
        return RuleOutcome(
            "fail",
            f"Source verification returned '{first.status}' (provider: {first.provider_code}, simulated: {first.is_simulated}).",
            inputs=inputs,
            evidence=[{"document_id": str(first.document_id) if first.document_id else None, "role": "reference", "label": "verification attempt"}],
            finding={
                "finding_code": "VERIFICATION_MISMATCH",
                "category": "verification",
                "title": "Issuing-source verification mismatch",
                "description": "Details returned by the issuing source disagree with the submitted document. Simulated providers are clearly labelled.",
                "expected_value": "verified",
                "observed_value": first.status,
                "recommended_action": "Compare the recorded source response with the document; route for manual confirmation.",
                "is_simulated": True,
            },
        )
    if inconclusive:
        statuses = ", ".join(sorted({a.status for a in inconclusive}))
        return RuleOutcome(
            "needs_review",
            f"{len(inconclusive)} verification attempt(s) inconclusive ({statuses}); routed for manual confirmation.",
            inputs=inputs,
            evidence=[],
        )
    return RuleOutcome(
        "pass",
        f"All relevant verification attempts ({len(relevant)}) returned verified.",
        inputs=inputs,
        evidence=[],
    )


EVALUATORS = {
    "application_field_min": _eval_application_field_min,
    "document_expiry": _eval_document_expiry,
    "required_document": _eval_required_document,
    "identifier_match": _eval_identifier_match,
    "verification_status": _eval_verification_status,
}


def evaluate_rule(
    ctx: EvalContext,
    rule_version: RuleVersion,
    validation_run_id: uuid.UUID,
) -> RuleEvaluation:
    d = rule_version.definition or {}
    evaluator_name = str(d.get("evaluator", ""))
    result = RuleEvaluation(
        validation_run_id=validation_run_id,
        application_id=ctx.application.id,
        rule_version_id=rule_version.id,
        outcome="unable_to_evaluate",
        explanation="",
        inputs={},
    )
    result.rule_version = rule_version

    applicable = not rule_version.applies_to or ctx.application.category_code in rule_version.applies_to
    effective = rule_version.effective_from <= ctx.as_of and (
        rule_version.effective_to is None or ctx.as_of <= rule_version.effective_to
    )
    if not applicable or not effective:
        result.outcome = "not_applicable"
        result.explanation = (
            f"Rule {rule_version.rule.rule_code} v{rule_version.version} does not apply to "
            f"category {ctx.application.category_code} on {ctx.as_of}."
        )
        result.inputs = {"applies_to": list(rule_version.applies_to), "category": ctx.application.category_code}
        return result

    evaluator = EVALUATORS.get(evaluator_name)
    if evaluator is None:
        result.outcome = "unable_to_evaluate"
        result.explanation = f"Unknown evaluator '{evaluator_name}' configured for this rule version."
        result.inputs = {"evaluator": evaluator_name}
        return result

    try:
        outcome = evaluator(ctx, rule_version)
    except Exception as exc:  # noqa: BLE001
        logger.exception("rule %s raised during evaluation", rule_version.rule.rule_code)
        result.outcome = "unable_to_evaluate"
        result.explanation = f"Rule evaluation raised an error; routed for manual attention: {exc}"
        result.inputs = {"evaluator": evaluator_name, "error": str(exc)[:300]}
        return result

    result.outcome = outcome.outcome
    result.explanation = outcome.explanation
    result.inputs = outcome.inputs
    result.evidence = outcome.evidence
    if outcome.finding is not None:
        result.inputs["_finding"] = outcome.finding
    return result


def findings_from_evaluations(evaluations: list[RuleEvaluation], institution_id: uuid.UUID) -> list[Finding]:
    created: list[Finding] = []
    for evaluation in evaluations:
        finding_payload = (evaluation.inputs or {}).get("_finding")
        if not finding_payload:
            continue
        created.append(_make_finding(evaluation, finding_payload, institution_id))
    return created


def _make_finding(evaluation: RuleEvaluation, payload: dict[str, Any], institution_id: uuid.UUID) -> Finding:
    fingerprint_source = "|".join(
        [
            payload["finding_code"],
            str(evaluation.rule_version_id),
            str(evaluation.application_id),
            str(payload.get("expected_value", "")),
        ]
    )
    return Finding(
        application_id=evaluation.application_id,
        institution_id=institution_id,
        validation_run_id=evaluation.validation_run_id,
        fingerprint=fingerprint_source[:400],
        finding_code=payload["finding_code"],
        category=payload["category"],
        severity=_severity_of(evaluation),
        status="open",
        title=payload["title"],
        description=payload["description"],
        expected_value=payload.get("expected_value"),
        observed_value=payload.get("observed_value"),
        recommended_action=payload.get("recommended_action"),
        detection_method="deterministic_rule_engine",
        confidence=None,
        rule_version_id=evaluation.rule_version_id,
        rule_evaluation_id=evaluation.id,
        is_simulated=bool(payload.get("is_simulated", False)),
    )


def evaluation_rule_institution(evaluation: RuleEvaluation) -> uuid.UUID:
    from app.models import Application

    return evaluation.application.institution_id if evaluation.application else None


def _severity_of(evaluation: RuleEvaluation) -> str:
    severity = getattr(evaluation.rule_version, "severity", "medium") if evaluation.rule_version else "medium"
    order = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
    return str(severity) if severity in order else "medium"
