import pytest

from agents.brief_parser import parse_brief
from schemas import DocumentInput, DocumentRole, ParagraphSpan, ParsedBrief


THREE_PARAGRAPH_TEXT = (
    "First paragraph talks about the motion.\n\n"
    "Second paragraph cites Smith v. Jones, 123 F.3d 456 (9th Cir. 1999).\n\n"
    "Third paragraph wraps up the argument."
)


def _brief_input(text: str, document_id: str = "test-brief-123") -> DocumentInput:
    return DocumentInput(
        document_id=document_id,
        display_name="Test Brief",
        role=DocumentRole.BRIEF,
        text=text,
    )


def test_returns_parsed_brief_type():
    result = parse_brief(_brief_input(THREE_PARAGRAPH_TEXT))
    assert isinstance(result, ParsedBrief)


def test_paragraph_count_matches_double_newline_splits():
    result = parse_brief(_brief_input(THREE_PARAGRAPH_TEXT))
    assert len(result.paragraphs) == 3


def test_paragraphs_are_paragraph_span_instances():
    result = parse_brief(_brief_input(THREE_PARAGRAPH_TEXT))
    for p in result.paragraphs:
        assert isinstance(p, ParagraphSpan)


def test_paragraph_indices_are_zero_based_and_sequential():
    result = parse_brief(_brief_input(THREE_PARAGRAPH_TEXT))
    assert [p.paragraph_index for p in result.paragraphs] == [0, 1, 2]


def test_offsets_round_trip_to_excerpt():
    result = parse_brief(_brief_input(THREE_PARAGRAPH_TEXT))
    raw = result.raw_text
    for p in result.paragraphs:
        assert raw[p.span.start : p.span.end].strip() == p.span.excerpt


def test_raw_text_preserved_verbatim():
    result = parse_brief(_brief_input(THREE_PARAGRAPH_TEXT))
    assert result.raw_text == THREE_PARAGRAPH_TEXT


def test_empty_paragraphs_dropped():
    text = "First paragraph.\n\n\n\n   \n\n\n\nSecond paragraph.\n\n\n\n"
    result = parse_brief(_brief_input(text))
    assert len(result.paragraphs) == 2
    excerpts = [p.span.excerpt for p in result.paragraphs]
    assert excerpts == ["First paragraph.", "Second paragraph."]


def test_single_paragraph_no_separator():
    text = "Only one block of text, no separator."
    result = parse_brief(_brief_input(text))
    assert len(result.paragraphs) == 1
    assert result.paragraphs[0].span.excerpt == text


def test_excerpt_is_stripped():
    text = "   Indented first paragraph.   \n\n   Second paragraph with padding.   "
    result = parse_brief(_brief_input(text))
    assert result.paragraphs[0].span.excerpt == "Indented first paragraph."
    assert result.paragraphs[1].span.excerpt == "Second paragraph with padding."


def test_span_document_id_matches_input():
    result = parse_brief(_brief_input(THREE_PARAGRAPH_TEXT, document_id="arbitrary-id-xyz"))
    assert result.document_id == "arbitrary-id-xyz"
    for p in result.paragraphs:
        assert p.document_id == "arbitrary-id-xyz"
        assert p.span.document_id == "arbitrary-id-xyz"


def test_role_mismatch_raises_value_error():
    record_input = DocumentInput(
        document_id="some-record",
        display_name="Some Record",
        role=DocumentRole.RECORD,
        text=THREE_PARAGRAPH_TEXT,
    )
    with pytest.raises(ValueError):
        parse_brief(record_input)


def test_works_with_arbitrary_document_id():
    result = parse_brief(_brief_input(THREE_PARAGRAPH_TEXT, document_id="not-a-real-filename"))
    assert result.document_id == "not-a-real-filename"


def test_offsets_round_trip_with_messy_whitespace():
    text = "  First.  \n\n\n   Second has  inner  spaces.   \n\n\nThird."
    result = parse_brief(_brief_input(text))
    raw = result.raw_text
    for p in result.paragraphs:
        assert raw[p.span.start : p.span.end].strip() == p.span.excerpt
