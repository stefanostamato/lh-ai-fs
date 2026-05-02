from pipeline import VERDICT_WEIGHTS, rank_findings
from schemas import (
    CaseLookupResult,
    CitationFinding,
    ConsistencyFinding,
    ExtractedCitation,
    ExtractedClaim,
    FindingRef,
    TextSpan,
    Verdict,
)


def _span(document_id: str = "brief-1") -> TextSpan:
    return TextSpan(document_id=document_id, start=0, end=5, excerpt="hello")


def _lookup() -> CaseLookupResult:
    return CaseLookupResult(
        found=True,
        canonical_cite="Smith v. Jones, 123 F.3d 456 (9th Cir. 1999)",
        holding_text="Some holding.",
        quoted_text_match=None,
        source_url=None,
        lookup_status="found",
        notes=None,
    )


def _citation_finding(verdict: Verdict, confidence: float, cite: str = "Smith v. Jones") -> CitationFinding:
    return CitationFinding(
        citation=ExtractedCitation(
            cite=cite,
            proposition="A proposition.",
            quoted_language=None,
            claim_span=_span(),
        ),
        verdict=verdict,
        confidence=confidence,
        reasoning="...",
        evidence_quote=None,
        evidence_span=None,
        lookup=_lookup(),
    )


def _consistency_finding(verdict: Verdict, confidence: float, claim_text: str = "X happened") -> ConsistencyFinding:
    return ConsistencyFinding(
        claim=ExtractedClaim(claim_text=claim_text, claim_span=_span()),
        verdict=verdict,
        confidence=confidence,
        reasoning="...",
        evidence_quote=None,
        evidence_span=None,
        checked_documents=[],
    )


def test_verdict_weights_are_locked():
    assert VERDICT_WEIGHTS == {
        "contradicted": 1.0,
        "unsupported": 0.7,
        "could_not_verify": 0.4,
        "undisputed": 0.0,
        "supported": 0.0,
    }


def test_rank_findings_returns_top_n_in_score_order():
    citations = [
        _citation_finding("supported", 0.9, cite="cite-0"),       # 0.0 * 0.9 = 0.0
        _citation_finding("contradicted", 0.8, cite="cite-1"),    # 1.0 * 0.8 = 0.8
        _citation_finding("could_not_verify", 0.5, cite="cite-2"),  # 0.4 * 0.5 = 0.20
    ]
    consistency = [
        _consistency_finding("unsupported", 0.9, claim_text="claim-0"),     # 0.7 * 0.9 = 0.63
        _consistency_finding("contradicted", 0.95, claim_text="claim-1"),   # 1.0 * 0.95 = 0.95
        _consistency_finding("supported", 1.0, claim_text="claim-2"),       # 0.0
        _consistency_finding("unsupported", 0.4, claim_text="claim-3"),     # 0.7 * 0.4 = 0.28
    ]

    refs = rank_findings(citations, consistency, top_n=5)

    assert refs == [
        FindingRef(finding_type="consistency", finding_index=1),  # 0.95
        FindingRef(finding_type="citation", finding_index=1),     # 0.80
        FindingRef(finding_type="consistency", finding_index=0),  # 0.63
        FindingRef(finding_type="consistency", finding_index=3),  # 0.28
        FindingRef(finding_type="citation", finding_index=2),     # 0.20
    ]


def test_rank_findings_supported_findings_rank_lowest():
    # Two supported findings (weight 0.0) should appear after every non-supported.
    citations = [
        _citation_finding("supported", 1.0, cite="cite-0"),
        _citation_finding("could_not_verify", 0.1, cite="cite-1"),  # 0.04
    ]
    consistency = [
        _consistency_finding("supported", 1.0, claim_text="claim-0"),
        _consistency_finding("unsupported", 0.05, claim_text="claim-1"),  # 0.035
    ]

    refs = rank_findings(citations, consistency, top_n=5)

    assert refs[0] == FindingRef(finding_type="citation", finding_index=1)
    assert refs[1] == FindingRef(finding_type="consistency", finding_index=1)
    # Tail: the two supported findings, in original order across citations then consistency.
    tail = refs[2:]
    assert FindingRef(finding_type="citation", finding_index=0) in tail
    assert FindingRef(finding_type="consistency", finding_index=0) in tail


def test_rank_findings_stable_sort_breaks_ties_by_original_order():
    # All same score (contradicted * 0.5 = 0.5). Citations come before consistency
    # in our concatenation, and within each list they keep their input order.
    citations = [
        _citation_finding("contradicted", 0.5, cite="cite-0"),
        _citation_finding("contradicted", 0.5, cite="cite-1"),
    ]
    consistency = [
        _consistency_finding("contradicted", 0.5, claim_text="claim-0"),
        _consistency_finding("contradicted", 0.5, claim_text="claim-1"),
    ]

    refs = rank_findings(citations, consistency, top_n=10)

    assert refs == [
        FindingRef(finding_type="citation", finding_index=0),
        FindingRef(finding_type="citation", finding_index=1),
        FindingRef(finding_type="consistency", finding_index=0),
        FindingRef(finding_type="consistency", finding_index=1),
    ]


def test_rank_findings_top_n_two_returns_exactly_two():
    citations = [
        _citation_finding("contradicted", 0.9, cite="cite-0"),
        _citation_finding("unsupported", 0.5, cite="cite-1"),
    ]
    consistency = [
        _consistency_finding("contradicted", 0.95, claim_text="claim-0"),
    ]

    refs = rank_findings(citations, consistency, top_n=2)

    assert len(refs) == 2
    assert refs[0] == FindingRef(finding_type="consistency", finding_index=0)
    assert refs[1] == FindingRef(finding_type="citation", finding_index=0)


def test_rank_findings_empty_input_returns_empty_list():
    assert rank_findings([], [], top_n=5) == []


def test_rank_findings_top_n_larger_than_input_returns_all():
    citations = [_citation_finding("contradicted", 0.9, cite="cite-0")]
    consistency = [_consistency_finding("unsupported", 0.5, claim_text="claim-0")]

    refs = rank_findings(citations, consistency, top_n=50)

    assert len(refs) == 2


def test_finding_index_points_back_into_original_list():
    citations = [
        _citation_finding("supported", 0.5, cite="cite-A"),
        _citation_finding("contradicted", 0.9, cite="cite-B"),
        _citation_finding("could_not_verify", 0.3, cite="cite-C"),
    ]
    consistency = [
        _consistency_finding("unsupported", 0.8, claim_text="claim-X"),
        _consistency_finding("contradicted", 0.6, claim_text="claim-Y"),
    ]

    refs = rank_findings(citations, consistency, top_n=5)

    for ref in refs:
        if ref.finding_type == "citation":
            assert 0 <= ref.finding_index < len(citations)
            citations[ref.finding_index]  # round-trip indexing must not raise
        else:
            assert 0 <= ref.finding_index < len(consistency)
            consistency[ref.finding_index]

    # Top-ranked is the highest-confidence contradiction, which is cite-B (index 1).
    top = refs[0]
    assert top.finding_type == "citation"
    assert top.finding_index == 1
    assert citations[top.finding_index].citation.cite == "cite-B"
