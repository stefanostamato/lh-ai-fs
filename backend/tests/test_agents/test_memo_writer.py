import asyncio

import pytest

from agents.memo_writer import (
    MemoResponse,
    MemoVerdictPromotionError,
    write_memo,
)
from schemas import (
    CaseLookupResult,
    CitationFinding,
    ConsistencyFinding,
    ExtractedCitation,
    ExtractedClaim,
    FindingRef,
    JudicialMemo,
    PartialFailure,
    TextSpan,
)
from usage import UsageCollector


BRIEF_ID = "brief-doc"
RECORD_ID = "record-doc"


def _claim_span(text: str = "the incident occurred on March 14") -> TextSpan:
    return TextSpan(document_id=BRIEF_ID, start=0, end=len(text), excerpt=text)


def _evidence_span(text: str = "report says March 15") -> TextSpan:
    return TextSpan(document_id=RECORD_ID, start=0, end=len(text), excerpt=text)


def _citation_finding(
    *,
    verdict: str = "could_not_verify",
    cite: str = "Smith v. Jones, 1 U.S. 1 (1900)",
    proposition: str = "summary judgment is appropriate",
) -> CitationFinding:
    return CitationFinding(
        citation=ExtractedCitation(
            cite=cite,
            proposition=proposition,
            quoted_language=None,
            claim_span=_claim_span(cite),
        ),
        verdict=verdict,
        confidence=0.6,
        reasoning="lookup returned no source",
        evidence_quote=None,
        evidence_span=None,
        lookup=CaseLookupResult(
            found=False,
            canonical_cite=None,
            holding_text=None,
            quoted_text_match=None,
            source_url=None,
            lookup_status="not_found",
            notes=None,
        ),
    )


def _consistency_finding(
    *,
    verdict: str = "could_not_verify",
    claim_text: str = "the incident occurred on March 14",
) -> ConsistencyFinding:
    return ConsistencyFinding(
        claim=ExtractedClaim(
            claim_text=claim_text,
            claim_span=_claim_span(claim_text),
        ),
        verdict=verdict,
        confidence=0.55,
        reasoning="record does not mention this date",
        evidence_quote=None,
        evidence_span=None,
        checked_documents=[RECORD_ID],
    )


def _pair_citation(idx: int, finding: CitationFinding) -> tuple[FindingRef, CitationFinding]:
    return FindingRef(finding_type="citation", finding_index=idx), finding


def _pair_consistency(idx: int, finding: ConsistencyFinding) -> tuple[FindingRef, ConsistencyFinding]:
    return FindingRef(finding_type="consistency", finding_index=idx), finding


def _resp(text: str) -> MemoResponse:
    return MemoResponse(text=text)


def test_returns_well_formed_judicial_memo(mock_llm):
    findings = [
        _pair_citation(0, _citation_finding(verdict="could_not_verify")),
        _pair_consistency(0, _consistency_finding(verdict="could_not_verify")),
    ]
    memo_text = (
        "The clerk notes that one cited authority could not be located in the "
        "available case-law lookup, and one factual claim about the incident "
        "date could not be confirmed against the record."
    )
    mock_llm({"clerk": _resp(memo_text)})

    result = asyncio.run(write_memo(findings, []))

    assert isinstance(result, JudicialMemo)
    assert result.text == memo_text
    assert len(result.top_findings) == 2
    assert result.top_findings[0].finding_type == "citation"
    assert result.top_findings[0].finding_index == 0
    assert result.top_findings[1].finding_type == "consistency"
    assert result.top_findings[1].finding_index == 0
    assert result.partial_failure_note is None


def test_partial_failure_note_is_none_when_no_failures(mock_llm):
    findings = [_pair_citation(0, _citation_finding(verdict="could_not_verify"))]
    mock_llm({"clerk": _resp("The clerk notes one cite could not be located.")})

    result = asyncio.run(write_memo(findings, []))

    assert result.partial_failure_note is None


def test_partial_failure_note_references_failed_agents_by_name(mock_llm):
    findings = [_pair_citation(0, _citation_finding(verdict="could_not_verify"))]
    failures = [
        PartialFailure(agent="citation_verifier", error="lookup timeout"),
        PartialFailure(agent="crossdoc_checker", error="model returned malformed JSON"),
    ]
    mock_llm({"clerk": _resp("The clerk notes one cite could not be located.")})

    result = asyncio.run(write_memo(findings, failures))

    assert result.partial_failure_note is not None
    assert "citation_verifier" in result.partial_failure_note
    assert "crossdoc_checker" in result.partial_failure_note


def test_verdict_promotion_raises_when_memo_calls_could_not_verify_finding_fabricated(mock_llm):
    findings = [
        _pair_citation(0, _citation_finding(verdict="could_not_verify")),
        _pair_consistency(0, _consistency_finding(verdict="could_not_verify")),
    ]
    bad_memo = (
        "The clerk reports that the moving party fabricated the cited authority "
        "and the asserted incident date is contradicted by the record."
    )
    mock_llm({"clerk": _resp(bad_memo)})

    with pytest.raises(MemoVerdictPromotionError):
        asyncio.run(write_memo(findings, []))


def test_verdict_promotion_raises_for_each_forbidden_word(mock_llm):
    findings = [_pair_citation(0, _citation_finding(verdict="could_not_verify"))]

    for forbidden in ["fabricated", "false", "misleading", "deceptive", "lying", "contradicted"]:
        bad_memo = f"The clerk notes the brief is {forbidden} on this point."
        mock_llm({"clerk": _resp(bad_memo)})

        with pytest.raises(MemoVerdictPromotionError):
            asyncio.run(write_memo(findings, []))


def test_contradicted_word_allowed_when_input_finding_is_contradicted(mock_llm):
    findings = [
        _pair_consistency(
            0,
            _consistency_finding(verdict="contradicted"),
        ),
    ]
    memo_text = (
        "The clerk notes the asserted incident date is contradicted by the "
        "police report's contemporaneous entry."
    )
    mock_llm({"clerk": _resp(memo_text)})

    result = asyncio.run(write_memo(findings, []))

    assert result.text == memo_text


def test_outcome_recommendation_should_be_granted_raises(mock_llm):
    findings = [_pair_citation(0, _citation_finding(verdict="could_not_verify"))]
    mock_llm({"clerk": _resp("The clerk notes one issue and the motion should be granted.")})

    with pytest.raises(MemoVerdictPromotionError):
        asyncio.run(write_memo(findings, []))


def test_outcome_recommendation_should_be_denied_raises(mock_llm):
    findings = [_pair_citation(0, _citation_finding(verdict="could_not_verify"))]
    mock_llm({"clerk": _resp("The clerk notes one issue and the motion should be denied.")})

    with pytest.raises(MemoVerdictPromotionError):
        asyncio.run(write_memo(findings, []))


def test_outcome_recommendation_motion_fails_raises(mock_llm):
    findings = [_pair_citation(0, _citation_finding(verdict="could_not_verify"))]
    mock_llm({"clerk": _resp("Given the gaps the motion fails on its face.")})

    with pytest.raises(MemoVerdictPromotionError):
        asyncio.run(write_memo(findings, []))


def test_outcome_recommendation_motion_succeeds_raises(mock_llm):
    findings = [_pair_citation(0, _citation_finding(verdict="could_not_verify"))]
    mock_llm({"clerk": _resp("On this record the motion succeeds without further inquiry.")})

    with pytest.raises(MemoVerdictPromotionError):
        asyncio.run(write_memo(findings, []))


def test_top_findings_refs_match_inputs_in_order(mock_llm):
    pairs = [
        _pair_consistency(2, _consistency_finding(verdict="could_not_verify")),
        _pair_citation(7, _citation_finding(verdict="could_not_verify")),
        _pair_citation(0, _citation_finding(verdict="could_not_verify")),
    ]
    mock_llm({"clerk": _resp("The clerk notes three items that could not be confirmed.")})

    result = asyncio.run(write_memo(pairs, []))

    assert [(r.finding_type, r.finding_index) for r in result.top_findings] == [
        ("consistency", 2),
        ("citation", 7),
        ("citation", 0),
    ]


def test_usage_collector_threaded_through_to_llm(mock_llm):
    findings = [_pair_citation(0, _citation_finding(verdict="could_not_verify"))]
    calls = mock_llm({"clerk": _resp("The clerk notes one cite could not be located.")})

    usage = UsageCollector()
    asyncio.run(write_memo(findings, [], usage=usage))

    assert len(calls) == 1
