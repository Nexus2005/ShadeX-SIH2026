"""Routers: dashboard, institutions, applications + documents, findings, rules,
verification, reports, audit, system."""
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import log_audit
from app.db import get_db
from app.models import (
    Application,
    ApplicationCategory,
    ApplicationStatusHistory,
    AuditEvent,
    Document,
    DocumentCategory,
    DocumentPage,
    DocumentVersion,
    ExtractedField,
    Finding,
    Institution,
    InstitutionMembership,
    Profile,
    ReportRecord,
    ValidationRun,
)
from app.schemas import (
    ApplicationCreate,
    ApplicationUpdate,
    InstitutionCreate,
    InstitutionUpdate,
    StatusChange,
)
from app.security import (
    CurrentUser,
    get_current_user,
    require_roles,
    require_reviewer_or_admin,
    require_staff,
    user_can_read_application,
    user_can_write_application,
    user_institution_ids,
)
from app.services import processing as processing_mod
from app.services import reports as reports_mod
from app.services import storage as storage_mod
from app.services import verification as verification_mod

staff = require_staff()
reviewer_or_admin = require_reviewer_or_admin()
admin_only = require_roles("admin")
institution_allowed = require_roles("admin", "reviewer", "institution")

# ------------------------------------------------------------------ helpers


def _get_application_or_403(db: Session, application_id: uuid.UUID, user: CurrentUser, write: bool = True) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    allowed = user_can_write_application(db, user, application) if write else user_can_read_application(db, user, application)
    if not allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this application")
    return application


ALLOWED_UPLOAD = {".pdf", ".png", ".jpg", ".jpeg"}


# ------------------------------------------------------------------ dashboard

from fastapi import APIRouter  # noqa: E402  (placed after shared deps for clarity)

dashboard_router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@dashboard_router.get("/summary", dependencies=[Depends(staff)])
def summary(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    applications = db.scalars(select(Application)).all()
    documents = db.scalars(select(Document).where(Document.is_deleted.is_(False))).all()
    findings = db.scalars(select(Finding)).all()

    status_counts: dict[str, int] = {}
    priority_counts: dict[str, int] = {}
    for app in applications:
        status_counts[app.status] = status_counts.get(app.status, 0) + 1
        priority_counts[app.priority] = priority_counts.get(app.priority, 0) + 1

    finding_status: dict[str, int] = {}
    severity_by_status: dict[str, int] = {}
    for f in findings:
        finding_status[f.status] = finding_status.get(f.status, 0) + 1
        if f.status in ("open", "under_review", "needs_clarification"):
            severity_by_status[f.severity] = severity_by_status.get(f.severity, 0) + 1

    unreadable = sum(1 for d in documents if d.status in ("failed", "unreadable"))
    processed_ok = sum(1 for d in documents if d.status == "processed")

    recent_validations = db.scalars(
        select(ValidationRun).order_by(ValidationRun.started_at.desc()).limit(5)
    ).all()

    return {
        "applications_total": len(applications),
        "applications_by_status": status_counts,
        "applications_by_priority": priority_counts,
        "documents_total": len(documents),
        "documents_processed": processed_ok,
        "documents_unreadable": unreadable,
        "findings_open": finding_status.get("open", 0) + finding_status.get("under_review", 0) + finding_status.get("needs_clarification", 0),
        "findings_by_status": finding_status,
        "open_findings_by_severity": severity_by_status,
        "recent_validation_runs": [
            {"id": str(r.id), "application_id": str(r.application_id), "status": r.status, "started_at": str(r.started_at)}
            for r in recent_validations
        ],
    }


# ------------------------------------------------------------------ institutions

institutions_router = APIRouter(prefix="/institutions", tags=["institutions"])


@institutions_router.get("")
def list_institutions(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ids = user_institution_ids(db, user.id)
    rows = db.scalars(select(Institution).order_by(Institution.name)).all()
    visible = [i for i in rows if user.is_staff() or i.id in ids]
    return {"institutions": [_institution_payload(i) for i in visible]}


@institutions_router.post("", status_code=201)
def create_institution(
    payload: InstitutionCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(reviewer_or_admin),
):
    exists = db.scalars(select(Institution).where(Institution.aicte_pid == payload.aicte_pid.upper())).first()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "An institution with this AICTE PID already exists")
    institution = Institution(**payload.model_dump())
    institution.aicte_pid = payload.aicte_pid.upper()
    institution.created_by = user.id
    if payload.contact_email:
        membership = InstitutionMembership(
            institution_id=institution.id, user_id=user.id, member_role="head"
        ) if user.role == "institution" else None
        if membership:
            db.add(membership)
    db.add(institution)
    db.flush()
    log_audit(db, action="institution.create", entity_type="institution", entity_id=str(institution.id),
              institution_id=institution.id, meta={"name": institution.name}, actor=user)
    return _institution_payload(institution)


def _institution_payload(i: Institution) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "name": i.name,
        "aicte_pid": i.aicte_pid,
        "institution_type": i.institution_type,
        "trust_society_name": i.trust_society_name,
        "address_line": i.address_line,
        "city": i.city,
        "district": i.district,
        "state": i.state,
        "pincode": i.pincode,
        "contact_email": i.contact_email,
        "contact_phone": i.contact_phone,
        "established_year": i.established_year,
        "is_synthetic": i.is_synthetic,
        "created_at": str(i.created_at),
    }


@institutions_router.get("/{institution_id}")
def get_institution(institution_id: uuid.UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    institution = db.get(Institution, institution_id)
    if institution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Institution not found")
    if not (user.is_staff() or institution.id in user_institution_ids(db, user.id)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted")
    apps = db.scalars(select(Application).where(Application.institution_id == institution.id)).all()
    payload = _institution_payload(institution)
    payload["applications"] = [
        {"id": str(a.id), "reference_no": a.reference_no, "title": a.title, "status": a.status, "year": a.academic_year}
        for a in apps
    ]
    return payload


@institutions_router.patch("/{institution_id}")
def update_institution(
    institution_id: uuid.UUID,
    payload: InstitutionUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    institution = db.get(Institution, institution_id)
    if institution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Institution not found")
    if not (user.is_reviewer_or_admin() or institution.id in user_institution_ids(db, user.id)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(institution, key, value)
    log_audit(db, action="institution.update", entity_type="institution", entity_id=str(institution.id),
              institution_id=institution.id, meta={"fields": list(payload.model_dump(exclude_unset=True).keys())}, actor=user)
    return _institution_payload(institution)


# ------------------------------------------------------------------ applications

applications_router = APIRouter(prefix="/applications", tags=["applications"])


@applications_router.get("")
def list_applications(
    status_filter: str | None = None,
    institution_id: str | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    stmt = select(Application).order_by(Application.updated_at.desc())
    if status_filter:
        stmt = stmt.where(Application.status == status_filter)
    if institution_id:
        stmt = stmt.where(Application.institution_id == uuid.UUID(institution_id))
    rows = db.scalars(stmt).all()
    ids = user_institution_ids(db, user.id)
    visible = [a for a in rows if user.is_staff() or a.institution_id in ids]
    return {
        "applications": [
            {
                "id": str(a.id),
                "reference_no": a.reference_no,
                "title": a.title,
                "institution_id": str(a.institution_id),
                "institution_name": a.institution.name,
                "category_code": a.category_code,
                "academic_year": a.academic_year,
                "status": a.status,
                "priority": a.priority,
                "updated_at": str(a.updated_at),
            }
            for a in visible
        ]
    }


@applications_router.post("", status_code=201)
def create_application(
    payload: ApplicationCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(institution_allowed),
):
    category = db.get(ApplicationCategory, payload.category_code)
    if category is None or not category.is_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Application category is unavailable")
    institution = db.get(Institution, payload.institution_id)
    if institution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Institution not found")
    if not (user.is_reviewer_or_admin() or institution.id in user_institution_ids(db, user.id)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot create applications for this institution")
    application = Application(**payload.model_dump(), created_by=user.id)
    db.add(application)
    db.flush()
    log_audit(
        db,
        action="application.create",
        entity_type="application",
        entity_id=str(application.id),
        application_id=application.id,
        institution_id=application.institution_id,
        meta={"reference_no": application.reference_no, "category": application.category_code},
        actor=user,
    )
    return {"id": str(application.id), "reference_no": application.reference_no, "status": application.status}


@applications_router.get("/{application_id}")
def get_application(
    application_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    application = _get_application_or_403(db, application_id, user, write=False)
    documents = db.scalars(
        select(Document).where(Document.application_id == application.id, Document.is_deleted.is_(False)).order_by(Document.created_at)
    ).all()
    latest_run = db.scalars(
        select(ValidationRun).where(ValidationRun.application_id == application.id).order_by(ValidationRun.started_at.desc()).limit(1)
    ).first()
    open_findings = db.scalars(
        select(func.count()).select_from(Finding).where(
            Finding.application_id == application.id,
            Finding.status.in_(["open", "under_review", "needs_clarification"]),
        )
    ).one()
    return {
        "id": str(application.id),
        "reference_no": application.reference_no,
        "title": application.title,
        "description": application.description,
        "status": application.status,
        "priority": application.priority,
        "category_code": application.category_code,
        "academic_year": application.academic_year,
        "institution": {"id": str(application.institution.id), "name": application.institution.name, "aicte_pid": application.institution.aicte_pid},
        "declared_data": application.declared_data,
        "submitted_at": str(application.submitted_at) if application.submitted_at else None,
        "last_validated_at": str(application.last_validated_at) if application.last_validated_at else None,
        "risk_summary": application.risk_summary,
        "documents": [_document_payload(d) for d in documents],
        "latest_validation_run": {
            "id": str(latest_run.id), "status": latest_run.status, "summary": latest_run.summary,
            "started_at": str(latest_run.started_at),
        } if latest_run else None,
        "open_findings_count": int(open_findings),
    }


def _document_payload(d: Document) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "title": d.title,
        "declared_category_code": d.declared_category_code,
        "detected_category_code": d.detected_category_code,
        "status": d.status,
        "classification_method": d.classification_method,
        "classification_confidence": float(d.classification_confidence) if d.classification_confidence else None,
        "version_no": d.current_version_no,
        "updated_at": str(d.updated_at),
    }


@applications_router.patch("/{application_id}")
def update_application(
    application_id: uuid.UUID,
    payload: ApplicationUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    application = _get_application_or_403(db, application_id, user, write=True)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(application, key, value)
    log_audit(db, action="application.update", entity_type="application", entity_id=str(application.id),
              application_id=application.id, institution_id=application.institution_id,
              meta={"fields": list(payload.model_dump(exclude_unset=True).keys())}, actor=user)
    return {"ok": True}


@applications_router.post("/{application_id}/status")
def change_status(
    application_id: uuid.UUID,
    payload: StatusChange,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    application = _get_application_or_403(db, application_id, user, write=True)
    if application.status == "draft" and payload.to_status == "submitted" and user.role == "institution":
        membership_ok = True
    try:
        processing_mod.transition_status(db, application, payload.to_status, user.id, payload.reason)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    log_audit(
        db,
        action="application.status_change",
        entity_type="application",
        entity_id=str(application.id),
        application_id=application.id,
        institution_id=application.institution_id,
        meta={"to": payload.to_status, "reason": payload.reason},
        actor=user,
    )
    return {"ok": True, "status": application.status}


@applications_router.get("/{application_id}/history")
def status_history(
    application_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    application = _get_application_or_403(db, application_id, user, write=False)
    history = db.scalars(
        select(ApplicationStatusHistory)
        .where(ApplicationStatusHistory.application_id == application.id)
        .order_by(ApplicationStatusHistory.created_at)
    ).all()
    return {
        "history": [
            {"from": h.from_status, "to": h.to_status, "reason": h.reason, "at": str(h.created_at), "actor": str(h.actor_id) if h.actor_id else None}
            for h in history
        ]
    }


# ------------------------------------------------------------------ documents

documents_router = APIRouter(prefix="/applications/{application_id}/documents", tags=["documents"])
document_router = APIRouter(prefix="/documents", tags=["documents"])


@documents_router.post("", status_code=201)
async def upload_document(
    application_id: uuid.UUID,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    declared_category_code: str | None = Form(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    application = _get_application_or_403(db, application_id, user, write=True)
    raw = await file.read()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty upload rejected")
    if len(raw) > get_limit():
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File exceeds the size limit")
    original = file.filename or "upload.bin"
    if not has_allowed_extension(original):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only PDF/PNG/JPEG documents are accepted")
    if declared_category_code:
        category = db.get(DocumentCategory, declared_category_code)
        if category is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown document category")

    try:
        storage_meta = storage_mod.save_application_file(application.reference_no, raw, original)
    except storage_mod.StorageError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    document = Document(
        application_id=application.id,
        institution_id=application.institution_id,
        title=(title or original)[:180],
        declared_category_code=declared_category_code,
        created_by=user.id,
        status="uploaded",
    )
    db.add(document)
    db.flush()
    version = DocumentVersion(
        document_id=document.id,
        application_id=application.id,
        version_no=1,
        original_filename=original[:200],
        mime_type=storage_meta["mime_type"],
        file_ext=storage_meta["file_ext"],
        size_bytes=storage_meta["size_bytes"],
        sha256=storage_meta["sha256"],
        storage_backend=storage_meta["backend"],
        storage_path=storage_meta["path"],
        uploaded_by=user.id,
    )
    db.add(version)
    document.current_version_id = version.id
    document.current_version_no = 1
    db.flush()
    log_audit(
        db,
        action="document.upload",
        entity_type="document",
        entity_id=str(document.id),
        application_id=application.id,
        institution_id=application.institution_id,
        meta={"filename": original[:120], "sha256": storage_meta["sha256"], "size": storage_meta["size_bytes"]},
        actor=user,
    )
    return _document_payload(document)


def get_limit() -> int:
    from app.config import get_settings

    return get_settings().max_upload_bytes


def has_allowed_extension(filename: str) -> bool:
    return ("." in filename) and ("." + filename.rsplit(".", 1)[-1].lower()) in ALLOWED_UPLOAD


@document_router.get("/{document_id}/stream")
def stream_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    application = db.get(Application, document.application_id)
    if not user_can_read_application(db, user, application):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted to view this document")
    version = db.get(DocumentVersion, document.current_version_id)
    try:
        payload = storage_mod.read_file(version.storage_backend, version.storage_path) if version else b""
    except storage_mod.StorageError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    log_audit(db, action="document.download", entity_type="document", entity_id=str(document.id),
              application_id=application.id, institution_id=application.institution_id, actor=user)
    import io

    return StreamingResponse(
        io.BytesIO(payload),
        media_type=version.mime_type,
        headers={"Content-Disposition": f'inline; filename="{version.original_filename}"'},
    )


@document_router.get("/{document_id}/extractions")
def document_extractions(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    application = db.get(Application, document.application_id)
    if not user_can_read_application(db, user, application):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted")
    fields = db.scalars(
        select(ExtractedField).where(
            ExtractedField.document_id == document.id, ExtractedField.is_current.is_(True)
        )
    ).all()
    pages = db.scalars(
        select(DocumentPage)
        .join(DocumentVersion, DocumentVersion.id == DocumentPage.document_version_id)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentPage.page_no)
    ).all()
    return {
        "fields": [
            {
                "id": str(f.id),
                "name": f.field_name,
                "label": f.label,
                "type": f.value_type,
                "raw_value": f.raw_value,
                "normalized_value": f.normalized_value,
                "corrected_value": f.corrected_value,
                "confidence": float(f.confidence) if f.confidence is not None else None,
                "page_no": f.page_no,
                "bbox": f.bbox,
                "method": f.extraction_method,
                "status": f.status,
            }
            for f in fields
        ],
        "pages": [
            {
                "page_no": p.page_no,
                "text_source": p.text_source,
                "text_preview": (p.text or "")[:400],
                "lines": (p.lines or [])[:400],
                "ocr_confidence": float(p.ocr_confidence) if p.ocr_confidence else None,
                "quality": p.quality,
            }
            for p in pages
        ],
        "forensics": db.get(DocumentVersion, document.current_version_id).forensics if document.current_version_id else {},
    }


def _extraction_model():
    from app.models import ExtractedField, DocumentPage

    return ExtractedField, DocumentPage


@document_router.post("/{document_id}/process")
def document_process(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    application = db.get(Application, document.application_id)
    if not user_can_write_application(db, user, application):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted to process this document")
    processing_mod.process_document(db, document_id, user.id)
    log_audit(db, action="document.processed", entity_type="document", entity_id=str(document.id),
              application_id=application.id, institution_id=application.institution_id, actor=user)
    updated = db.get(Document, document_id)
    return _document_payload(updated)


@applications_router.post("/{application_id}/process")
def process_application_documents(
    application_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(reviewer_or_admin),
):
    application = _get_application_or_403(db, application_id, user)
    documents = db.scalars(
        select(Document).where(
            Document.application_id == application.id,
            Document.is_deleted.is_(False),
            Document.status.in_(["uploaded", "failed"]),
        )
    ).all()
    results = []
    for document in documents:
        processing_mod.process_document(db, document.id, user.id)
        results.append(_document_payload(document))
    application.last_processed_at = datetime.now(timezone.utc)
    log_audit(db, action="application.processed", entity_type="application", entity_id=str(application.id),
              application_id=application.id, institution_id=application.institution_id,
              meta={"processed": len(results)}, actor=user)
    return {"processed": results}


@applications_router.post("/{application_id}/validate")
def validate_application(
    application_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    application = _get_application_or_403(db, application_id, user)
    if not application.submitted_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Submit the application before validation")
    run = processing_mod.validate_application(db, application_id, user.id)
    log_audit(db, action="application.validated", entity_type="application", entity_id=str(application.id),
              application_id=application.id, institution_id=application.institution_id,
              meta={"run_id": str(run.id), "summary": run.summary}, actor=user)
    return {"run_id": str(run.id), "status": run.status, "summary": run.summary}


@applications_router.post("/{application_id}/verify")
def verify_application(
    application_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(reviewer_or_admin),
):
    application = _get_application_or_403(db, application_id, user)
    attempts = verification_mod.run_application_verification(db, application, user)
    log_audit(db, action="application.verified", entity_type="application", entity_id=str(application.id),
              application_id=application.id, institution_id=application.institution_id,
              meta={"attempts": len(attempts), "simulated": True}, actor=user)
    return {
        "attempts": [
            {
                "id": str(a.id),
                "document_id": str(a.document_id) if a.document_id else None,
                "provider_code": a.provider_code,
                "status": a.status,
                "is_simulated": a.is_simulated,
                "reference_no": a.reference_no,
                "response": a.response_payload,
            }
            for a in attempts
        ]
    }


@applications_router.post("/{application_id}/reports", status_code=201)
def create_report(
    application_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(staff),
):
    application = _get_application_or_403(db, application_id, user)
    meta = reports_mod.generate_scrutiny_report(db, application, user.profile.full_name)
    from app.models import ReportRecord

    record = ReportRecord(
        application_id=application.id,
        reference_code=meta["reference_code"],
        storage_backend=meta["storage"]["backend"],
        storage_path=meta["storage"]["path"],
        sha256=meta["storage"]["sha256"],
        size_bytes=meta["storage"]["size_bytes"],
        summary=meta["summary"],
        includes_simulated=meta["includes_simulated"],
        generated_by=user.id,
    )
    db.add(record)
    db.flush()
    log_audit(db, action="report.generated", entity_type="report", entity_id=str(record.id),
              application_id=application.id, institution_id=application.institution_id,
              meta={"reference_code": meta["reference_code"]}, actor=user)
    return {
        "id": str(record.id),
        "reference_code": record.reference_code,
        "size_bytes": record.size_bytes,
        "summary": record.summary,
        "includes_simulated": record.includes_simulated,
        "download": f"/api/v1/reports/{record.id}/download",
    }
