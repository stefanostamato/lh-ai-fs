import asyncio

import pytest

from agents.tools.case_lookup import (
    CaseLawLookup,
    ParametricLLMLookup,
    ParametricLookupResponse,
)
from llm import LlmError
from schemas import CaseLookupResult, ExtractedCitation, TextSpan


BRIEF_ID = "brief-rivera"
CITE_PRIVETTE = "Privette v. Superior Court, 5 Cal.4th 689 (1993)"


def _citation(quoted: str | None = None) -> ExtractedCitation:
    return ExtractedCitation(
        cite=CITE_PRIVETTE,
        proposition="hirer of an independent contractor is not liable for on-the-job injuries to the contractor's employees",
        quoted_language=quoted,
        claim_span=TextSpan(
            document_id=BRIEF_ID,
            start=0,
            end=len(CITE_PRIVETTE),
            excerpt=CITE_PRIVETTE,
        ),
    )


def test_protocol_has_async_fetch_returning_case_lookup_result():
    # Structural: CaseLawLookup is a Protocol with a single async `fetch`.
    assert hasattr(CaseLawLookup, "fetch")
    # The Protocol exists and ParametricLLMLookup is a structural match.
    assert isinstance(ParametricLLMLookup(), CaseLawLookup)


def test_returns_found_when_model_is_confident(mock_llm):
    payload = ParametricLookupResponse(
        confidence=0.95,
        canonical_cite=CITE_PRIVETTE,
        holding_text=(
            "A person who hires an independent contractor is generally not "
            "liable for injuries sustained by the contractor's employees on "
            "the job."
        ),
        quoted_text_match=None,
        notes=None,
    )
    mock_llm({CITE_PRIVETTE: payload})

    result = asyncio.run(ParametricLLMLookup().fetch(_citation()))

    assert isinstance(result, CaseLookupResult)
    assert result.lookup_status == "found"
    assert result.found is True
    assert result.canonical_cite == CITE_PRIVETTE
    assert result.holding_text is not None


def test_returns_not_found_when_model_is_low_confidence(mock_llm):
    payload = ParametricLookupResponse(
        confidence=0.3,
        canonical_cite=None,
        holding_text=(
            "I think this might be a workers' comp case but I'm not sure."
        ),
        quoted_text_match=None,
        notes="Not confident this is a real citation.",
    )
    mock_llm({CITE_PRIVETTE: payload})

    result = asyncio.run(ParametricLLMLookup().fetch(_citation()))

    assert result.lookup_status == "not_found"
    assert result.found is False
    assert result.holding_text is None


def test_returns_lookup_failed_when_llm_raises(monkeypatch):
    # The mock_llm fixture records-then-returns; for LlmError we bypass it and
    # patch the seam directly. The contract under test: LlmError out of llm.py
    # becomes lookup_status="lookup_failed", not a crash and not "not_found".
    async def _boom(*args, **kwargs):
        raise LlmError("model refused")

    monkeypatch.setattr("llm.call_llm_async", _boom, raising=False)

    result = asyncio.run(ParametricLLMLookup().fetch(_citation()))

    assert result.lookup_status == "lookup_failed"
    assert result.found is False
    assert result.holding_text is None


def test_holding_text_is_none_whenever_status_is_not_found(mock_llm):
    # Even when the model still drops a stray holding_text on a low-confidence
    # response, we strip it. The lookup must not fabricate.
    payload = ParametricLookupResponse(
        confidence=0.4,
        canonical_cite="Some Other Case, 1 F.3d 1 (1990)",
        holding_text="A holding the model is guessing at.",
        quoted_text_match=None,
        notes=None,
    )
    mock_llm({CITE_PRIVETTE: payload})

    result = asyncio.run(ParametricLLMLookup().fetch(_citation()))

    assert result.lookup_status != "found"
    assert result.holding_text is None


def test_quoted_text_match_is_none_when_citation_has_no_quote(mock_llm):
    payload = ParametricLookupResponse(
        confidence=0.95,
        canonical_cite=CITE_PRIVETTE,
        holding_text="A holding text.",
        # Even if the model fills this in, the citation has no quoted_language
        # so we must surface None - there's nothing to match against.
        quoted_text_match=True,
        notes=None,
    )
    mock_llm({CITE_PRIVETTE: payload})

    result = asyncio.run(ParametricLLMLookup().fetch(_citation(quoted=None)))

    assert result.quoted_text_match is None


def test_quoted_text_match_passes_through_when_citation_has_quote(mock_llm):
    payload = ParametricLookupResponse(
        confidence=0.95,
        canonical_cite=CITE_PRIVETTE,
        holding_text="A holding text matching the brief's quote.",
        quoted_text_match=True,
        notes=None,
    )
    mock_llm({CITE_PRIVETTE: payload})

    result = asyncio.run(
        ParametricLLMLookup().fetch(_citation(quoted="some verbatim quote"))
    )

    assert result.quoted_text_match is True
