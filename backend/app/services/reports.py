"""Scrutiny report generation: ReportLab PDF built exclusively from persisted data."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Application,
    Document,
    ExtractedField,
    Finding,
    ReviewAction,
    RuleEvaluation,
    ValidationRun,
    VerificationAttempt,
)
from app.services import storage

logger = logging.getLogger("resilient.reports")

NAVY = colors.HexColor("#173D35")
INK = colors.HexColor("#1a2233")
MUTED = colors.HexColor("#5b6675")
LIGHT = colors.HexColor("#E8F1ED")


def generate_scrutiny_report(
    db: Session, application: Application, actor_name: str
) -> dict[str, Any]:
    story: list[Any] = []
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Title"], fontSize=20, textColor=NAVY, spaceAfter=2)
    sub = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=9.5, textColor=MUTED)
    h2 = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontSize=12.5, textColor=NAVY, spaceBefore=10, spaceAfter=4
    )
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9, leading=12.5, textColor=INK)
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8, leading=11, textColor=MUTED)
    warn = ParagraphStyle(
        "Warn", parent=styles["Normal"], fontSize=8.5, leading=11.5, textColor=colors.HexColor("#8a4b00")
    )

    reference_code = f"REP-{application.academic_year.replace('-', '')}-{uuid.uuid4().hex[:8].upper()}"
    generated_at = datetime.now(timezone.utc)

    story.append(Paragraph("Resilient AI — Preliminary Application Scrutiny Report", h1))
    story.append(
        Paragraph(
            f"Reference {reference_code} · generated {generated_at:%d %b %Y %H:%M} UTC · by {actor_name}",
            sub,
        )
    )
    story.append(HRFlowable(width="100%", thickness=1.2, color=NAVY, spaceBefore=6, spaceAfter=10))

    story.append(
        KeepTogether(
            [
                Paragraph("1. Application", h2),
                _kv_table(
                    [
                        ("Application", f"{application.reference_no} — {application.title}"),
                        ("Institution", application.institution.name),
                        ("AICTE PID", application.institution.aicte_pid),
                        ("Category", application.category_code),
                        ("Academic year", application.academic_year),
                        ("Workflow status", application.status),
                        ("Synthetic demo data", "yes" if application.is_synthetic else "no"),
                    ],
                    styles,
                ),
            ]
        )
    )

    documents = db.scalars(
        select(Document).where(Document.application_id == application.id, Document.is_deleted.is_(False))
    ).all()
    doc_rows = [
        (
            d.title,
            d.declared_category_code or d.detected_category_code or "—",
            f"v{d.current_version_no}" if d.current_version_no else "—",
            d.status,
        )
        for d in documents
    ]
    story.append(Paragraph("2. Documents received", h2))
    story.append(_simple_table(doc_rows, ["Title", "Category", "Ver.", "Processing"], styles))
    docs_missing_category = [
        d for d in documents if not (d.declared_category_code or d.detected_category_code)
    ]
    if docs_missing_category:
        story.append(
            Paragraph(
                f"{len(docs_missing_category)} document(s) could not be classified confidently.",
                small,
            )
        )

    fields = db.scalars(
        select(ExtractedField).where(
            ExtractedField.application_id == application.id, ExtractedField.is_current.is_(True)
        )
    ).all()
    story.append(Paragraph("3. Extracted fields (values traceable to their source page)", h2))
    field_rows = [
        (
            f.label,
            f.status,
            _fmt(f.normalized_value or f.raw_value),
            _fmt(f.confidence),
            _fmt(f.page_no) if f.page_no else "—",
        )
        for f in fields
    ]
    story.append(_simple_table(field_rows, ["Field", "Status", "Value", "Conf.", "Page"], styles))
    low = [f for f in fields if f.status == "low_confidence" or f.status == "missing"]
    if low:
        story.append(
            Paragraph(
                "Fields marked low_confidence/missing were not reliably read and should be confirmed by a human.",
                warn,
            )
        )

    runs = db.scalars(
        select(ValidationRun)
        .where(ValidationRun.application_id == application.id)
        .order_by(ValidationRun.started_at.desc())
        .limit(5)
    ).all()
    if runs:
        latest = runs[0]
        evals = db.scalars(
            select(RuleEvaluation).where(RuleEvaluation.validation_run_id == latest.id)
        ).all()
        eval_rows = [
            (
                str(e.rule_version.rule.rule_code) + f" v{e.rule_version.version}",
                e.outcome,
                e.rule_version.severity + (" (demo rule)" if e.rule_version.is_demo_rule else ""),
                _truncate(e.explanation, 120),
            )
            for e in evals
        ]
        story.append(Paragraph("4. Rule evaluation (latest validation run)", h2))
        story.append(_simple_table(eval_rows, ["Rule", "Outcome", "Severity", "Explanation"], styles))

    findings = db.scalars(
        select(Finding).where(Finding.application_id == application.id).order_by(Finding.severity)
    ).all()
    ordering = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings = sorted(findings, key=lambda f: (ordering.get(f.severity, 9), f.first_detected_at))
    story.append(Paragraph("5. Findings and preliminary indicators", h2))
    if findings:
        for f in findings:
            badges = ("advisory", "simulated" if f.is_simulated else "automated")
            story.append(
                KeepTogether(
                    [
                        Paragraph(
                            f"{_sev_prefix(f.severity)} [{f.severity.upper()}] {f.title} "
                            f"— status: {f.status} · source: {f.detection_method.replace('_', ' ')} "
                            f"· flags: {', '.join(badges)}",
                            body,
                        ),
                        Paragraph(f"Description: {f.description}", small),
                        Paragraph(
                            f"Expected: {f.expected_value or '—'} · Observed: {f.observed_value or '—'}",
                            small,
                        ),
                        Paragraph(f"Recommended action: {f.recommended_action or '—'}", small),
                        Spacer(1, 4),
                    ]
                )
            )
    else:
        story.append(Paragraph("No automated findings recorded.", body))

    verifications = db.scalars(
        select(VerificationAttempt)
        .where(VerificationAttempt.application_id == application.id)
        .order_by(VerificationAttempt.requested_at)
    ).all()
    story.append(Paragraph("6. Source verification", h2))
    if verifications:
        v_rows = [
            (
                str(v.provider_code),
                _fmt(v.reference_no),
                v.status,
                "SIMULATED" if v.is_simulated else "LIVE",
                v.latency_ms and f"{v.latency_ms} ms" or "",
            )
            for v in verifications
        ]
        story.append(_simple_table(v_rows, ["Provider", "Ref no.", "Status", "Mode", "Latency"], styles))
        story.append(
            Paragraph(
                "Simulated results are synthetic and must never be presented as live government/integration confirmation.",
                warn,
            )
        )
    else:
        story.append(Paragraph("No verification attempts recorded.", body))

    actions = db.scalars(
        select(ReviewAction)
        .where(ReviewAction.application_id == application.id)
        .order_by(ReviewAction.created_at)
        .limit(30)
    ).all()
    story.append(Paragraph("7. Reviewer actions", h2))
    if actions:
        a_rows = [
            (str(a.created_at)[:16], a.action, a.reason_code or "", _truncate(a.comment or "", 80))
            for a in actions
        ]
        story.append(_simple_table(a_rows, ["When (UTC)", "Action", "Reason", "Comment"], styles))
    else:
        story.append(Paragraph("No reviewer actions recorded yet.", body))

    story.append(Spacer(1, 18))
    story.append(HRFlowable(width="100%", thickness=0.8, color=MUTED, spaceAfter=6))
    story.append(
        Paragraph(
            "This report is an automated decision-support artifact generated by Resilient AI. It is not an "
            "official AICTE certificate, approval order, or adverse finding against any institution. Any automated "
            "result, including anomaly indicators labelled simulated/pending/unavailable, requires review by "
            "authorized AICTE officials before regulatory effect.",
            small,
        )
    )

    path_obj = SimpleDocTemplate(
        buffer := bytearray(),
        pagesize=A4,
        title=f"Resilient AI scrutiny report {application.reference_no}",
        author="Resilient AI",
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )
    path_obj.build(story, onFirstPage=_footer(small, reference_code), onLaterPages=_footer(small, reference_code))
    pdf_bytes = bytes(buffer)

    meta = storage.save_report_file(application.reference_no, pdf_bytes, "scrutiny")
    return {
        "reference_code": reference_code,
        "storage": meta,
        "generated_at": generated_at,
        "includes_simulated": any(v.is_simulated for v in verifications),
        "summary": {
            "documents": len(documents),
            "fields": len(fields),
            "findings": len(findings),
            "verifications": len(verifications),
            "reviews": len(actions),
        },
    }


def _footer(style, reference_code: str):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.grey)
        canvas.drawString(18 * mm, 10 * mm, f"Resilient AI · {reference_code}")
        canvas.drawRightString(
            A4[0] - 18 * mm, 10 * mm, f"Page {canvas.getPageNumber()}"
        )
        canvas.restoreState()

    return draw


def _sev_prefix(severity: str) -> str:
    return {
        "critical": "//",
        "high": "/",
        "medium": "-",
        "low": ".",
        "info": "·",
    }.get(severity, "·")


def _kv_table(rows, styles) -> Table:
    data = [[Paragraph(f"<b>{k}</b>", styles["Normal"]), Paragraph(str(v), styles["Normal"])] for k, v in rows]
    table = Table(data, colWidths=[40 * mm, 120 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#F3F8F5")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _simple_table(rows, headers, styles, width=170 * mm) -> Table:
    if not rows:
        return Paragraph("Nothing recorded.", styles["Normal"])
    data = [headers] + [list(r) for r in rows]
    table = Table(data, colWidths=[width / len(headers)] * len(headers), repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                ("TEXTCOLOR", (0, 0), (-1, 0), NAVY),
                ("FONTSIZE", (0, 0), (-1, -1), 7.8),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c7d2cb")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FAF8")]),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) or (isinstance(value, int) and not isinstance(value, bool) and value > 10 ** 12):
        return f"{value:.2f}" if isinstance(value, float) else str(value)
    if isinstance(value, int) and 0 <= value <= 1:
        return f"{value:.2f}"
    return str(value)[:80]


def _truncate(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"
