import asyncio

from agents.citation_verifier import (
    CitationJudgmentResponse,
    verify_citation,
)
from schemas import (
    CaseLookupResult,
    CitationFinding,
    ExtractedCitation,
    TextSpan,
)


BRIEF_ID = "brief-rivera"
CITE_PRIVETTE = "Privette v. Superior Court, 5 Cal.4th 689 (1993)"
PRIVETTE_PROPOSITION = (
    "a hirer of an independent contractor is not liable for on-the-job "
    "injuries to the contractor's employees"
)
# The verifier's prompt doesn't echo the cite string back - it embeds the
# proposition + holding. Key the mock on a stable substring of the prompt's
# scaffolding so any test citation routes here.
PROMPT_KEY = "legal clerk verifying one citation"


def _citation(quoted: str | None = None) -> ExtractedCitation:
    return ExtractedCitation(
        cite=CITE_PRIVETTE,
        proposition=PRIVETTE_PROPOSITION,
        quoted_language=quoted,
        claim_span=TextSpan(
            document_id=BRIEF_ID,
            start=0,
            end=len(CITE_PRIVETTE),
            excerpt=CITE_PRIVETTE,
        ),
    )


def _found_lookup(holding: str) -> CaseLookupResult:
    return CaseLookupResult(
        found=True,
        canonical_cite=CITE_PRIVETTE,
        holding_text=holding,
        quoted_text_match=None,
        source_url=None,
        lookup_status="found",
        notes=None,
    )


def _not_found_lookup() -> CaseLookupResult:
    return CaseLookupResult(
        found=False,
        canonical_cite=None,
        holding_text=None,
        quoted_text_match=None,
        source_url=None,
        lookup_status="not_found",
        notes=None,
    )


def _failed_lookup() -> CaseLookupResult:
    return CaseLookupResult(
        found=False,
        canonical_cite=None,
        holding_text=None,
        quoted_text_match=None,
        source_url=None,
        lookup_status="lookup_failed",
        notes=None,
    )


class _FakeLookup:
    def __init__(self, result: CaseLookupResult) -> None:
        self._result = result
        self.calls: list[ExtractedCitation] = []

    async def fetch(self, citation: ExtractedCitation) -> CaseLookupResult:
        self.calls.append(citation)
        return self._result


def test_supported_when_holding_directly_supports_proposition(mock_llm):
    holding = (
        "A person who hires an independent contractor is generally not liable "
        "for injuries sustained by the contractor's employees on the job."
    )
    lookup = _FakeLookup(_found_lookup(holding))
    payload = CitationJudgmentResponse(
        verdict="supported",
        confidence=0.92,
        reasoning="The holding squarely matches the proposition.",
        evidence_quote=holding,
    )
    mock_llm({PROMPT_KEY: payload})

    finding = asyncio.run(verify_citation(_citation(), lookup))

    assert isinstance(finding, CitationFinding)
    assert finding.verdict == "supported"
    assert finding.confidence == 0.92
    assert finding.lookup.lookup_status == "found"
    assert len(lookup.calls) == 1


def test_contradicted_when_holding_contradicts_proposition(mock_llm):
    holding = (
        "A hirer of an independent contractor IS liable for on-the-job "
        "injuries to the contractor's employees."
    )
    lookup = _FakeLookup(_found_lookup(holding))
    payload = CitationJudgmentResponse(
        verdict="contradicted",
        confidence=0.88,
        reasoning="Holding flips the proposition.",
        evidence_quote=holding,
    )
    mock_llm({PROMPT_KEY: payload})

    finding = asyncio.run(verify_citation(_citation(), lookup))

    assert finding.verdict == "contradicted"
    assert finding.confidence == 0.88


def test_unsupported_when_holding_is_irrelevant(mock_llm):
    holding = "An entirely unrelated holding about admiralty jurisdiction."
    lookup = _FakeLookup(_found_lookup(holding))
    payload = CitationJudgmentResponse(
        verdict="unsupported",
        confidence=0.5,
        reasoning="Holding does not address the brief's proposition.",
        evidence_quote=None,
    )
    mock_llm({PROMPT_KEY: payload})

    finding = asyncio.run(verify_citation(_citation(), lookup))

    assert finding.verdict == "unsupported"


def test_not_found_short_circuits_without_calling_llm(monkeypatch):
    # mock_llm asserts called >= 1 at teardown, but this test must prove the
    # judgment LLM never fires. Patch the seam directly with a counter.
    call_count = {"n": 0}

    async def _boom(*args, **kwargs):
        call_count["n"] += 1
        raise AssertionError("LLM must not be called when lookup is not_found")

    monkeypatch.setattr("llm.call_llm_async", _boom, raising=False)

    lookup = _FakeLookup(_not_found_lookup())
    finding = asyncio.run(verify_citation(_citation(), lookup))

    assert call_count["n"] == 0
    assert finding.verdict == "could_not_verify"
    assert finding.confidence == 0.9
    assert finding.lookup.lookup_status == "not_found"


def test_lookup_failed_short_circuits_with_low_confidence(monkeypatch):
    call_count = {"n": 0}

    async def _boom(*args, **kwargs):
        call_count["n"] += 1
        raise AssertionError("LLM must not be called when lookup failed")

    monkeypatch.setattr("llm.call_llm_async", _boom, raising=False)

    lookup = _FakeLookup(_failed_lookup())
    finding = asyncio.run(verify_citation(_citation(), lookup))

    assert call_count["n"] == 0
    assert finding.verdict == "could_not_verify"
    assert finding.confidence == 0.5
    assert finding.lookup.lookup_status == "lookup_failed"


def test_quoted_language_present_includes_quote_check_in_prompt(mock_llm):
    holding = "A holding that supports the proposition."
    quote = "the contractor's employees are barred from suing the hirer"
    lookup = _FakeLookup(_found_lookup(holding))
    payload = CitationJudgmentResponse(
        verdict="supported",
        confidence=0.85,
        reasoning="ok",
        evidence_quote=holding,
    )
    calls = mock_llm({PROMPT_KEY: payload})

    asyncio.run(verify_citation(_citation(quoted=quote), lookup))

    assert len(calls) == 1
    prompt = calls[0]["prompt"]
    assert quote in prompt
    # the prompt has to actually frame this as a quote-accuracy check
    assert "quote" in prompt.lower()


def test_no_quoted_language_omits_quote_check_from_prompt(mock_llm):
    holding = "A holding that supports the proposition."
    lookup = _FakeLookup(_found_lookup(holding))
    payload = CitationJudgmentResponse(
        verdict="supported",
        confidence=0.85,
        reasoning="ok",
        evidence_quote=holding,
    )
    calls = mock_llm({PROMPT_KEY: payload})

    asyncio.run(verify_citation(_citation(quoted=None), lookup))

    assert len(calls) == 1
    prompt = calls[0]["prompt"]
    # No verbatim quote was attributed -> no quote-accuracy sub-question.
    assert "verbatim quote" not in prompt.lower()
    assert "quote-accuracy" not in prompt.lower()


def test_lookup_attached_to_finding_for_provenance(mock_llm):
    holding = "A holding."
    lookup_result = _found_lookup(holding)
    lookup = _FakeLookup(lookup_result)
    payload = CitationJudgmentResponse(
        verdict="supported",
        confidence=0.85,
        reasoning="ok",
        evidence_quote=holding,
    )
    mock_llm({PROMPT_KEY: payload})

    finding = asyncio.run(verify_citation(_citation(), lookup))

    assert finding.lookup == lookup_result
