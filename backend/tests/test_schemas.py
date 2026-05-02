import json

import pytest
from pydantic import ValidationError

from schemas import (
    CaseLookupResult,
    CitationFinding,
    ConsistencyFinding,
    DocumentId,
    DocumentInput,
    DocumentRole,
    DocumentSet,
    DocumentSummary,
    ExtractedCitation,
    ExtractedClaim,
    FindingRef,
    JudicialMemo,
    ParagraphSpan,
    ParsedBrief,
    ParsedRecord,
    PartialFailure,
    Report,
    ReportMeta,
    TextSpan,
    TokenUsage,
    Verdict,
)


def _span(document_id: str = "brief-1", start: int = 0, end: int = 5, excerpt: str = "hello") -> TextSpan:
    return TextSpan(document_id=document_id, start=start, end=end, excerpt=excerpt)


def _citation(document_id: str = "brief-1") -> ExtractedCitation:
    return ExtractedCitation(
        cite="Smith v. Jones, 123 F.3d 456 (9th Cir. 1999)",
        proposition="The thing is true.",
        quoted_language=None,
        claim_span=_span(document_id=document_id),
    )


def _lookup() -> CaseLookupResult:
    return CaseLookupResult(
        found=True,
        canonical_cite="Smith v. Jones, 123 F.3d 456 (9th Cir. 1999)",
        holding_text="The thing is held to be true.",
        quoted_text_match=None,
        source_url=None,
        lookup_status="found",
        notes=None,
    )


def _doc(document_id: str = "brief-1", role: DocumentRole = DocumentRole.BRIEF) -> DocumentInput:
    return DocumentInput(
        document_id=document_id,
        display_name="Some Document",
        role=role,
        text="Hello world. This is a paragraph.\n\nAnd a second one.",
    )


def _docset_happy() -> DocumentSet:
    return DocumentSet(
        documents=[
            _doc("brief-1", DocumentRole.BRIEF),
            _doc("record-1", DocumentRole.RECORD),
            _doc("record-2", DocumentRole.RECORD),
        ]
    )


# --- DocumentId is a plain str alias ---


def test_document_id_is_str_alias():
    assert DocumentId is str


def test_textspan_accepts_arbitrary_document_id():
    span = TextSpan(document_id="anything-goes", start=0, end=5, excerpt="hello")
    assert span.document_id == "anything-goes"


# --- extra="forbid" round-trip checks ---


def test_textspan_round_trip_and_forbids_extra():
    span = _span()
    data = json.loads(span.model_dump_json())
    assert TextSpan.model_validate(data) == span
    with pytest.raises(ValidationError):
        TextSpan.model_validate({**data, "wat": 1})


def test_paragraph_span_round_trip_and_forbids_extra():
    p = ParagraphSpan(document_id="brief-1", paragraph_index=0, span=_span())
    data = json.loads(p.model_dump_json())
    assert ParagraphSpan.model_validate(data) == p
    with pytest.raises(ValidationError):
        ParagraphSpan.model_validate({**data, "extra_key": True})


def test_document_input_round_trip_and_forbids_extra():
    doc = _doc()
    data = json.loads(doc.model_dump_json())
    assert DocumentInput.model_validate(data) == doc
    with pytest.raises(ValidationError):
        DocumentInput.model_validate({**data, "rogue": 1})


def test_document_set_round_trip_and_forbids_extra():
    doc_set = _docset_happy()
    data = json.loads(doc_set.model_dump_json())
    assert DocumentSet.model_validate(data) == doc_set
    with pytest.raises(ValidationError):
        DocumentSet.model_validate({**data, "what": "no"})


def test_parsed_brief_round_trip_and_forbids_extra():
    brief = ParsedBrief(
        document_id="brief-1",
        raw_text="hello\n\nworld",
        paragraphs=[ParagraphSpan(document_id="brief-1", paragraph_index=0, span=_span())],
    )
    data = json.loads(brief.model_dump_json())
    assert ParsedBrief.model_validate(data) == brief
    with pytest.raises(ValidationError):
        ParsedBrief.model_validate({**data, "nope": True})


def test_parsed_record_round_trip_and_forbids_extra():
    rec = ParsedRecord(
        document_id="record-1",
        display_name="Police Report",
        raw_text="hello\n\nworld",
        paragraphs=[ParagraphSpan(document_id="record-1", paragraph_index=0, span=_span("record-1"))],
    )
    data = json.loads(rec.model_dump_json())
    assert ParsedRecord.model_validate(data) == rec
    with pytest.raises(ValidationError):
        ParsedRecord.model_validate({**data, "nope": True})


def test_extracted_citation_round_trip_and_forbids_extra():
    c = _citation()
    data = json.loads(c.model_dump_json())
    assert ExtractedCitation.model_validate(data) == c
    with pytest.raises(ValidationError):
        ExtractedCitation.model_validate({**data, "stowaway": 1})


def test_extracted_claim_round_trip_and_forbids_extra():
    claim = ExtractedClaim(claim_text="something happened", claim_span=_span())
    data = json.loads(claim.model_dump_json())
    assert ExtractedClaim.model_validate(data) == claim
    with pytest.raises(ValidationError):
        ExtractedClaim.model_validate({**data, "x": 1})


def test_case_lookup_result_round_trip_and_forbids_extra():
    look = _lookup()
    data = json.loads(look.model_dump_json())
    assert CaseLookupResult.model_validate(data) == look
    with pytest.raises(ValidationError):
        CaseLookupResult.model_validate({**data, "x": 1})


def test_citation_finding_round_trip_and_forbids_extra():
    finding = CitationFinding(
        citation=_citation(),
        verdict="supported",
        confidence=0.9,
        reasoning="The source supports it.",
        evidence_quote="held that the thing is true",
        evidence_span=_span("record-1"),
        lookup=_lookup(),
    )
    data = json.loads(finding.model_dump_json())
    assert CitationFinding.model_validate(data) == finding
    with pytest.raises(ValidationError):
        CitationFinding.model_validate({**data, "boom": 1})


def test_citation_finding_evidence_span_nullable_when_could_not_verify():
    finding = CitationFinding(
        citation=_citation(),
        verdict="could_not_verify",
        confidence=0.1,
        reasoning="Lookup failed.",
        evidence_quote=None,
        evidence_span=None,
        lookup=CaseLookupResult(
            found=False,
            canonical_cite=None,
            holding_text=None,
            quoted_text_match=None,
            source_url=None,
            lookup_status="lookup_failed",
            notes="parametric model returned no answer",
        ),
    )
    data = json.loads(finding.model_dump_json())
    rebuilt = CitationFinding.model_validate(data)
    assert rebuilt.evidence_span is None
    assert rebuilt.evidence_quote is None


def test_consistency_finding_round_trip_and_forbids_extra():
    finding = ConsistencyFinding(
        claim=ExtractedClaim(claim_text="X happened", claim_span=_span()),
        verdict="contradicted",
        confidence=0.7,
        reasoning="The record says otherwise.",
        evidence_quote="the record disagrees",
        evidence_span=_span("record-1"),
        checked_documents=["record-1", "record-2"],
    )
    data = json.loads(finding.model_dump_json())
    assert ConsistencyFinding.model_validate(data) == finding
    with pytest.raises(ValidationError):
        ConsistencyFinding.model_validate({**data, "uh": "oh"})


def test_consistency_finding_evidence_span_nullable_when_could_not_verify():
    finding = ConsistencyFinding(
        claim=ExtractedClaim(claim_text="X happened", claim_span=_span()),
        verdict="could_not_verify",
        confidence=0.0,
        reasoning="Agent error.",
        evidence_quote=None,
        evidence_span=None,
        checked_documents=[],
    )
    rebuilt = ConsistencyFinding.model_validate(json.loads(finding.model_dump_json()))
    assert rebuilt.evidence_span is None


def test_finding_ref_round_trip_and_forbids_extra():
    ref = FindingRef(finding_type="citation", finding_index=0)
    data = json.loads(ref.model_dump_json())
    assert FindingRef.model_validate(data) == ref
    with pytest.raises(ValidationError):
        FindingRef.model_validate({**data, "x": 1})


def test_judicial_memo_round_trip_and_forbids_extra():
    memo = JudicialMemo(
        text="The brief mostly holds up.",
        top_findings=[FindingRef(finding_type="citation", finding_index=0)],
        partial_failure_note=None,
    )
    data = json.loads(memo.model_dump_json())
    assert JudicialMemo.model_validate(data) == memo
    with pytest.raises(ValidationError):
        JudicialMemo.model_validate({**data, "x": 1})


def test_partial_failure_round_trip_and_forbids_extra():
    pf = PartialFailure(agent="citation_verifier", error="LlmError: timeout")
    data = json.loads(pf.model_dump_json())
    assert PartialFailure.model_validate(data) == pf
    with pytest.raises(ValidationError):
        PartialFailure.model_validate({**data, "x": 1})


def test_token_usage_round_trip_and_forbids_extra():
    tu = TokenUsage(prompt=100, completion=50)
    data = json.loads(tu.model_dump_json())
    assert TokenUsage.model_validate(data) == tu
    with pytest.raises(ValidationError):
        TokenUsage.model_validate({**data, "total": 150})


def test_document_summary_round_trip_and_forbids_extra():
    summary = DocumentSummary(
        document_id="brief-1",
        display_name="Some Document",
        role=DocumentRole.BRIEF,
    )
    data = json.loads(summary.model_dump_json())
    assert DocumentSummary.model_validate(data) == summary
    with pytest.raises(ValidationError):
        DocumentSummary.model_validate({**data, "rogue": 1})


def test_report_meta_round_trip_and_forbids_extra():
    meta = ReportMeta(
        model="gpt-4o",
        elapsed_ms=1234,
        token_usage=TokenUsage(prompt=10, completion=5),
        partial_failures=[PartialFailure(agent="x", error="y")],
        documents=[
            DocumentSummary(
                document_id="brief-1",
                display_name="Brief",
                role=DocumentRole.BRIEF,
            ),
            DocumentSummary(
                document_id="record-1",
                display_name="Record One",
                role=DocumentRole.RECORD,
            ),
        ],
    )
    data = json.loads(meta.model_dump_json())
    assert ReportMeta.model_validate(data) == meta
    with pytest.raises(ValidationError):
        ReportMeta.model_validate({**data, "x": 1})


def test_report_meta_requires_documents_field():
    with pytest.raises(ValidationError):
        ReportMeta.model_validate(
            {
                "model": "gpt-4o",
                "elapsed_ms": 1,
                "token_usage": {"prompt": 0, "completion": 0},
                "partial_failures": [],
            }
        )


def test_report_round_trip_and_forbids_extra():
    report = Report(
        consistency=[],
        citations=[],
        memo=JudicialMemo(text="ok", top_findings=[], partial_failure_note=None),
        meta=ReportMeta(
            model="gpt-4o",
            elapsed_ms=1,
            token_usage=TokenUsage(prompt=0, completion=0),
            partial_failures=[],
            documents=[],
        ),
    )
    data = json.loads(report.model_dump_json())
    assert Report.model_validate(data) == report
    with pytest.raises(ValidationError):
        Report.model_validate({**data, "x": 1})


# --- Verdict literal ---


def test_verdict_rejects_unknown_value():
    with pytest.raises(ValidationError):
        CitationFinding(
            citation=_citation(),
            verdict="bogus",
            confidence=0.5,
            reasoning="...",
            evidence_quote=None,
            evidence_span=None,
            lookup=_lookup(),
        )


def test_verdict_accepts_all_five_values():
    valid: list[Verdict] = [
        "supported",
        "contradicted",
        "unsupported",
        "undisputed",
        "could_not_verify",
    ]
    for v in valid:
        ConsistencyFinding(
            claim=ExtractedClaim(claim_text="x", claim_span=_span()),
            verdict=v,
            confidence=0.5,
            reasoning="...",
            evidence_quote=None,
            evidence_span=None,
            checked_documents=[],
        )


# --- DocumentSet validation ---


def test_documentset_rejects_zero_briefs():
    with pytest.raises(ValidationError):
        DocumentSet(
            documents=[
                _doc("record-1", DocumentRole.RECORD),
                _doc("record-2", DocumentRole.RECORD),
            ]
        )


def test_documentset_rejects_two_briefs():
    with pytest.raises(ValidationError):
        DocumentSet(
            documents=[
                _doc("brief-a", DocumentRole.BRIEF),
                _doc("brief-b", DocumentRole.BRIEF),
                _doc("record-1", DocumentRole.RECORD),
            ]
        )


def test_documentset_rejects_duplicate_ids():
    with pytest.raises(ValidationError):
        DocumentSet(
            documents=[
                _doc("dup", DocumentRole.BRIEF),
                _doc("dup", DocumentRole.RECORD),
            ]
        )


# --- DocumentSet helpers ---


def test_documentset_brief_returns_single_brief():
    doc_set = _docset_happy()
    brief = doc_set.brief()
    assert brief.role == DocumentRole.BRIEF
    assert brief.document_id == "brief-1"


def test_documentset_records_returns_only_records():
    doc_set = _docset_happy()
    records = doc_set.records()
    assert [r.document_id for r in records] == ["record-1", "record-2"]
    assert all(r.role == DocumentRole.RECORD for r in records)


def test_documentset_by_id_returns_match():
    doc_set = _docset_happy()
    assert doc_set.by_id("record-2").document_id == "record-2"


def test_documentset_by_id_raises_keyerror_when_missing():
    doc_set = _docset_happy()
    with pytest.raises(KeyError):
        doc_set.by_id("nope")


def test_documentset_has_id():
    doc_set = _docset_happy()
    assert doc_set.has_id("brief-1") is True
    assert doc_set.has_id("record-1") is True
    assert doc_set.has_id("not-here") is False
