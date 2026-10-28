import uuid

from app.models import Application, Document, ExtractedField
from app.services import checks


class FakeSession:
    """Minimal Session stand-in: findings are collected, flush is a no-op."""

    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass

    @property
    def findings(self):
        return [row for row in self.added if row.__class__.__name__ == "Finding"]


def _app() -> Application:
    return Application(id=uuid.uuid4(), institution_id=uuid.uuid4(), category_code="EOA")


def _doc(title: str) -> Document:
    return Document(id=uuid.uuid4(), title=title, is_deleted=False, status="processed")


def _field(doc: Document, name: str, value: str, value_type: str = "text") -> ExtractedField:
    return ExtractedField(
        id=uuid.uuid4(),
        document_id=doc.id,
        document_version_id=uuid.uuid4(),
        field_name=name,
        value_type=value_type,
        raw_value=value,
        normalized_value=value,
        is_current=True,
        page_no=1,
    )


def _run(fields_by_document, doc_by_id):
    db = FakeSession()
    app = _app()
    checks.build_consistency_findings(db, app, uuid.uuid4(), fields_by_document, doc_by_id)
    return db.findings


def test_identifier_conflict_becomes_evidence_linked_finding():
    doc_a, doc_b = _doc("Previous EoA Letter"), _doc("Faculty List")
    field_a = _field(doc_a, "aicte_pid", "DEMO-PID-1001", "identifier")
    field_b = _field(doc_b, "aicte_pid", "DEMO-PID-2002", "identifier")

    findings = _run(
        {str(doc_a.id): [field_a], str(doc_b.id): [field_b]},
        {str(doc_a.id): doc_a, str(doc_b.id): doc_b},
    )

    assert len(findings) == 1
    assert findings[0].finding_code == "XDOC_CONFLICT"
    assert findings[0].severity == "high"


def test_institution_name_typo_is_tolerated():
    doc_a, doc_b = _doc("Fire NOC"), _doc("EoA Letter")
    field_a = _field(doc_a, "institution_name", "Demo Valley Institute of Technology")
    field_b = _field(doc_b, "institution_name", "Demo Valley Institue of Technology")

    findings = _run(
        {str(doc_a.id): [field_a], str(doc_b.id): [field_b]},
        {str(doc_a.id): doc_a, str(doc_b.id): doc_b},
    )
    assert findings == []


def test_document_specific_fields_are_not_compared_across_documents():
    """Regression: NOC numbers/issue dates/issuers differ legitimately between certificates."""
    doc_a, doc_b = _doc("Fire NOC 2025"), _doc("Building Certificate")
    fields_a = [
        _field(doc_a, "noc_number", "FSN-2025-00918", "identifier"),
        _field(doc_a, "issued_on", "2025-03-12", "date"),
        _field(doc_a, "issuer_name", "Directorate of Fire Services"),
        _field(doc_a, "valid_until", "2027-08-25", "date"),
    ]
    fields_b = [
        _field(doc_b, "certificate_no", "BCC/2016/4410", "identifier"),
        _field(doc_b, "issued_on", "2016-12-01", "date"),
        _field(doc_b, "issuer_name", "Pune Municipal Corporation"),
    ]

    findings = _run(
        {str(doc_a.id): fields_a, str(doc_b.id): fields_b},
        {str(doc_a.id): doc_a, str(doc_b.id): doc_b},
    )
    assert findings == []


def test_substantive_conflict_thresholds():
    assert checks._substantive_conflict(["A", "B"], "identifier") is not None
    assert checks._substantive_conflict(["Demo Valley Institute", "Demo Valley Institute"], "text") is None
    assert checks._substantive_conflict(["Pune Municipal Corporation", "Delhi Municipal Council"], "text") is not None
