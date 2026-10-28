import fitz
import pytest

from app.services import extraction
from app.services.processing import determinism_patterns, normalize_value, search_pattern


def _pdf(lines: list[str]) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 80
    for line in lines:
        page.insert_text((60, y), line, fontsize=11, fontname="Times-Roman")
        y += 22
    data = doc.tobytes()
    doc.close()
    return data


NOC_LINES = [
    "Fire Safety No Objection Certificate",
    "NOC number: FSN-2025-00918",
    "Issuing authority: Directorate of Fire Services, Maharashtra",
    "Institution name: Demo Valley Institute of Technology",
    "Issued on: 12/03/2025",
    "Valid until: 25/08/2027",
]


def test_extract_document_uses_text_layer_with_normalized_boxes():
    result = extraction.extract_document(_pdf(NOC_LINES), "pdf")

    assert result["page_count"] == 1
    assert result["ocr_used"] is False
    page = result["pages"][0]
    assert page["text_source"] == "text_layer"
    assert page["ocr_confidence"] == 0.99
    assert "FSN-2025-00918" in page["text"]
    assert page["lines"], "text layer must produce line records"
    for line in page["lines"]:
        x0, y0, x1, y1 = line["bbox"]
        assert 0.0 <= x0 <= x1 <= 1.0
        assert 0.0 <= y0 <= y1 <= 1.0


def test_extract_document_rejects_non_pdf_bytes():
    with pytest.raises(ValueError):
        extraction.extract_document(b"definitely not a document", "pdf")


def _field(spec: dict, line: str) -> tuple[str, str] | None:
    for pattern in determinism_patterns(spec["name"], spec["type"], spec):
        found = search_pattern(line, pattern)
        if found:
            return found
    return None


def test_expiry_field_never_picks_up_issue_date():
    """Regression: generic 'Issued on' pattern used to win over the label-specific one."""
    spec = {"name": "valid_until", "label": "Valid until", "type": "date"}
    patterns = determinism_patterns(spec["name"], spec["type"], spec)

    assert search_pattern("Issued on: 12/03/2025", patterns[0]) is None
    assert _field(spec, "Valid until: 25/08/2027") == ("25/08/2027", "Valid until: 25/08/2027")
    assert _field(spec, "Valid upto 25/08/2027")[0] == "25/08/2027"


def test_issue_date_field_matches_labelled_line():
    spec = {"name": "issued_on", "label": "Issued on", "type": "date"}
    assert _field(spec, "Issued on: 12/03/2025")[0] == "12/03/2025"


def test_normalize_date_is_iso_and_rejects_garbage():
    assert normalize_value("25/08/2027", "date") == "2027-08-25"
    assert normalize_value("12-03-2025", "date") == "2025-03-12"
    assert normalize_value("no date here", "date") is None


def test_normalize_number_and_identifier():
    assert normalize_value("1,24,500", "number") == "124500.0"
    assert normalize_value("fsn 2025", "identifier") == "FSN2025"
