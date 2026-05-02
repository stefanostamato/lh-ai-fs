import asyncio

from agents.crossdoc_checker import (
    CrossDocCheckerResponse,
    check_claim,
)
from schemas import (
    ConsistencyFinding,
    ExtractedClaim,
    ParagraphSpan,
    ParsedRecord,
    TextSpan,
)


BRIEF_ID = "brief-x"


def _claim(text: str = "The incident occurred on March 14, 2021.") -> ExtractedClaim:
    return ExtractedClaim(
        claim_text=text,
        claim_span=TextSpan(
            document_id=BRIEF_ID,
            start=0,
            end=len(text),
            excerpt=text,
        ),
    )


def _record(document_id: str, display_name: str, raw_text: str) -> ParsedRecord:
    return ParsedRecord(
        document_id=document_id,
        display_name=display_name,
        raw_text=raw_text,
        paragraphs=[
            ParagraphSpan(
                document_id=document_id,
                paragraph_index=0,
                span=TextSpan(
                    document_id=document_id,
                    start=0,
                    end=len(raw_text),
                    excerpt=raw_text.strip(),
                ),
            )
        ],
    )


def test_contradicted_claim_resolves_evidence_span_to_correct_document_id(mock_llm):
    claim = _claim("The incident occurred on March 14, 2021.")
    record_text = "The incident took place on April 2, 2021, per the official log."
    record = _record(
        document_id="rec-a",
        display_name="Record A",
        raw_text=record_text,
    )

    payload = CrossDocCheckerResponse(
        verdict="contradicted",
        confidence=0.9,
        reasoning="Record A states a different date for the incident.",
        evidence_quote="April 2, 2021",
        evidence_doc_name="Record A",
    )
    mock_llm({"factual claim": payload})

    finding = asyncio.run(check_claim(claim, [record]))

    assert isinstance(finding, ConsistencyFinding)
    assert finding.verdict == "contradicted"
    assert finding.evidence_span is not None
    assert finding.evidence_span.document_id == "rec-a"
    expected_start = record_text.find("April 2, 2021")
    assert finding.evidence_span.start == expected_start
    assert finding.evidence_span.end == expected_start + len("April 2, 2021")
    assert finding.evidence_span.excerpt == "April 2, 2021"
    assert finding.evidence_quote == "April 2, 2021"


def test_undisputed_when_no_record_addresses_claim(mock_llm):
    claim = _claim("The incident occurred on March 14, 2021.")
    record = _record(
        document_id="rec-a",
        display_name="Record A",
        raw_text="This record discusses unrelated maintenance schedules.",
    )

    payload = CrossDocCheckerResponse(
        verdict="undisputed",
        confidence=0.3,
        reasoning="No record contradicts the date of the incident.",
        evidence_quote=None,
        evidence_doc_name=None,
    )
    mock_llm({"factual claim": payload})

    finding = asyncio.run(check_claim(claim, [record]))

    assert finding.verdict == "undisputed"
    assert finding.evidence_span is None
    assert finding.evidence_quote is None


def test_supported_claim_resolves_to_other_record(mock_llm):
    claim = _claim("The incident occurred on March 14, 2021.")
    record_a = _record(
        document_id="rec-a",
        display_name="Record A",
        raw_text="Record A contains scheduling notes only.",
    )
    record_b_text = "Per the dispatch log, the incident occurred on March 14, 2021."
    record_b = _record(
        document_id="rec-b",
        display_name="Record B",
        raw_text=record_b_text,
    )

    payload = CrossDocCheckerResponse(
        verdict="supported",
        confidence=0.92,
        reasoning="Record B confirms the date stated in the claim.",
        evidence_quote="the incident occurred on March 14, 2021",
        evidence_doc_name="Record B",
    )
    mock_llm({"factual claim": payload})

    finding = asyncio.run(check_claim(claim, [record_a, record_b]))

    assert finding.verdict == "supported"
    assert finding.evidence_span is not None
    assert finding.evidence_span.document_id == "rec-b"
    expected_start = record_b_text.find("the incident occurred on March 14, 2021")
    assert finding.evidence_span.start == expected_start
    assert finding.evidence_span.excerpt == "the incident occurred on March 14, 2021"


def test_unknown_evidence_doc_name_downgrades_to_could_not_verify(mock_llm):
    claim = _claim()
    record = _record(
        document_id="rec-a",
        display_name="Record A",
        raw_text="Some content.",
    )

    payload = CrossDocCheckerResponse(
        verdict="contradicted",
        confidence=0.85,
        reasoning="A document called Nonexistent Doc says otherwise.",
        evidence_quote="anything",
        evidence_doc_name="Nonexistent Doc",
    )
    mock_llm({"factual claim": payload})

    finding = asyncio.run(check_claim(claim, [record]))

    assert finding.verdict == "could_not_verify"
    assert finding.evidence_span is None
    assert "could not be located" in finding.reasoning.lower() or "not be located" in finding.reasoning.lower()


def test_quote_not_found_in_record_downgrades_to_could_not_verify(mock_llm):
    claim = _claim()
    record = _record(
        document_id="rec-a",
        display_name="Record A",
        raw_text="Record A only contains scheduling notes.",
    )

    payload = CrossDocCheckerResponse(
        verdict="supported",
        confidence=0.7,
        reasoning="Record A has the date.",
        evidence_quote="March 14, 2021",  # not actually in the record's text
        evidence_doc_name="Record A",
    )
    mock_llm({"factual claim": payload})

    finding = asyncio.run(check_claim(claim, [record]))

    assert finding.verdict == "could_not_verify"
    assert finding.evidence_span is None
    assert "could not be located" in finding.reasoning.lower() or "not be located" in finding.reasoning.lower()


def test_multi_line_quote_with_omitted_fields_resolves_via_line_fallback(mock_llm):
    """The model often stitches together non-adjacent lines from a record,
    skipping intermediate fields ("Address: ...", "DOB: ..."). Whitespace-
    normalized find can't match the whole needle in that case; falling back
    to the longest line lets us anchor on it instead of downgrading."""

    record_text = (
        "Injured Party:\n"
        "    Name:          Carlos M. Rivera\n"
        "    DOB:           06/18/1985\n"
        "    Address:       1847 Bonnie Brae St, Los Angeles, CA\n"
        "    Employer:      Apex Staffing Solutions\n"
        "    Occupation:    Journeyman Scaffolder\n"
    )
    record = _record(
        document_id="rec-a",
        display_name="Record A",
        raw_text=record_text,
    )
    claim = _claim("Rivera was employed by Apex Staffing Solutions.")

    # Model returns a multi-line block that omits DOB and Address.
    payload = CrossDocCheckerResponse(
        verdict="supported",
        confidence=0.9,
        reasoning="Record A names Apex.",
        evidence_quote=(
            "Injured Party:\n"
            "    Name:          Carlos M. Rivera\n"
            "    Employer:      Apex Staffing Solutions\n"
            "    Occupation:    Journeyman Scaffolder"
        ),
        evidence_doc_name="Record A",
    )
    mock_llm({"factual claim": payload})

    finding = asyncio.run(check_claim(claim, [record]))
    assert finding.verdict == "supported"
    assert finding.evidence_span is not None
    assert finding.evidence_span.document_id == "rec-a"
    # The fallback anchored on whichever long line matched - it should
    # contain "Apex Staffing Solutions" or another distinctive line.
    assert finding.evidence_span.excerpt in record_text


def test_whitespace_differences_in_quote_resolve_via_normalized_find(mock_llm):
    """Records with column-padded key/value blocks ("Employer:    Apex
    Staffing Solutions") should still resolve when the model echoes the
    same content with collapsed whitespace ("Employer: Apex Staffing
    Solutions"). The downgrade should not fire."""

    record_text = (
        "Injured Party:\n"
        "    Name:          Carlos M. Rivera\n"
        "    Employer:      Apex Staffing Solutions\n"
        "    Occupation:    Journeyman Scaffolder\n"
    )
    record = _record(
        document_id="rec-a",
        display_name="Record A",
        raw_text=record_text,
    )
    claim = _claim("Rivera was employed by Apex Staffing Solutions.")

    # Model echoes the value with single-space padding, not the column form.
    payload = CrossDocCheckerResponse(
        verdict="supported",
        confidence=0.85,
        reasoning="Record A names Apex Staffing Solutions as Rivera's employer.",
        evidence_quote="Employer: Apex Staffing Solutions",
        evidence_doc_name="Record A",
    )
    mock_llm({"factual claim": payload})

    finding = asyncio.run(check_claim(claim, [record]))

    assert finding.verdict == "supported"
    assert finding.evidence_span is not None
    assert finding.evidence_span.document_id == "rec-a"
    # Excerpt comes from the record verbatim - the column-padded form, not
    # the normalized form the model produced.
    assert "Apex Staffing Solutions" in finding.evidence_span.excerpt
    # The literal "Employer: Apex" form should not have been found, so the
    # excerpt is the column-padded original.
    assert "Employer:      Apex Staffing Solutions" in record_text
    assert finding.evidence_span.excerpt.startswith("Employer:")


def test_checked_documents_lists_every_record_id(mock_llm):
    claim = _claim()
    records = [
        _record("rec-alpha", "Alpha Record", "Some text."),
        _record("rec-beta", "Beta Record", "Other text."),
        _record("rec-gamma", "Gamma Record", "More text."),
    ]
    payload = CrossDocCheckerResponse(
        verdict="undisputed",
        confidence=0.2,
        reasoning="No record contradicts the claim.",
        evidence_quote=None,
        evidence_doc_name=None,
    )
    mock_llm({"factual claim": payload})

    finding = asyncio.run(check_claim(claim, records))

    assert finding.checked_documents == ["rec-alpha", "rec-beta", "rec-gamma"]


def test_prompt_labels_records_by_display_name_not_document_id(mock_llm):
    claim = _claim()
    records = [
        _record("rec-secret-id-001", "Friendly Record Name", "alpha content"),
        _record("rec-secret-id-002", "Another Friendly Name", "beta content"),
    ]
    payload = CrossDocCheckerResponse(
        verdict="undisputed",
        confidence=0.2,
        reasoning="No coverage.",
        evidence_quote=None,
        evidence_doc_name=None,
    )
    calls = mock_llm({"factual claim": payload})

    asyncio.run(check_claim(claim, records))

    assert len(calls) == 1
    prompt = calls[0]["prompt"]
    assert "Friendly Record Name" in prompt
    assert "Another Friendly Name" in prompt
    assert "rec-secret-id-001" not in prompt
    assert "rec-secret-id-002" not in prompt
