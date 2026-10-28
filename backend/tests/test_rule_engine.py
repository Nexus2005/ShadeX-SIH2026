import uuid
from datetime import date

from app.models import Application, ComplianceRule, Document, ExtractedField, RuleVersion
from app.services import rule_engine as re_

AS_OF = date(2026, 10, 29)


def _application(**overrides) -> Application:
    return Application(
        id=uuid.uuid4(),
        institution_id=uuid.uuid4(),
        category_code=overrides.pop("category_code", "EOA"),
        declared_data=overrides.pop("declared_data", {}),
        **overrides,
    )


def _rule(definition: dict, **overrides) -> RuleVersion:
    rule = RuleVersion(
        id=uuid.uuid4(),
        version=overrides.pop("version", 1),
        definition=definition,
        applies_to=overrides.pop("applies_to", ["EOA"]),
        severity=overrides.pop("severity", "high"),
        effective_from=overrides.pop("effective_from", date(2024, 1, 1)),
        effective_to=overrides.pop("effective_to", None),
        **overrides,
    )
    rule.rule = ComplianceRule(rule_code="TEST_RULE", name="test rule", category="completeness")
    return rule


def _document(category: str, **overrides) -> Document:
    return Document(
        id=uuid.uuid4(),
        application_id=uuid.uuid4(),
        title=overrides.pop("title", "Doc"),
        declared_category_code=category,
        detected_category_code=category,
        status=overrides.pop("status", "processed"),
        is_deleted=overrides.pop("is_deleted", False),
    )


def _field(document: Document, name: str, value: str, value_type: str = "date") -> ExtractedField:
    return ExtractedField(
        id=uuid.uuid4(),
        document_id=document.id,
        document_version_id=uuid.uuid4(),
        field_name=name,
        value_type=value_type,
        raw_value=value,
        normalized_value=value,
        is_current=True,
        page_no=1,
    )


def _ctx(application, documents=(), fields_by_document=None, active_fields=()):
    return re_.EvalContext(
        application=application,
        as_of=AS_OF,
        session=None,
        documents=list(documents),
        active_fields=list(active_fields),
        fields_by_document=fields_by_document or {},
    )


def test_required_document_passes_and_fails_with_evidence():
    rule = _rule({"evaluator": "required_document", "document_category": "fire_noc"})
    doc = _document("fire_noc")

    passed = re_._eval_required_document(_ctx(_application(), [doc]), rule)
    assert passed.outcome == "pass"
    assert passed.finding is None
    assert passed.evidence[0]["document_id"] == str(doc.id)

    failed = re_._eval_required_document(_ctx(_application(), []), rule)
    assert failed.outcome == "fail"
    assert failed.finding["finding_code"] == "DOC_MISSING"


def test_required_document_ignores_deleted_or_unreadable_files():
    rule = _rule({"evaluator": "required_document", "document_category": "fire_noc"})
    unreadable = _document("fire_noc", status="unreadable")
    outcome = re_._eval_required_document(_ctx(_application(), [unreadable]), rule)
    assert outcome.outcome == "fail"


def test_document_expiry_detects_expired_document():
    rule = _rule({"evaluator": "document_expiry", "document_category": "fire_noc", "expiry_field": "valid_until"})
    doc = _document("fire_noc")
    field = _field(doc, "valid_until", "2026-09-19")
    outcome = re_._eval_document_expiry(
        _ctx(_application(), [doc], {str(doc.id): [field]}), rule
    )
    assert outcome.outcome == "fail"
    assert outcome.finding["finding_code"] == "DOC_EXPIRED"
    assert outcome.finding["observed_value"] == "2026-09-19"


def test_document_expiry_passes_when_valid_and_respects_grace_days():
    rule = _rule({"evaluator": "document_expiry", "document_category": "fire_noc", "grace_days": 60})
    doc = _document("fire_noc")
    field = _field(doc, "valid_until", "2026-09-19")  # 40 days before AS_OF
    outcome = re_._eval_document_expiry(_ctx(_application(), [doc], {str(doc.id): [field]}), rule)
    assert outcome.outcome == "pass"


def test_document_expiry_without_documents_is_not_a_pass():
    rule = _rule({"evaluator": "document_expiry", "document_category": "fire_noc"})
    assert re_._eval_document_expiry(_ctx(_application()), rule).outcome == "unable_to_evaluate"


def test_identifier_conflict_across_sources():
    rule = _rule({"evaluator": "identifier_match", "identifier_field": "aicte_pid"})
    app = _application(declared_data={"aicte_pid": "DEMO-PID-1001"})
    doc = _document("aicte_letter")
    field = _field(doc, "aicte_pid", "DEMO-PID-9999", "identifier")
    outcome = re_._eval_identifier_match(
        _ctx(app, [doc], {str(doc.id): [field]}, active_fields=[field]), rule
    )
    assert outcome.outcome == "fail"
    assert outcome.finding["finding_code"] == "IDENTIFIER_CONFLICT"


def test_identifier_match_passes_when_sources_agree():
    rule = _rule({"evaluator": "identifier_match", "identifier_field": "aicte_pid"})
    app = _application(declared_data={"aicte_pid": "demo-pid-1001"})
    doc = _document("aicte_letter")
    field = _field(doc, "aicte_pid", "DEMO-PID-1001", "identifier")
    outcome = re_._eval_identifier_match(
        _ctx(app, [doc], {str(doc.id): [field]}, active_fields=[field]), rule
    )
    assert outcome.outcome == "pass"


def test_application_field_min_treats_missing_value_as_unevaluable():
    rule = _rule({"evaluator": "application_field_min", "field_name": "proposed_intake", "min_value": 60})
    assert re_._eval_application_field_min(_ctx(_application()), rule).outcome == "unable_to_evaluate"

    app = _application(declared_data={"proposed_intake": "180"})
    assert re_._eval_application_field_min(_ctx(app), rule).outcome == "pass"

    low = _application(declared_data={"proposed_intake": "30"})
    assert re_._eval_application_field_min(_ctx(low), rule).outcome == "fail"

    words = _application(declared_data={"proposed_intake": "unknown"})
    assert re_._eval_application_field_min(_ctx(words), rule).outcome == "needs_review"


def test_unknown_evaluator_never_passes_silently():
    rule = _rule({"evaluator": "does_not_exist"})
    evaluation = re_.evaluate_rule(_ctx(_application()), rule, uuid.uuid4())
    assert evaluation.outcome == "unable_to_evaluate"
    assert "Unknown evaluator" in evaluation.explanation


def test_rule_out_of_effect_date_is_not_applicable():
    rule = _rule({"evaluator": "required_document", "document_category": "fire_noc"},
                 effective_from=date(2030, 1, 1))
    evaluation = re_.evaluate_rule(_ctx(_application()), rule, uuid.uuid4())
    assert evaluation.outcome == "not_applicable"
