import asyncio
import logging

from agents.claim_extractor import (
    ClaimDraft,
    ClaimExtractionResponse,
    extract_claims,
)
from schemas import (
    DocumentInput,
    DocumentRole,
    ExtractedClaim,
    ParagraphSpan,
    ParsedBrief,
    TextSpan,
)
from usage import UsageCollector


BRIEF_ID = "brief-rivera"

# Five sentences across two paragraphs. Three factual (checkable against the
# record), two legal arguments (not checkable).
BRIEF_TEXT = (
    "The incident occurred on March 14, 2021. "
    "Rivera was not wearing personal protective equipment. "
    "The fall was 14 feet from the scaffold.\n\n"
    "Defendant owed no duty under California law. "
    "The Privette doctrine bars recovery as a matter of law."
)


FACT_DATE = "The incident occurred on March 14, 2021."
FACT_PPE = "Rivera was not wearing personal protective equipment."
FACT_FALL = "The fall was 14 feet from the scaffold."

LEGAL_DUTY = "Defendant owed no duty under California law."
LEGAL_PRIVETTE = "The Privette doctrine bars recovery as a matter of law."


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
        cursor = end + 2  # advance past "\n\n"
    return ParsedBrief(document_id=BRIEF_ID, raw_text=doc.text, paragraphs=paragraphs)


def _draft(brief: ParsedBrief, claim_text: str) -> ClaimDraft:
    start = brief.raw_text.find(claim_text)
    end = start + len(claim_text)
    return ClaimDraft(
        claim_text=claim_text,
        span_start=start,
        span_end=end,
    )


def test_returns_only_factual_claims(mock_llm):
    brief = _brief()
    payload = ClaimExtractionResponse(
        claims=[
            _draft(brief, FACT_DATE),
            _draft(brief, FACT_PPE),
            _draft(brief, FACT_FALL),
        ]
    )
    mock_llm({"claim": payload})

    result = asyncio.run(extract_claims(brief))

    assert len(result) == 3
    assert all(isinstance(c, ExtractedClaim) for c in result)
    returned = {c.claim_text for c in result}
    assert returned == {FACT_DATE, FACT_PPE, FACT_FALL}
    assert LEGAL_DUTY not in returned
    assert LEGAL_PRIVETTE not in returned


def test_each_returned_excerpt_appears_in_brief_raw_text(mock_llm):
    brief = _brief()
    payload = ClaimExtractionResponse(
        claims=[
            _draft(brief, FACT_DATE),
            _draft(brief, FACT_PPE),
            _draft(brief, FACT_FALL),
        ]
    )
    mock_llm({"claim": payload})

    result = asyncio.run(extract_claims(brief))

    for c in result:
        assert c.claim_span.excerpt in brief.raw_text
        assert brief.raw_text[c.claim_span.start : c.claim_span.end] == c.claim_span.excerpt
        assert c.claim_span.document_id == BRIEF_ID


def test_string_find_fallback_used_when_model_returns_wrong_offsets(mock_llm):
    brief = _brief()
    correct_start = brief.raw_text.find(FACT_PPE)
    bad_start = correct_start + 25  # deliberately off
    bad_draft = ClaimDraft(
        claim_text=FACT_PPE,
        span_start=bad_start,
        span_end=bad_start + len(FACT_PPE),
    )
    payload = ClaimExtractionResponse(claims=[bad_draft])
    mock_llm({"claim": payload})

    result = asyncio.run(extract_claims(brief))

    assert len(result) == 1
    assert result[0].claim_span.start == correct_start
    assert result[0].claim_span.end == correct_start + len(FACT_PPE)
    assert result[0].claim_span.excerpt == FACT_PPE


def test_claim_dropped_when_neither_span_nor_find_locates_text(mock_llm, caplog):
    brief = _brief()
    phantom_text = "Plaintiff was struck by lightning while bowling."
    phantom = ClaimDraft(
        claim_text=phantom_text,
        span_start=0,
        span_end=len(phantom_text),
    )
    good = _draft(brief, FACT_FALL)
    payload = ClaimExtractionResponse(claims=[phantom, good])
    mock_llm({"claim": payload})

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(extract_claims(brief))

    assert len(result) == 1
    assert result[0].claim_text == FACT_FALL
    assert any(phantom_text in rec.message for rec in caplog.records)


def test_empty_claims_list_returns_empty(mock_llm):
    brief = _brief()
    mock_llm({"claim": ClaimExtractionResponse(claims=[])})

    result = asyncio.run(extract_claims(brief))

    assert result == []


def test_usage_collector_threaded_through_to_llm(mock_llm):
    brief = _brief()
    payload = ClaimExtractionResponse(claims=[_draft(brief, FACT_DATE)])
    calls = mock_llm({"claim": payload})

    usage = UsageCollector()
    asyncio.run(extract_claims(brief, usage=usage))

    assert len(calls) == 1
