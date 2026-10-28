from datetime import date

from app.seed import DOC_CATEGORIES
from app.services import verification


def test_same_handles_dates_numbers_and_text():
    assert verification._same("2027-08-25", "2027-08-25") is True
    assert verification._same("2027-08-25", "25/08/2027") is True
    assert verification._same("25/08/2027", "2027-08-25") is True
    assert verification._same("1,24,500", "124500") is True
    assert verification._same("FSN-2025-00918", "fsn-2025-00918") is True
    assert verification._same("FSN-2025-00918", "FSN-2019-00505") is False
    assert verification._same(None, "anything") is False
    assert verification._same("Pune", "Delhi") is False


def test_as_date_parses_supported_layouts_and_rejects_text():
    assert verification._as_date("2027-08-25") == date(2027, 8, 25)
    assert verification._as_date("25/08/2027") == date(2027, 8, 25)
    assert verification._as_date("25-08-2027") == date(2027, 8, 25)
    assert verification._as_date("not a date") is None
    assert verification._as_date(None) is None


def test_compare_map_uses_extracted_field_names():
    """Regression: mapping to non-existent fields made every check compare the reference
    with itself and report 'verified' without inspecting the document."""
    expected_by_category = {
        category["code"]: {field["name"] for field in category["expected_fields"]}
        for category in DOC_CATEGORIES
    }

    assert verification.COMPARE_MAP["fire_noc"], "fire NOC comparison must not be empty"
    for ref_key, field_name in verification.COMPARE_MAP["fire_noc"]:
        assert field_name in expected_by_category["fire_noc"], f"{ref_key} -> {field_name} not extracted"
    for _ref_key, field_name in verification.COMPARE_MAP["aicte_record"]:
        assert field_name in expected_by_category["aicte_letter"]
    for _ref_key, field_name in verification.COMPARE_MAP["land_record"]:
        assert field_name in expected_by_category["land_record"]


def test_legacy_record_types_are_mapped():
    assert verification._LEGACY_RECORD_TYPES["reference"] == "fire_noc"
    assert verification._LEGACY_RECORD_TYPES["institution"] == "aicte_record"
