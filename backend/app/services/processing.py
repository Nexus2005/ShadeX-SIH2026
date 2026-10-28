"""Processing orchestration.

Runs synchronously inside the request path for the prototype (demo-scale volumes); every
run records a processing_jobs row with stages, attempts and errors so a background worker
can adopt the same code path later.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import fitz
import numpy as np
from PIL import Image
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Application,
    ApplicationStatusHistory,
    Document,
    DocumentCategory,
    DocumentClassification,
    DocumentPage,
    DocumentVersion,
    ExtractedField,
    Finding,
    FindingEvidence,
    InfrastructureEvidence,
    ProcessingJob,
    ValidationRun,
    VerificationAttempt,
)
from app.services import anomaly
from app.services import checks as checks_mod
from app.services import classification as classify_mod
from app.services import extraction
from app.services import gemini as gemini_mod
from app.services import verification as verification_mod
from app.services.rule_engine import EvalContext, evaluate_rule, findings_from_evaluations
from app.services.storage import read_file

logger = logging.getLogger("resilient.processing")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _stage(stages: list[dict[str, Any]], name: str, detail: str = "", outcome: str = "ok") -> None:
    stages.append({"stage": name, "detail": detail, "outcome": outcome})


# ------------------------------------------------------------------
# Document processing: extract -> classify -> fields -> forensics
# ------------------------------------------------------------------

def process_document(db: Session, document_id: uuid.UUID, actor_id: uuid.UUID | None = None) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise LookupError("document not found")
    version = db.get(DocumentVersion, document.current_version_id)
    if version is None:
        raise LookupError("document has no stored version")

    job = ProcessingJob(
        job_type="process_document",
        application_id=document.application_id,
        document_id=document.id,
        document_version_id=version.id,
        payload={"version_no": version.version_no},
        status="running",
        attempts=1,
        started_at=now_utc(),
    )
    db.add(document)
    document.status = "processing"
    version.processing_status = "processing"
    db.flush()
    stages: list[dict[str, Any]] = []

    try:
        raw = read_file(version.storage_backend, version.storage_path)
        _stage(stages, "storage_read", f"{version.size_bytes} bytes read", "ok")

        # Reprocessing replaces derived rows for this version; page rows are unique per
        # (version, page_no) so stale rows must be cleared before re-inserting.
        db.execute(delete(DocumentPage).where(DocumentPage.document_version_id == version.id))
        db.execute(
            delete(DocumentClassification).where(DocumentClassification.document_version_id == version.id)
        )
        db.flush()

        result = extraction.extract_document(raw, version.file_ext)
        _stage(
            stages,
            "extract_text",
            f"{result['page_count']} page(s), OCR {'used' if result['ocr_used'] else 'not needed'}",
            "ok",
        )

        pages_rows = []
        for page in result["pages"]:
            rendered = render_page(raw, page["page_no"], version.file_ext)
            quality = dict(page["quality"])
            if rendered is not None:
                quality.update(anomaly.estimate_page_image_quality(rendered))
                if (page["page_no"] == 1 or not page["text"]) and page["text_source"] == "ocr":
                    phash = anomaly.phash_of_image(rendered)
                    page["phash"] = phash
                    ela = anomaly.ela_signals(rendered)
                    if ela:
                        quality["ela"] = ela
            else:
                page["phash"] = None
            rows = DocumentPage(
                document_version_id=version.id,
                page_no=page["page_no"],
                width=page["width"],
                height=page["height"],
                text_source=page["text_source"],
                page_text=page["text"],
                lines=page["lines"],
                ocr_confidence=page["ocr_confidence"],
                quality=quality,
                phash=page.get("phash"),
            )
            pages_rows.append(rows)
            db.add(rows)

        classification_payload = classify_doc(db, all_text(result), document.declared_category_code)
        version.page_count = result["page_count"]
        version.text_layer_chars = result["text_layer_chars"]
        version.ocr_used = result["ocr_used"]
        version.mean_ocr_confidence = result["mean_ocr_confidence"]
        version.quality = result["quality"]
        version.pdf_metadata = result["pdf_metadata"]
        version.forensics = anomaly.analyze_pdf_metadata(result["pdf_metadata"])
        version.processing_status = "processed"
        version.processed_at = now_utc()
        document.status = "processed"
        document.status_detail = None
        document.detected_category_code = classification_payload["predicted_category"]
        document.classification_confidence = classification_payload["confidence"]
        document.classification_method = classification_payload["method"]
        if not document.declared_category_code and classification_payload["predicted_category"]:
            document.declared_category_code = classification_payload["predicted_category"]
        db.add(
            DocumentClassification(
                document_version_id=version.id,
                predicted_category=classification_payload["predicted_category"],
                confidence=classification_payload["confidence"],
                method=classification_payload["method"],
                model_id=classification_payload.get("model_id"),
                scores=classification_payload["scores"],
                rationale=classification_payload["rationale"],
            )
        )
        db.flush()
        document.current_version_id = version.id

        _stage(stages, "classify", f"{classification_payload['method']}: {classification_payload['predicted_category']}", "ok")

        categories = db.scalars(select(DocumentCategory)).all()
        category = next((c for c in categories if c.code == (document.declared_category_code or classification_payload["predicted_category"])), None)
        extract_fields(db, document, version, result["pages"], category)
        _stage(stages, "extract_fields", f"{count_current_fields(db, document)} field(s)", "ok")

        if category and category.is_infrastructure:
            sync_infrastructure_evidence(db, document)
            _stage(stages, "infra_index", f"{category.code} registered as infrastructure evidence", "ok")

        job.status = "succeeded"
        job.stages = stages
        job.finished_at = now_utc()
        db.flush()
        return document

    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.error = str(exc)[:800]
        job.stages = stages
        job.finished_at = now_utc()
        document.status = "failed"
        document.status_detail = str(exc)[:500]
        version.processing_status = "failed"
        version.processing_error = str(exc)[:800]
        db.flush()
        logger.exception("processing failed for document %s", document_id)
        return document


def render_page(raw: bytes, page_no: int, file_ext: str) -> Image.Image | None:
    try:
        doc = fitz.open(stream=raw, filetype=file_ext if file_ext in ("pdf", "png", "jpeg") else "pdf")
        page = doc.load_page(page_no - 1)
        pix = page.get_pixmap(dpi=110, colorspace=fitz.csRGB)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        doc.close()
        return image
    except Exception:  # noqa: BLE001
        return None


def all_text(result: dict[str, Any]) -> str:
    return "\n".join(p["text"] for p in result["pages"])


def classify_doc(db: Session, text: str, declared_code: str | None):
    validator_rows = db.scalars(select(DocumentCategory)).all()
    return classify_mod.classify_document(text, validator_rows, declared_code)


def count_current_fields(db: Session, document: Document) -> int:
    rows = db.scalars(
        select(ExtractedField).where(
            ExtractedField.document_id == document.id, ExtractedField.is_current.is_(True)
        )
    ).all()
    return len(rows)


# ------------------------------------------------------------------
# Field extraction (deterministic patterns + Gemini assist for gaps)
# ------------------------------------------------------------------

def extract_fields(
    db: Session,
    document: Document,
    version: DocumentVersion,
    pages: list[dict[str, Any]],
    category: Any,
    actor_id: uuid.UUID | None = None,
) -> None:
    db.query(ExtractedField).filter(
        ExtractedField.document_id == document.id, ExtractedField.application_id == document.application_id
    ).delete()
    db.flush()

    if category is None:
        return
    expected = category.expected_fields or []
    model_id = get_settings().GEMINI_MODEL if get_settings().gemini_enabled else None

    for spec in expected:
        name = spec.get("name")
        label = spec.get("label", name)
        value_type = spec.get("type", "text")
        required = bool(spec.get("required", False))
        best: dict[str, Any] | None = None

        for page in pages:
            for line in page["lines"]:
                for pattern_rank, pattern in enumerate(determinism_patterns(name, value_type, spec)):
                    found = search_pattern(line["text"], pattern)
                    if not found:
                        continue
                    candidate_value, matched_snippet = found
                    confidence = float(line.get("conf", 0.6)) * (0.95 if page["text_source"] == "text_layer" else 0.9)
                    normalized = normalize_value(candidate_value, value_type)
                    if normalized is None:
                        continue
                    # More specific patterns (lower rank) win confidence ties: a label-specific
                    # match must beat a generic "issued on" hit from another line.
                    if best is None or (confidence, -pattern_rank) > (best["confidence"], -best["rank"]):
                        best = {
                            "value": candidate_value,
                            "normalized": normalized,
                            "confidence": confidence,
                            "rank": pattern_rank,
                            "page": page["page_no"],
                            "bbox": line["bbox"],
                            "line_text": matched_snippet,
                            "text_source": page["text_source"],
                        }

        if best is None and get_settings().gemini_enabled:
            assist = gemini_mod.normalize_field(label, value_type, "\n".join(p["text"] for p in pages))
            if assist and assist.get("found") and assist.get("value"):
                normalized = normalize_value(str(assist["value"]), value_type)
                if normalized:
                    best = {
                        "value": str(assist["value"]),
                        "normalized": normalized,
                        "confidence": float(assist.get("confidence", 0.5)),
                        "page": None,
                        "bbox": None,
                        "line_text": assist.get("evidence", ""),
                    }
                    model_id = get_settings().GEMINI_MODEL
                    method = "gemini_structured"
                else:
                    best = None
            else:
                best = None

        if best:
            if best.get("bbox"):
                method = "text_layer_pattern" if best.get("text_source") == "text_layer" else "ocr_pattern"
            else:
                method = "gemini_structured"
            db.add(
                ExtractedField(
                    application_id=document.application_id,
                    document_id=document.id,
                    document_version_id=version.id,
                    field_name=name,
                    label=label,
                    value_type=value_type,
                    raw_value=best["value"],
                    normalized_value=best["normalized"],
                    confidence=round(min(max(best["confidence"], 0.0), 1.0), 4),
                    page_no=best["page"],
                    bbox=best["bbox"],
                    extraction_method=method,
                    model_id=model_id if method == "gemini_structured" else None,
                    status="extracted" if best["confidence"] >= get_settings().OCR_LOW_CONFIDENCE else "low_confidence",
                )
            )
        elif required:
            db.add(
                ExtractedField(
                    application_id=document.application_id,
                    document_id=document.id,
                    document_version_id=version.id,
                    field_name=name,
                    label=label,
                    value_type=value_type,
                    raw_value=None,
                    normalized_value=None,
                    confidence=None,
                    page_no=None,
                    bbox=None,
                    extraction_method="not_found",
                    model_id=None,
                    status="missing",
                )
            )
    db.flush()


def determinism_patterns(field_name: str, value_type: str, spec: dict[str, Any]) -> list[str]:
    import re as _re

    custom = spec.get("patterns")
    if isinstance(custom, list) and custom:
        return [str(p) for p in custom]
    label_words = (spec.get("label", field_name).replace("_", " ")).lower()
    base = _re.escape(label_words)
    if value_type == "date":
        patterns = [rf"(?i){base}\s*[:\-]?\s*([0-9]{{1,2}}[-/.][0-9]{{1,2}}[-/.][0-9]{{2,4}})"]
        # Only add the generic variants when the field's own label is about them, otherwise
        # e.g. an expiry field would happily pick up an unrelated "Issued on" line.
        if any(word in label_words for word in ("valid", "expir")):
            patterns.append(
                rf"(?i)(valid|expiry|expires|expiration)\s*(?:until|upto|up to|till|date)?\D{{0,12}}"
                rf"([0-9]{{1,2}}[-/.][0-9]{{1,2}}[-/.][0-9]{{2,4}})"
            )
        if any(word in label_words for word in ("issue", "date", "dated")):
            patterns.append(
                rf"(?i)(issue[ds]?\s*(?:on|date)|date\s*of\s*issue)\D{{0,12}}"
                rf"([0-9]{{1,2}}[-/.][0-9]{{1,2}}[-/.][0-9]{{2,4}})"
            )
        return patterns
    if value_type == "number":
        return [rf"(?i){base}\s*[:\-]?\s*([0-9][0-9,\.]{{0,12}})"]
    if value_type == "identifier":
        return [rf"(?i){base}\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]{{2,24}})"]
    return [rf"(?i){base}\s*[:\-]?\s*([^\n|:;]{{2,80}})"]


def search_pattern(line_text: str, pattern: str) -> tuple[str, str] | None:
    import re as _re

    match = _re.search(pattern, line_text)
    if not match:
        return None
    groups = [g for g in match.groups() if g]
    value = (groups[-1] if groups else match.group(0)).strip(" :,-.")
    if not value:
        return None
    return value, line_text.strip()[:200]


def normalize_value(value: str, value_type: str) -> str | None:
    import re as _re

    value = str(value).strip()
    if not value:
        return None
    if value_type == "date":
        match = _re.search(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", value)
        if not match:
            return None
        d, m, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if y < 100:
            y += 2000
        if d > 12 and m <= 12:
            pass
        return f"{y:04d}-{min(m,12):02d}-{min(d,31):02d}"
    if value_type == "number":
        cleaned = value.replace(",", "").replace("â‚¹", "")
        match = _re.search(r"-?\d+(\.\d+)?", cleaned)
        return str(float(match.group(0))) if match else None
    if value_type == "identifier":
        return value.upper().replace(" ", "")
    return value[:200]


# ------------------------------------------------------------------
# Infrastructure evidence bookkeeping
# ------------------------------------------------------------------

def sync_infrastructure_evidence(db: Session, document: Document) -> None:
    existing = db.scalars(
        select(InfrastructureEvidence).where(InfrastructureEvidence.document_id == document.id)
    ).first()
    category = db.get(DocumentCategory, document.declared_category_code or document.detected_category_code)
    if existing is None:
        db.add(
            InfrastructureEvidence(
                application_id=document.application_id,
                document_id=document.id,
                evidence_type="other" if category is None else _infra_type(category.code),
            )
        )
        db.flush()


def _infra_type(category_code: str) -> str:
    mapping = {
        "building_plan": "floor_plan",
        "lab_photo": "lab_photo",
        "classroom_photo": "classroom_photo",
        "library_photo": "library_photo",
        "building_certificate": "building_certificate",
    }
    return mapping.get(category_code, "other")


# ------------------------------------------------------------------
# Application validation run
# ------------------------------------------------------------------

def validate_application(db: Session, application_id: uuid.UUID, actor_id: uuid.UUID | None) -> ValidationRun:
    application = db.get(Application, application_id)
    if application is None:
        raise LookupError("application not found")

    run = ValidationRun(application_id=application.id, status="running", triggered_by=actor_id)
    db.add(run)
    db.flush()

    summary: dict[str, Any] = {"counts": {}, "errors": []}
    stages: list[dict[str, Any]] = []

    try:
        documents, fields_by_document, active_fields = checks_mod.collect_document_fields(db, application)
        doc_by_id = {str(d.id): d for d in documents}
        ctx = EvalContext(
            application=application,
            as_of=run.evaluation_date,
            session=db,
            documents=documents,
            active_fields=active_fields,
            fields_by_document=fields_by_document,
        )

        stages.append({"stage": "rules_start", "detail": ""})
        versions = db.scalars(
            select()
            .select_from(ComplianceRuleJoin := load_rule_versions())
        ).all() if False else active_rule_versions(db)
        evaluations = []
        for rv in versions:
            evaluation = evaluate_rule(ctx, rv, run.id)
            db.add(evaluation)
            db.flush()
            evaluations.append(evaluation)

        new_findings = findings_from_evaluations(evaluations, application.institution_id)
        rule_evidence: dict[str, list[dict]] = {}
        for finding in new_findings:
            source_eval = next((e for e in evaluations if e.id == finding.rule_evaluation_id), None)
            rule_evidence[finding.fingerprint] = list(source_eval.evidence or []) if source_eval else []
        rule_map = upsert_findings(db, new_findings)
        for fingerprint, items in rule_evidence.items():
            effective = rule_map.get(fingerprint)
            existing_evidence = (
                db.scalars(select(FindingEvidence).where(FindingEvidence.finding_id == effective.id)).all()
                if effective
                else []
            )
            if existing_evidence:
                continue
            for item in items:
                db.add(
                    FindingEvidence(
                        finding_id=effective.id,
                        document_id=item.get("document_id"),
                        document_version_id=item.get("document_version_id"),
                        extracted_field_id=item.get("extracted_field_id"),
                        page_no=item.get("page_no"),
                        bbox=item.get("bbox"),
                        excerpt=item.get("excerpt"),
                        role=item.get("role", "primary") or "primary",
                        label=item.get("label"),
                    )
                )
        db.flush()

        xdoc = checks_mod.build_consistency_findings(db, application, run.id, fields_by_document, doc_by_id)
        db.flush()
        upsert_findings(db, xdoc)

        dupes = checks_mod.exact_duplicates(db, application)
        dup_findings = duplicate_findings(application, run.id, dupes, doc_by_id)
        dup_evidence: dict[str, list[dict]] = {}
        for finding in dup_findings:
            group = next((g for g in dupes if g["sha256"] == finding.observed_value), None)
            if group:
                dup_evidence[finding.fingerprint] = group["members"]
        dup_map = upsert_findings(db, dup_findings)
        for fingerprint, members in dup_evidence.items():
            effective = dup_map.get(fingerprint)
            existing_evidence = (
                db.scalars(select(FindingEvidence).where(FindingEvidence.finding_id == effective.id)).all()
                if effective
                else []
            )
            if existing_evidence:
                continue
            for index, member in enumerate(members):
                db.add(
                    FindingEvidence(
                        finding_id=effective.id,
                        document_id=member["document"],
                        document_version_id=member["version"],
                        role="primary" if index == 0 else "duplicate_of",
                        label=member["name"],
                    )
                )
        db.flush()

        stage_name = "meta_findings"
        stages.append({"stage": stage_name, "detail": f"{len(new_findings) + len(xdoc) + len(dup_findings)} finding(s) this run"})

        unreadable = [d for d in documents if d.status in ("failed", "unreadable")]
        low_conf = [f for f in active_fields if f.status == "low_confidence"]
        missing_fields = [f for f in active_fields if f.status == "missing"]

        outcome_counts: dict[str, int] = {}
        for evaluation in evaluations:
            outcome_counts[evaluation.outcome] = outcome_counts.get(evaluation.outcome, 0) + 1

        open_findings = db.scalars(
            select(Finding).where(Finding.application_id == application.id, Finding.status.in_(["open", "under_review", "needs_clarification"]))
        ).all()
        severity_counts: dict[str, int] = {}
        for f in open_findings:
            severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1

        summary = {
            "counts": {
                "documents": len(documents),
                "extracted_fields": len(active_fields),
                "low_confidence_fields": len(low_conf),
                "missing_fields": len(missing_fields),
                "unreadable_documents": len(unreadable),
                "rule_evaluations": len(evaluations),
                **{f"outcome_{k}": v for k, v in outcome_counts.items()},
                "open_findings": len(open_findings),
                **{f"severity_{k}": v for k, v in severity_counts.items()},
            },
            "duplicate_groups": len(dupes),
        }

        run.status = "completed"
        run.summary = summary
        run.finished_at = now_utc()
        application.last_validated_at = now_utc()
        finalize_status(db, application, summary, actor_id)
        db.flush()
        return run
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        recovery = _fresh_session()
        failed = recovery.get(ValidationRun, run.id)
        if failed:
            failed.status = "failed"
            failed.summary = {"errors": [str(exc)[:500]]}
            failed.finished_at = now_utc()
        recovery.commit()
        recovery.close()
        raise


def load_rule_versions():
    from app.models import RuleVersion

    return RuleVersion


def active_rule_versions(db: Session):
    from app.models import ComplianceRule, RuleVersion

    return db.scalars(
        select(RuleVersion)
        .join(ComplianceRule, ComplianceRule.id == RuleVersion.rule_id)
        .where(
            ComplianceRule.is_active.is_(True),
            RuleVersion.id == ComplianceRule.current_version_id,
        )
    ).all()


def attach_rule_finding_evidence(db: Session, finding: Finding, evaluations: list) -> None:
    source_eval = next((e for e in evaluations if e.id == finding.rule_evaluation_id), None)
    evidence_items = (source_eval.evidence or []) if source_eval else []
    for item in evidence_items:
        db.add(
            FindingEvidence(
                finding_id=finding.id,
                document_id=item.get("document_id"),
                document_version_id=item.get("document_version_id"),
                extracted_field_id=item.get("extracted_field_id"),
                page_no=item.get("page_no"),
                bbox=item.get("bbox"),
                excerpt=item.get("excerpt"),
                role=item.get("role", "primary") or "primary",
                label=item.get("label"),
            )
        )


def attach_xdoc_evidence(db: Session, finding: Finding, doc_by_id: dict, fields_by_document: dict) -> None:
    # detail pattern: â€œAâ€ in doc X vs â€œBâ€ in doc Y â€” recreate associations by scanning fields
    doc_titles = list(doc_by_id.keys())
    items = []
    for doc_key in doc_titles:
        for f in (fields_by_document.get(doc_key) or []):
            if f.is_current and finding.expected_value and f.normalized_value and (f.normalized_value.upper().strip() in (finding.observed_value or "")):
                doc = doc_by_id[doc_key]
                items.append(
                    {
                        "document_id": doc.id,
                        "document_version_id": f.document_version_id,
                        "extracted_field_id": f.id,
                        "page_no": f.page_no,
                        "bbox": f.bbox,
                        "excerpt": f.raw_value,
                        "role": "source_a" if not any(e is True for e in [i.get("role") == "source_a" for i in items]) else "source_b",
                    }
                )
    for item in items:
        db.add(FindingEvidence(**item, finding_id=finding.id))


def attach_dup_evidence(db: Session, finding: Finding, dupes: list[dict]) -> None:
    for group in dupes:
        if finding.observed_value and finding.observed_value not in str(group["sha256"]):
            continue
        members = group["members"]
        for index, member in enumerate(members):
            db.add(
                FindingEvidence(
                    finding_id=finding.id,
                    document_id=member["document"],
                    document_version_id=member["version"],
                    role="primary" if index == 0 else "duplicate_of",
                    label=member["name"],
                )
            )


def duplicate_findings(
    application: Application, run_id: uuid.UUID, dupes: list[dict], doc_by_id: dict
) -> list[Finding]:
    findings: list[Finding] = []
    for group in dupes:
        sha = group["sha256"]
        titles = [doc_by_id.get(str(m["document"])) for m in group["members"]]
        title_texts = [t.title if t else "?" for t in titles]
        findings.append(
            Finding(
                application_id=application.id,
                institution_id=application.institution_id,
                validation_run_id=run_id,
                fingerprint=f"EXACT_DUPLICATE::{sha}"[:400],
                finding_code="EXACT_DUPLICATE",
                category="authenticity",
                severity="medium",
                status="open",
                title="Exact duplicate documents detected",
                description=(
                    "Two or more submitted documents are byte-identical (same SHA-256). "
                    "A photocopy may be legitimate (e.g. same certificate attached twice), but this "
                    "is a preliminary indicator and should be reviewed rather than assumed acceptable."
                ),
                expected_value="Distinct documents per requirement",
                observed_value=sha,
                recommended_action="Request uniquely issued documents or confirm duplication is expected.",
                detection_method="sha256_exact_match",
                confidence=None,
                document_id=group["members"][0]["document"],
            )
        )
    return findings


def upsert_findings(db: Session, new_findings: list[Finding]) -> dict[str, Finding]:
    """Deduplicates findings by fingerprint. Existing findings keep their status and
    review history; we only bump last_detected_at / latest evaluation linkage.
    Returns candidate_fingerprint -> effective Finding (for evidence attachment)."""
    mapping: dict[str, Finding] = {}
    if not new_findings:
        return mapping
    application_ids = list({f.application_id for f in new_findings})
    fingerprints = [f.fingerprint for f in new_findings]
    existing_rows = db.scalars(
        select(Finding).where(
            Finding.application_id.in_(application_ids),
            Finding.fingerprint.in_(fingerprints),
        )
    ).all()
    existing_map = {f.fingerprint: f for f in existing_rows}
    for candidate in new_findings:
        current = existing_map.get(candidate.fingerprint)
        if current is None:
            db.add(candidate)
            db.flush()
            mapping[candidate.fingerprint] = candidate
            existing_map[candidate.fingerprint] = candidate
        else:
            current.last_detected_at = now_utc()
            current.validation_run_id = candidate.validation_run_id
            for field_name in ("rule_evaluation_id", "rule_version_id"):
                new_value = getattr(candidate, field_name)
                if new_value:
                    setattr(current, field_name, new_value)
            mapping[candidate.fingerprint] = current
    db.flush()
    return mapping


def finalize_status(db: Session, application: Application, summary: dict, actor_id: uuid.UUID | None) -> None:
    counts = summary.get("counts", {})
    if application.status == "draft":
        # validation only meaningful once submitted; leave workflow status untouched for drafts
        pass
    elif counts.get("open_findings", 0) > 0 and application.status in ("submitted", "processing", "needs_correction"):
        application.status = "under_review"


# ------------------------------------------------------------------
# Application submit / status transitions
# ------------------------------------------------------------------

VALID_TRANSITIONS: dict[str, list[str]] = {
    "draft": ["submitted", "closed"],
    "submitted": ["processing", "under_review", "needs_correction", "closed"],
    "processing": ["under_review", "needs_correction", "closed"],
    "needs_correction": ["submitted", "under_review", "closed"],
    "under_review": ["reviewed", "needs_correction", "closed"],
    "reviewed": ["closed"],
    "closed": [],
}


def transition_status(
    db: Session, application: Application, to_status: str, actor_id: uuid.UUID | None, reason: str | None
) -> None:
    if to_status not in VALID_TRANSITIONS.get(application.status, []):
        raise ValueError(f"Invalid status transition {application.status} -> {to_status}")
    history = ApplicationStatusHistory(
        application_id=application.id,
        from_status=application.status,
        to_status=to_status,
        actor_id=actor_id,
        reason=reason,
    )
    if to_status == "submitted":
        application.submitted_at = now_utc()
    application.status = to_status
    db.add(history)
    db.flush()


def _fresh_session() -> Session:
    from app.db import SessionFactory

    return SessionFactory()

