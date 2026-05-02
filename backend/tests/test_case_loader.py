from case_loader import load_default_case
from schemas import DocumentRole, DocumentSet


def test_load_default_case_returns_document_set():
    doc_set = load_default_case()
    assert isinstance(doc_set, DocumentSet)
    assert len(doc_set.documents) == 4


def test_load_default_case_has_one_brief_three_records():
    doc_set = load_default_case()
    briefs = [d for d in doc_set.documents if d.role == DocumentRole.BRIEF]
    records = [d for d in doc_set.documents if d.role == DocumentRole.RECORD]
    assert len(briefs) == 1
    assert len(records) == 3


def test_load_default_case_brief_is_motion():
    doc_set = load_default_case()
    assert doc_set.brief().document_id == "motion_for_summary_judgment"


def test_load_default_case_documents_have_text_and_display_name():
    doc_set = load_default_case()
    for doc in doc_set.documents:
        assert doc.text.strip(), f"empty text for {doc.document_id}"
        assert doc.display_name.strip(), f"empty display_name for {doc.document_id}"


def test_load_default_case_record_ids_present():
    doc_set = load_default_case()
    record_ids = {d.document_id for d in doc_set.records()}
    assert record_ids == {
        "police_report",
        "medical_records_excerpt",
        "witness_statement",
    }
