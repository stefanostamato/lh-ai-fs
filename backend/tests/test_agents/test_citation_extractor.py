import asyncio
import logging

from agents.citation_extractor import (
    CitationDraft,
    CitationExtractionResponse,
    extract_citations,
)
from schemas import (
    DocumentInput,
    DocumentRole,
    ExtractedCitation,
    ParagraphSpan,
    ParsedBrief,
    TextSpan,
)
from usage import UsageCollector


BRIEF_ID = "brief-rivera"

BRIEF_TEXT = (
    "The moving party relies on Privette v. Superior Court, 5 Cal.4th 689 (1993) "
    "for the proposition that a hirer of an independent contractor is not liable "
    "for on-the-job injuries to the contractor's employees.\n\n"
    "Further, Smith v. Jones, 123 F.3d 456 (9th Cir. 1999) holds that summary "
    "judgment is appropriate where no genuine issue of material fact exists.\n\n"
    "Finally, the court in Doe v. Roe, 42 Cal.App.4th 100 (1996) concluded that "
    "a duty of care does not extend beyond the scope of the contract."
)


def _brief() -> ParsedBrief:
    doc = DocumentInput(
        document_id=BRIEF_ID,
        display_name="Rivera Motion",
        role=DocumentRole.BRIEF,
        text=BRIEF_TEXT,
    )
    paragraphs: list[ParagraphSpan] = []
    cursor = 0
    for i, chunk in enumerate(BRIEF_TEXT.split("\n\n")):
        start = cursor
        end = cursor + len(chunk)
        paragraphs.append(
            ParagraphSpan(
                document_id=BRIEF_ID,
                paragraph_index=i,
                span=TextSpan(
                    document_id=BRIEF_ID,
                    start=start,
                    end=end,
                    excerpt=chunk.strip(),
                ),
            )
        )
        cursor = end + 2  # advance past the "\n\n" separator
    return ParsedBrief(document_id=BRIEF_ID, raw_text=doc.text, paragraphs=paragraphs)


CITE_PRIVETTE = "Privette v. Superior Court, 5 Cal.4th 689 (1993)"
CITE_SMITH = "Smith v. Jones, 123 F.3d 456 (9th Cir. 1999)"
CITE_DOE = "Doe v. Roe, 42 Cal.App.4th 100 (1996)"


def _draft(brief: ParsedBrief, cite: str, proposition: str, quoted: str | None = None) -> CitationDraft:
    start = brief.raw_text.find(cite)
    end = start + len(cite)
    return CitationDraft(
        cite=cite,
        proposition=proposition,
        quoted_language=quoted,
        span_start=start,
        span_end=end,
    )


def test_returns_three_extracted_citations_when_model_returns_three(mock_llm):
    brief = _brief()
    payload = CitationExtractionResponse(
        citations=[
            _draft(brief, CITE_PRIVETTE, "hirer not liable for contractor employee injuries"),
            _draft(brief, CITE_SMITH, "summary judgment standard"),
            _draft(brief, CITE_DOE, "duty bounded by contract scope"),
        ]
    )
    mock_llm({"citation": payload})

    result = asyncio.run(extract_citations(brief))

    assert len(result) == 3
    assert all(isinstance(c, ExtractedCitation) for c in result)


def test_each_returned_excerpt_appears_verbatim_in_brief_raw_text(mock_llm):
    brief = _brief()
    payload = CitationExtractionResponse(
        citations=[
            _draft(brief, CITE_PRIVETTE, "p1"),
            _draft(brief, CITE_SMITH, "p2"),
            _draft(brief, CITE_DOE, "p3"),
        ]
    )
    mock_llm({"citation": payload})

    result = asyncio.run(extract_citations(brief))

    for c in result:
        assert c.claim_span.excerpt in brief.raw_text
        # span offsets resolve to the same excerpt
        assert brief.raw_text[c.claim_span.start : c.claim_span.end] == c.claim_span.excerpt
        assert c.claim_span.document_id == BRIEF_ID


def test_string_find_fallback_used_when_model_returns_wrong_offsets(mock_llm):
    brief = _brief()
    correct_start = brief.raw_text.find(CITE_SMITH)
    bad_start = correct_start + 50  # deliberately off
    bad_draft = CitationDraft(
        cite=CITE_SMITH,
        proposition="summary judgment standard",
        quoted_language=None,
        span_start=bad_start,
        span_end=bad_start + len(CITE_SMITH),
    )
    payload = CitationExtractionResponse(citations=[bad_draft])
    mock_llm({"citation": payload})

    result = asyncio.run(extract_citations(brief))

    assert len(result) == 1
    assert result[0].claim_span.start == correct_start
    assert result[0].claim_span.end == correct_start + len(CITE_SMITH)
    assert result[0].claim_span.excerpt == CITE_SMITH


def test_citation_dropped_when_neither_span_nor_find_locates_cite(mock_llm, caplog):
    brief = _brief()
    phantom_cite = "Ghost v. Phantom, 999 U.S. 999 (1900)"
    phantom = CitationDraft(
        cite=phantom_cite,
        proposition="not in the brief at all",
        quoted_language=None,
        span_start=0,
        span_end=len(phantom_cite),
    )
    good = _draft(brief, CITE_DOE, "duty bounded by contract scope")
    payload = CitationExtractionResponse(citations=[phantom, good])
    mock_llm({"citation": payload})

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(extract_citations(brief))

    assert len(result) == 1
    assert result[0].cite == CITE_DOE
    assert any("Ghost v. Phantom" in rec.message or phantom_cite in rec.message for rec in caplog.records)


def test_usage_collector_threaded_through_to_llm(mock_llm):
    brief = _brief()
    payload = CitationExtractionResponse(
        citations=[_draft(brief, CITE_PRIVETTE, "p1")]
    )
    calls = mock_llm({"citation": payload})

    usage = UsageCollector()
    asyncio.run(extract_citations(brief, usage=usage))

    assert len(calls) == 1
