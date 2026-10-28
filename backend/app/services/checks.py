"""Completeness + cross-document consistency checks; produces deterministic findings."""
import uuid
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Application,
    Document,
    DocumentVersion,
    ExtractedField,
    Finding,
    FindingEvidence,
)

_NAME_FIELDS = {"institution_name", "trust_name", "entity_name"}
_ADDRESS_FIELDS = {"address", "address_line"}
_EXACT_FIELDS = {"aicte_pid", "pincode", "reference_no"}

# Fields that describe the institution/application and must therefore agree across all
# submitted documents. Document-specific values (NOC numbers, issue/expiry dates, issuer
# names, ...) legitimately differ between unrelated certificates, so comparing them would
# create false conflicts.
CROSS_DOCUMENT_FIELDS = {"institution_name", "trust_name", "entity_name", "aicte_pid", "pincode"}

FIELD_LABELS: dict[str, tuple[str, str]] = {
    "institution_name": ("Institution name", "text"),
    "aicte_pid": ("AICTE Permanent ID", "identifier"),
    "trust_name": ("Trust / Society name", "text"),
    "address": ("Address", "text"),
    "valid_until": ("Valid until", "date"),
    "issued_on": ("Issued on", "date"),
    "document_no": ("Document number", "identifier"),
    "approved_intake": ("Approved intake", "number"),
    "academic_year": ("Academic year", "text"),
    "issuer_name": ("Issuing authority", "text"),
}


def collect_document_fields(
    db: Session, application: Application
) -> tuple[list[Document], dict[str, list[ExtractedField]], list[ExtractedField]]:
    documents = db.scalars(
        select(Document)
        .where(Document.application_id == application.id, Document.is_deleted.is_(False))
        .order_by(Document.created_at)
    ).all()
    fields = db.scalars(
        select(ExtractedField).where(
            ExtractedField.application_id == application.id,
            ExtractedField.is_current.is_(True),
        )
    ).all()
    by_document: dict[str, list[ExtractedField]] = {}
    for f in fields:
        by_document.setdefault(str(f.document_id), []).append(f)
    return list(documents), by_document, fields


def _active_value(field: ExtractedField) -> str | None:
    return field.corrected_value or field.normalized_value or field.raw_value


def _match_keys(field_name: str) -> tuple[str, str]:
    field_name, _label = field_name, FIELD_LABELS.get(field_name, (field_name, "text"))
    return field_name, FIELD_LABELS.get(field_name, (field_name, "text"))[1]


def exact_duplicates(db: Session, application: Application) -> list[dict[str, Any]]:
    rows = db.execute(
        select(DocumentVersion.sha256, Document.id, DocumentVersion.id, DocumentVersion.original_filename)
        .join(Document, DocumentVersion.document_id == Document.id)
        .where(Document.application_id == application.id, Document.is_deleted.is_(False))
        .order_by(DocumentVersion.uploaded_at)
    ).all()
    groups: dict[str, list[tuple]] = {}
    for sha, doc_id, version_id, fname in rows:
        groups.setdefault(sha, []).append((doc_id, version_id, fname))
    return [
        {"sha256": sha, "members": [{"document": d, "version": v, "name": n} for d, v, n in members]}
        for sha, members in groups.items()
        if len(members) > 1
    ]


def build_consistency_findings(
    db: Session,
    application: Application,
    validation_run_id: uuid.UUID,
    fields_by_document: dict[str, list[ExtractedField]],
    doc_by_id: dict[str, Document],
) -> list[Finding]:
    findings: list[Finding] = []
    clusters: dict[str, list[tuple[ExtractedField, str]]] = {}
    for _doc_key, flist in fields_by_document.items():
        for f in flist:
            value = _active_value(f)
            if not value:
                continue
            clusters.setdefault(f.field_name, []).append(
                (f, value.strip().upper() if f.value_type in ("identifier", "date") else value.strip())
            )

    for field_name, observations in clusters.items():
        if field_name not in CROSS_DOCUMENT_FIELDS:
            continue
        if len(observations) < 2:
            continue
        _label, value_type = _match_keys(field_name)
        distinct: dict[str, ExtractedField] = {}
        for f, norm in observations:
            distinct.setdefault(norm, f)
        if len(distinct) < 2:
            continue
        values = list(distinct.keys())
        conflict = _substantive_conflict(values, value_type)
        if conflict is None:
            continue
        value_a, value_b = values[0], values[1]
        f_a, f_b = distinct[value_a], distinct[value_b]
        doc_a, doc_b = doc_by_id.get(str(f_a.document_id)), doc_by_id.get(str(f_b.document_id))
        field_label = _match_keys(field_name)[0].replace("_", " ").title()
        fingerprint = f"XDOC_CONFLICT::{field_name}::{value_a}#{value_b}"[:400]
        finding = Finding(
            application_id=application.id,
            institution_id=application.institution_id,
            validation_run_id=validation_run_id,
            fingerprint=fingerprint,
            finding_code="XDOC_CONFLICT",
            category="consistency",
            severity="high" if value_type == "identifier" else "medium",
            status="open",
            title=f"Cross-document conflict: {field_label}",
            description=conflict,
            expected_value=f"Same {field_label} in all documents",
            observed_value=f"{value_a!r} ({doc_a.title if doc_a else f_a.document_id}) vs {value_b!r} ({doc_b.title if doc_b else f_b.document_id})",
            recommended_action="Verify supporting documents; request a corrected document if the discrepancy is confirmed.",
            detection_method="deterministic_cross_document_compare",
            confidence=None,
            document_id=f_a.document_id,
        )
        db.add(finding)
        db.flush()
        for role, field_row in (("source_a", f_a), ("source_b", f_b)):
            db.add(
                FindingEvidence(
                    finding_id=finding.id,
                    document_id=field_row.document_id,
                    document_version_id=field_row.document_version_id,
                    extracted_field_id=field_row.id,
                    page_no=field_row.page_no,
                    bbox=field_row.bbox,
                    excerpt=(field_row.raw_value or field_row.normalized_value or "")[:200],
                    role=role,
                    label=_match_keys(field_name)[0],
                )
            )
        findings.append(finding)
    return findings


def _substantive_conflict(values: list[str], value_type: str) -> str | None:
    if value_type in ("identifier", "date", "number"):
        return "Exact values differ; these field types are compared strictly."
    a, b = values[0], values[1]
    ratio = fuzz.token_sort_ratio(a, b) / 100.0
    if ratio < 0.80:
        return f"Textual fields differ materially (normalized similarity {ratio:.2f} < 0.80)."
    return None
