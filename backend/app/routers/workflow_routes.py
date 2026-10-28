"""Findings + review workflow + rules + verification listing + reports download +
audit trail + system endpoints."""
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import log_audit
from app.db import get_db
from app.config import get_settings
from app.models import (
    Application,
    ApplicationCategory,
    AuditEvent,
    FieldCorrection,
    Finding,
    FindingEvidence,
    Profile,
    ReferenceRecord,
    DocumentCategory,
    ReviewAction,
    ReportRecord,
    RuleVersion,
    ComplianceRule,
    VerificationAttempt,
    VerificationProvider,
)
from app.schemas import FieldCorrectionIn, ReviewActionIn
from app.security import (
    CurrentUser,
    get_current_user,
    require_admin,
    require_staff,
    require_reviewer_or_admin,
    user_can_read_application,
)
from app.services import reports as reports_mod
from app.services import storage as storage_mod

staff = require_staff()

FINDING_TRANSITIONS = {
    "confirm": {"from": ["open", "under_review", "needs_clarification"], "to": "confirmed"},
    "dismiss": {"from": ["open", "under_review", "needs_clarification"], "to": "dismissed"},
    "resolve": {"from": ["open", "under_review", "confirmed", "needs_clarification"], "to": "resolved"},
    "reopen": {"from": ["confirmed", "dismissed", "resolved"], "to": "open"},
    "start_review": {"from": ["open", "needs_clarification"], "to": "under_review"},
    "request_clarification": {"from": ["open", "under_review"], "to": "needs_clarification"},
}

# ---------------------------------------------------------------- findings

findings_router = APIRouter(prefix="/findings", tags=["findings"])


@findings_router.get("")
def list_findings(
    application_id: str | None = None,
    status_filter: str | None = None,
    severity: str | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    stmt = select(Finding).order_by(Finding.last_detected_at.desc())
    if application_id:
        stmt = stmt.where(Finding.application_id == uuid.UUID(application_id))
    if status_filter:
        stmt = stmt.where(Finding.status == status_filter)
    if severity:
        stmt = stmt.where(Finding.severity == severity)
    rows = db.scalars(stmt).all()
    visible: list[Finding] = []
    staff_user = user.is_staff()
    for f in rows:
        if staff_user:
            visible.append(f)
            continue
        if not f.visible_to_institution:
            continue
        application = db.get(Application, f.application_id)
        if user_can_read_application(db, user, application):
            visible.append(f)
    return {"findings": [_finding_payload(f) for f in visible]}


def _finding_payload(f: Finding) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "application_id": str(f.application_id),
        "reference_no": f.application.reference_no if f.application else None,
        "finding_code": f.finding_code,
        "category": f.category,
        "severity": f.severity,
        "status": f.status,
        "title": f.title,
        "description": f.description,
        "expected_value": f.expected_value,
        "observed_value": f.observed_value,
        "recommended_action": f.recommended_action,
        "detection_method": f.detection_method,
        "confidence": float(f.confidence) if f.confidence is not None else None,
        "is_simulated": f.is_simulated,
        "is_advisory": f.is_advisory,
        "visible_to_institution": f.visible_to_institution,
        "last_detected_at": str(f.last_detected_at),
        "first_detected_at": str(f.first_detected_at),
        "document_id": str(f.document_id) if f.document_id else None,
    }


@findings_router.get("/{finding_id}")
def get_finding(
    finding_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    finding = db.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Finding not found")
    if not user.is_staff():
        if not finding.visible_to_institution:
            raise HTTPException(http_status.HTTP_403_FORBIDDEN, "Not permitted")
        application = db.get(Application, finding.application_id)
        if not user_can_read_application(db, user, application):
            raise HTTPException(http_status.HTTP_403_FORBIDDEN, "Not permitted")
    evidence = db.scalars(select(FindingEvidence).where(FindingEvidence.finding_id == finding.id)).all()
    actions = db.scalars(
        select(ReviewAction).where(ReviewAction.finding_id == finding.id).order_by(ReviewAction.created_at)
    ).all()
    payload = _finding_payload(finding)
    payload["evidence"] = [
        {
            "id": str(e.id),
            "document_id": str(e.document_id) if e.document_id else None,
            "document_version_id": str(e.document_version_id) if e.document_version_id else None,
            "extracted_field_id": str(e.extracted_field_id) if e.extracted_field_id else None,
            "page_no": e.page_no,
            "bbox": e.bbox,
            "excerpt": e.excerpt,
            "role": e.role,
            "label": e.label,
        }
        for e in evidence
    ]
    payload["rule_version_id"] = str(finding.rule_version_id) if finding.rule_version_id else None
    payload["actions"] = [
        {"action": a.action, "reason_code": a.reason_code, "comment": a.comment, "at": str(a.created_at), "actor": str(a.actor_id) if a.actor_id else None}
        for a in actions
    ]
    return payload


@findings_router.post("/{finding_id}/review")
def review_finding(
    finding_id: uuid.UUID,
    payload: ReviewActionIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_reviewer_or_admin()),
):
    finding = db.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Finding not found")
    application = db.get(Application, finding.application_id)
    if payload.action in FINDING_TRANSITIONS:
        rule = FINDING_TRANSITIONS[payload.action]
        if finding.status not in rule["from"]:
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, f"Cannot {payload.action} a finding currently '{finding.status}'")
        new_status = rule["to"]
    elif payload.action in ("assign", "comment", "institution_response", "application_decision"):
        new_status = None
    else:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "Unsupported action")
    if payload.action not in ("comment",) and not (payload.comment or payload.reason_code):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "A reason code or comment is required for review decisions")

    previous_status = finding.status
    if new_status:
        finding.status = new_status
        if new_status in ("dismissed", "resolved", "confirmed"):
            from app.services.processing import now_utc

            finding.resolved_at = now_utc()
    if payload.action == "assign":
        finding.assigned_to = user.id
    if payload.visibility_release is not None:
        finding.visible_to_institution = bool(payload.visibility_release)
    db.add(
        ReviewAction(
            application_id=application.id,
            finding_id=finding.id,
            actor_id=user.id,
            action=payload.action,
            from_status=previous_status,
            to_status=new_status,
            reason_code=payload.reason_code,
            comment=payload.comment,
        )
    )
    db.flush()
    log_audit(
        db,
        action=f"finding.{payload.action}",
        entity_type="finding",
        entity_id=str(finding.id),
        application_id=application.id,
        institution_id=application.institution_id,
        meta={"from": previous_status, "to": new_status, "reason_code": payload.reason_code},
        actor=user,
    )
    return {"ok": True, "id": str(finding.id), "status": finding.status}


# ---------------------------------------------------------------- fields correction

fields_router = APIRouter(prefix="/fields", tags=["fields"])


@fields_router.patch("/{field_id}")
def correct_field(
    field_id: uuid.UUID,
    payload: FieldCorrectionIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    from app.models import ExtractedField, Application

    field = db.get(ExtractedField, field_id)
    if field is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Field not found")
    application = db.get(Application, field.application_id)
    if not (user.is_reviewer_or_admin() or application.institution_id in _membership_ids(db, user.id)):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "Not permitted to correct this field")
    old_value = field.corrected_value or field.normalized_value or field.raw_value
    field.corrected_value = payload.new_value
    field.corrected_by = user.id
    field.corrected_at = _now()
    field.status = "corrected"
    db.add(
        FieldCorrection(
            extracted_field_id=field.id,
            old_value=old_value,
            new_value=payload.new_value,
            reason=payload.reason,
            actor_id=user.id,
        )
    )
    db.flush()
    log_audit(
        db,
        action="field.corrected",
        entity_type="extracted_field",
        entity_id=str(field.id),
        application_id=application.id,
        institution_id=application.institution_id,
        meta={"old": old_value, "new": payload.new_value, "reason": payload.reason},
        actor=user,
    )
    return {"ok": True, "id": str(field.id), "corrected_value": payload.new_value}


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _membership_ids(db: Session, user: CurrentUser) -> set[uuid.UUID]:
    from app.models import InstitutionMembership

    return {m.institution_id for m in db.scalars(select(InstitutionMembership).where(InstitutionMembership.user_id == user.id)).all()}


# ---------------------------------------------------------------- rules

rules_router = APIRouter(prefix="/rules", tags=["rules"])


@rules_router.get("")
def list_rules(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    rows = db.scalars(select(ComplianceRule).order_by(ComplianceRule.rule_code)).all()
    out = []
    for rule in rows:
        versions = db.scalars(
            select(RuleVersion).where(RuleVersion.rule_id == rule.id).order_by(RuleVersion.version.desc())
        ).all()
        current = next((v for v in versions if v.id == rule.current_version_id), versions[0] if versions else None)
        out.append(_rule_payload(rule, current, versions))
    return {"rules": out}


def _rule_payload(rule: ComplianceRule, current, versions) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "rule_code": rule.rule_code,
        "name": rule.name,
        "category": rule.category,
        "is_active": rule.is_active,
        "current_version": {
            "id": str(current.id),
            "version": current.version,
            "name": current.name,
            "description": current.description,
            "definition": current.definition,
            "severity": current.severity,
            "effective_from": str(current.effective_from),
            "effective_to": str(current.effective_to) if current.effective_to else None,
            "source_document": current.source_document,
            "source_clause": current.source_clause,
            "source_quote": current.source_quote,
            "is_demo_rule": current.is_demo_rule,
            "applies_to": list(current.applies_to),
        } if current else None,
        "version_count": len(versions),
    }


@rules_router.get("/versions/{rule_version_id}")
def get_rule_version(rule_version_id: uuid.UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    version = db.get(RuleVersion, rule_version_id)
    if version is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Rule version not found")
    return {
        "id": str(version.id),
        "rule_code": version.rule.rule_code,
        "version": version.version,
        "definition": version.definition,
        "severity": version.severity,
        "effective_from": str(version.effective_from),
        "is_demo_rule": version.is_demo_rule,
        "source_document": version.source_document,
        "source_clause": version.source_clause,
        "source_quote": version.source_quote,
    }


@rules_router.post("/{rule_id}/versions", status_code=201)
def create_rule_version(
    rule_id: uuid.UUID,
    payload: dict,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_admin()),
):
    rule = db.get(ComplianceRule, rule_id)
    if rule is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Rule not found")
    definition = payload.get("definition")
    if not isinstance(definition, dict) or "evaluator" not in definition:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "definition.evaluator is required")
    version_no = _next_version(db, rule.id)
    version = RuleVersion(
        rule_id=rule.id,
        version=version_no,
        name=payload.get("name", rule.name),
        description=payload.get("description", rule.name),
        definition=definition,
        applies_to=payload.get("applies_to", ["EOA"]),
        severity=payload.get("severity", current_severity(rule)),
        effective_from=payload.get("effective_from") or _today(),
        effective_to=payload.get("effective_to"),
        source_document=payload.get("source_document"),
        source_clause=payload.get("source_clause"),
        source_quote=payload.get("source_quote"),
        source_page=payload.get("source_page"),
        is_demo_rule=bool(payload.get("is_demo_rule", True)),
        change_note=payload.get("change_note", "created via API"),
        created_by=admin.id,
    )
    db.add(version)
    rule.current_version_id = version.id
    db.flush()
    log_audit(db, action="rule.version_created", entity_type="rule", entity_id=str(rule.id),
              meta={"version": version_no}, actor=admin)
    return {"ok": True, "rule_id": str(rule.id), "version": version_no, "is_demo_rule": version.is_demo_rule}


def _next_version(db: Session, rule_id: uuid.UUID) -> int:
    rows = db.scalars(select(RuleVersion.version).where(RuleVersion.rule_id == rule_id)).all()
    return max(rows) + 1 if rows else 1


def _today():
    from datetime import date

    return date.today().isoformat()


def current_severity(rule: ComplianceRule) -> str:
    return "medium"


# ---------------------------------------------------------------- verification listing

verification_router = APIRouter(prefix="/verification", tags=["verification"])


@verification_router.get("")
def list_verification(
    application_id: str | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(staff),
):
    stmt = select(VerificationAttempt).order_by(VerificationAttempt.requested_at.desc()).limit(300)
    if application_id:
        stmt = stmt.where(VerificationAttempt.application_id == uuid.UUID(application_id))
    rows = db.scalars(stmt).all()
    return {
        "attempts": [
            {
                "id": str(a.id),
                "application_id": str(a.application_id),
                "document_id": str(a.document_id) if a.document_id else None,
                "provider_code": a.provider_code,
                "reference_no": a.reference_no,
                "status": a.status,
                "is_simulated": a.is_simulated,
                "compared_fields": a.compared_fields,
                "response": a.response_payload,
                "requested_at": str(a.requested_at),
            }
            for a in rows
        ]
    }


@verification_router.get("/providers")
def list_providers(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    rows = db.scalars(select(VerificationProvider)).all()
    return {
        "providers": [
            {
                "code": p.code,
                "name": p.name,
                "type": p.provider_type,
                "availability": p.availability,
                "is_enabled": p.is_enabled,
                "supported_document_categories": list(p.supported_document_categories or []),
                "description": p.description,
            }
            for p in rows
        ]
    }


@verification_router.post("/providers/{code}/availability")
def set_provider_availability(
    code: str,
    payload: dict,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_admin()),
):
    provider = db.get(VerificationProvider, code)
    if provider is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Provider not found")
    availability = payload.get("availability")
    if availability not in ("available", "unavailable", "not_configured"):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "availability must be available/unavailable/not_configured")
    provider.availability = availability
    log_audit(db, action="provider.availability_change", entity_type="verification_provider", entity_id=code,
              meta={"availability": availability}, actor=admin)
    return {"ok": True, "code": provider.code, "availability": provider.availability}


# ---------------------------------------------------------------- reports download

reports_router = APIRouter(prefix="/reports", tags=["reports"])


@reports_router.get("")
def list_reports(application_id: str | None = None, db: Session = Depends(get_db), user: CurrentUser = Depends(staff)):
    stmt = select(ReportRecord).order_by(ReportRecord.generated_at.desc()).limit(100)
    if application_id:
        stmt = stmt.where(ReportRecord.application_id == uuid.UUID(application_id))
    rows = db.scalars(stmt).all()
    return {
        "reports": [
            {"id": str(r.id), "application_id": str(r.application_id), "reference_code": r.reference_code,
             "report_type": r.report_type, "generated_at": str(r.generated_at), "size_bytes": r.size_bytes,
             "includes_simulated": r.includes_simulated}
            for r in rows
        ]
    }


@reports_router.get("/{report_id}/download")
def download_report(report_id: uuid.UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(staff)):
    report = db.get(ReportRecord, report_id)
    if report is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Report not found")
    log_audit(db, action="report.downloaded", entity_type="report", entity_id=str(report.id),
              application_id=report.application_id, actor=user)
    try:
        payload = storage_mod.resolve_report(report.storage_backend, report.storage_path).read_bytes()
    except storage_mod.StorageError as exc:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, str(exc))
    import io

    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report.reference_code}.pdf"'},
    )


# ---------------------------------------------------------------- audit + system

audit_router = APIRouter(prefix="/audit", tags=["audit"])


@audit_router.get("")
def list_audit_events(
    application_id: str | None = None,
    action: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    if not (user.role in ("admin", "auditor") or user.role == "reviewer"):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "Audit trail access requires auditor/admin/reviewer role")
    stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(limit, 500))
    if application_id:
        stmt = stmt.where(AuditEvent.application_id == uuid.UUID(application_id))
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    rows = db.scalars(stmt).all()
    return {
        "events": [
            {
                "id": e.id,
                "event_id": str(e.event_id),
                "occurred_at": str(e.occurred_at),
                "actor_email": e.actor_email,
                "actor_role": e.actor_role,
                "action": e.action,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "application_id": str(e.application_id) if e.application_id else None,
                "meta": e.meta,
                "event_hash": e.event_hash,
            }
            for e in rows
        ]
    }


@audit_router.get("/verify-chain")
def verify_chain(db: Session = Depends(get_db), admin: CurrentUser = Depends(get_current_user)):
    result = db.execute(
        text("select audit_id, problem from public.verify_audit_chain()")
    ).all()
    return {"problems": [{"audit_id": r[0], "problem": r[1]} for r in result]}


system_router = APIRouter(prefix="/system", tags=["system"])


@system_router.get("/health")
def health(db: Session = Depends(get_db)):
    from sqlalchemy import text

    db.execute(text("select 1"))
    settings = get_settings()
    return {
        "ok": True,
        "database": "up",
        "gemini": "configured" if settings.gemini_enabled else "not_configured",
        "storage_backend": settings.STORAGE_BACKEND,
        "environment": settings.ENVIRONMENT,
    }


@system_router.get("/integrations")
def integrations(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    settings = get_settings()
    providers = db.scalars(select(VerificationProvider)).all()
    return {
        "gemini": {
            "configured": settings.gemini_enabled,
            "model": settings.GEMINI_MODEL if settings.gemini_enabled else None,
            "role": "optional assist (classification + field normalization); system degrades gracefully",
        },
        "database": {"host": "supabase-pooler", "schema": settings.DB_SCHEMA},
        "storage": {"backend": settings.STORAGE_BACKEND, "bucket": settings.STORAGE_BUCKET},
        "verification_providers": [
            {"code": p.code, "type": p.provider_type, "availability": p.availability, "enabled": p.is_enabled}
            for p in providers
        ],
        "official_integrations_note": "No live DigiLocker/AICTE/issuer integration is authorized; adapters are simulated and labelled.",
    }


@system_router.get("/catalogs")
def catalogs(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    categories = db.scalars(select(ApplicationCategory)).all()
    document_categories = db.scalars(select(DocumentCategory)).all()
    return {
        "application_categories": [
            {"code": c.code, "name": c.name, "enabled": c.is_enabled, "source_reference": c.source_reference}
            for c in categories
        ],
        "document_categories": [
            {
                "code": c.code,
                "name": c.name,
                "has_expiry": c.has_expiry,
                "is_infrastructure": c.is_infrastructure,
                "expected_fields": c.expected_fields,
                "classification_keywords": c.classification_keywords,
            }
            for c in document_categories
        ],
    }
