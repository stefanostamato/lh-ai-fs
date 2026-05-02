import pytest

from agents.record_parser import parse_record
from schemas import DocumentInput, DocumentRole, ParagraphSpan, ParsedRecord


TWO_PARAGRAPH_TEXT = (
    "Officer arrived on scene at 14:32 and observed the vehicle.\n\n"
    "Witness reported the light was red at the time of the collision."
)


def _record_input(
    text: str,
    document_id: str = "test-record-456",
    display_name: str = "Test Record",
) -> DocumentInput:
    return DocumentInput(
        document_id=document_id,
        display_name=display_name,
        role=DocumentRole.RECORD,
        text=text,
    )


def test_returns_parsed_record_type():
    result = parse_record(_record_input(TWO_PARAGRAPH_TEXT))
    assert isinstance(result, ParsedRecord)


def test_paragraph_count_matches_double_newline_splits():
    result = parse_record(_record_input(TWO_PARAGRAPH_TEXT))
    assert len(result.paragraphs) == 2


def test_paragraphs_are_paragraph_span_instances():
    result = parse_record(_record_input(TWO_PARAGRAPH_TEXT))
    for p in result.paragraphs:
        assert isinstance(p, ParagraphSpan)


def test_paragraph_indices_are_zero_based_and_sequential():
    text = "P1.\n\nP2.\n\nP3.\n\nP4."
    result = parse_record(_record_input(text))
    assert [p.paragraph_index for p in result.paragraphs] == [0, 1, 2, 3]


def test_offsets_round_trip_to_excerpt():
    result = parse_record(_record_input(TWO_PARAGRAPH_TEXT))
    raw = result.raw_text
    for p in result.paragraphs:
        assert raw[p.span.start : p.span.end].strip() == p.span.excerpt


def test_raw_text_preserved_verbatim():
    result = parse_record(_record_input(TWO_PARAGRAPH_TEXT))
    assert result.raw_text == TWO_PARAGRAPH_TEXT


def test_empty_paragraphs_dropped():
    text = "First.\n\n\n\n   \n\n\nSecond.\n\n\n\n   \n"
    result = parse_record(_record_input(text))
    assert len(result.paragraphs) == 2
    excerpts = [p.span.excerpt for p in result.paragraphs]
    assert excerpts == ["First.", "Second."]


def test_display_name_carried_through():
    result = parse_record(
        _record_input(TWO_PARAGRAPH_TEXT, display_name="Some Friendly Display Name")
    )
    assert result.display_name == "Some Friendly Display Name"


def test_span_document_id_matches_input():
    result = parse_record(
        _record_input(TWO_PARAGRAPH_TEXT, document_id="arbitrary-record-id-789")
    )
    assert result.document_id == "arbitrary-record-id-789"
    for p in result.paragraphs:
        assert p.document_id == "arbitrary-record-id-789"
        assert p.span.document_id == "arbitrary-record-id-789"


def test_role_mismatch_raises_value_error():
    brief_input = DocumentInput(
        document_id="some-brief",
        display_name="Some Brief",
        role=DocumentRole.BRIEF,
        text=TWO_PARAGRAPH_TEXT,
    )
    with pytest.raises(ValueError):
        parse_record(brief_input)


def test_works_with_arbitrary_document_id_and_display_name():
    result = parse_record(
        _record_input(
            TWO_PARAGRAPH_TEXT,
            document_id="totally-unique-id",
            display_name="Totally Unique Display",
        )
    )
    assert result.document_id == "totally-unique-id"
    assert result.display_name == "Totally Unique Display"


def test_excerpt_is_stripped():
    text = "   Padded first.   \n\n   Padded second.   "
    result = parse_record(_record_input(text))
    assert result.paragraphs[0].span.excerpt == "Padded first."
    assert result.paragraphs[1].span.excerpt == "Padded second."


def test_offsets_round_trip_with_messy_whitespace():
    text = "  Para A.  \n\n\n   Para B with  inner  spaces.   \n\n\nPara C."
    result = parse_record(_record_input(text))
    raw = result.raw_text
    for p in result.paragraphs:
        assert raw[p.span.start : p.span.end].strip() == p.span.excerpt
