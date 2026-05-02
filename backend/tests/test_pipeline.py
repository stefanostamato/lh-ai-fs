import asyncio

from llm import LlmError
from pipeline import run_pipeline
from schemas import (
    DocumentInput,
    DocumentRole,
    DocumentSet,
    Report,
)


# Brief text the deterministic parser will split into a couple of paragraphs.
# The mock LLM responses below pretend to "find" the citations and claims at
# the offsets where they actually appear in this text.
SYNTHETIC_BRIEF_TEXT = (
    "First paragraph of the moving brief. The moving party cites Doe v. Roe, "
    "111 F.3d 222 (9th Cir. 1999) for the proposition that the sky is blue.\n\n"
    "Second paragraph asserts the incident happened on a Tuesday at noon. "
    "It also relies on Smith v. Jones, 222 F.3d 333 (9th Cir. 2001).\n"
)

SYNTHETIC_RECORD_A_TEXT = (
    "Record A: the on-scene officer reported the incident occurred Wednesday "
    "evening, not Tuesday at noon.\n"
)

SYNTHETIC_RECORD_B_TEXT = (
    "Record B: the witness signed a statement saying she was at the scene.\n"
)


def _doc_set(
    *,
    brief_id: str = "test-brief",
    brief_text: str = SYNTHETIC_BRIEF_TEXT,
    records: list[tuple[str, str, str]] | None = None,
) -> DocumentSet:
    """Build a synthetic DocumentSet. records: list of (id, display_name, text)."""

    records = records or [
        ("test-record-a", "Record A", SYNTHETIC_RECORD_A_TEXT),
        ("test-record-b", "Record B", SYNTHETIC_RECORD_B_TEXT),
    ]
    docs: list[DocumentInput] = [
        DocumentInput(
            document_id=brief_id,
            display_name="Brief Under Review",
            role=DocumentRole.BRIEF,
            text=brief_text,
        )
    ]
    for rid, name, text in records:
        docs.append(
            DocumentInput(
                document_id=rid,
                display_name=name,
                role=DocumentRole.RECORD,
                text=text,
            )
        )
    return DocumentSet(documents=docs)


# Distinctive substrings of each agent's prompt, used to route mock responses.
CITATION_EXTRACTOR_KEY = "cataloguing the legal citations"
CITATION_VERIFIER_KEY = "verifying one citation"
CLAIM_EXTRACTOR_KEY = "extracting factual claims"
CROSSDOC_CHECKER_KEY = "checking one factual claim"
MEMO_WRITER_KEY = "drafting a one-paragraph memo"


def _citation_extractor_payload(brief_text: str):
    from agents.citation_extractor import CitationDraft, CitationExtractionResponse

    cite_one = "Doe v. Roe, 111 F.3d 222 (9th Cir. 1999)"
    cite_two = "Smith v. Jones, 222 F.3d 333 (9th Cir. 2001)"
    s1 = brief_text.find(cite_one)
    s2 = brief_text.find(cite_two)
    return CitationExtractionResponse(
        citations=[
            CitationDraft(
                cite=cite_one,
                proposition="The sky is blue.",
                quoted_language=None,
                span_start=s1,
                span_end=s1 + len(cite_one),
            ),
            CitationDraft(
                cite=cite_two,
                proposition="Tuesdays are workdays.",
                quoted_language=None,
                span_start=s2,
                span_end=s2 + len(cite_two),
            ),
        ]
    )


def _claim_extractor_payload(brief_text: str):
    from agents.claim_extractor import ClaimDraft, ClaimExtractionResponse

    claim = "the incident happened on a Tuesday at noon"
    s = brief_text.find(claim)
    return ClaimExtractionResponse(
        claims=[
            ClaimDraft(
                claim_text=claim,
                span_start=s,
                span_end=s + len(claim),
            )
        ]
    )


def _citation_verifier_payload():
    from agents.citation_verifier import CitationJudgmentResponse

    return CitationJudgmentResponse(
        verdict="supported",
        confidence=0.85,
        reasoning="Holding aligns with the proposition.",
        evidence_quote="the sky is, indeed, blue",
    )


def _crossdoc_checker_payload(record_display_name: str, evidence_quote: str):
    from agents.crossdoc_checker import CrossDocCheckerResponse

    return CrossDocCheckerResponse(
        verdict="contradicted",
        confidence=0.8,
        reasoning="Record contradicts the brief on timing.",
        evidence_quote=evidence_quote,
        evidence_doc_name=record_display_name,
    )


def _memo_payload():
    from agents.memo_writer import MemoResponse

    return MemoResponse(
        text=(
            "The brief's timing claim is contradicted by Record A. "
            "The cited authority appears to support the brief on its narrow proposition."
        )
    )


def _happy_responder(brief_text: str, record_display_name: str, evidence_quote: str):
    citation_resp = _citation_extractor_payload(brief_text)
    claim_resp = _claim_extractor_payload(brief_text)
    verifier_resp = _citation_verifier_payload()
    crossdoc_resp = _crossdoc_checker_payload(record_display_name, evidence_quote)
    memo_resp = _memo_payload()

    def respond(prompt: str):
        if CITATION_EXTRACTOR_KEY in prompt:
            return citation_resp
        if CLAIM_EXTRACTOR_KEY in prompt:
            return claim_resp
        if CITATION_VERIFIER_KEY in prompt:
            return verifier_resp
        if CROSSDOC_CHECKER_KEY in prompt:
            return crossdoc_resp
        if MEMO_WRITER_KEY in prompt:
            return memo_resp
        raise KeyError(f"unexpected prompt: {prompt[:120]!r}")

    return respond


# --- Tests -----------------------------------------------------------------


def _found_lookup_result(cite: str) -> "CaseLookupResult":
    from schemas import CaseLookupResult

    return CaseLookupResult(
        found=True,
        canonical_cite=cite,
        holding_text="The court held the sky is, indeed, blue.",
        quoted_text_match=None,
        source_url="https://www.courtlistener.com/opinion/1/example/",
        lookup_status="found",
        notes=None,
    )


def test_run_pipeline_happy_path(mock_llm, mock_lookup):
    doc_set = _doc_set()
    mock_llm(_happy_responder(SYNTHETIC_BRIEF_TEXT, "Record A", "the incident occurred Wednesday"))
    mock_lookup(lambda citation: _found_lookup_result(citation.cite))

    report = asyncio.run(run_pipeline(doc_set))

    assert isinstance(report, Report)
    assert len(report.citations) == 2
    assert len(report.consistency) == 1
    assert report.memo.text
    assert report.meta.partial_failures == []
    assert report.meta.elapsed_ms > 0
    assert report.meta.token_usage.prompt >= 0
    assert report.meta.token_usage.completion >= 0
    assert report.meta.model
    assert all(c.lookup.source_url for c in report.citations)


def test_run_pipeline_concurrency_capped_at_five(mock_llm, mock_lookup, monkeypatch):
    cites = [f"Case{i} v. Other, {i} F.3d {i} (9th Cir. 200{i % 10})" for i in range(12)]
    brief_text = "Intro paragraph.\n\n" + " ".join(cites) + "\n"
    doc_set = _doc_set(brief_text=brief_text)

    from agents.citation_extractor import CitationDraft, CitationExtractionResponse

    drafts = []
    for cite in cites:
        s = brief_text.find(cite)
        drafts.append(
            CitationDraft(
                cite=cite,
                proposition="some prop",
                quoted_language=None,
                span_start=s,
                span_end=s + len(cite),
            )
        )
    citation_resp = CitationExtractionResponse(citations=drafts)
    from agents.claim_extractor import ClaimExtractionResponse
    claim_resp = ClaimExtractionResponse(claims=[])
    verifier_resp = _citation_verifier_payload()
    memo_resp = _memo_payload()

    mock_lookup(lambda citation: _found_lookup_result(citation.cite))

    in_flight = 0
    max_in_flight = 0

    async def patched_async(prompt: str, *args, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            await asyncio.sleep(0.05)
            if CITATION_EXTRACTOR_KEY in prompt:
                return citation_resp
            if CLAIM_EXTRACTOR_KEY in prompt:
                return claim_resp
            if CITATION_VERIFIER_KEY in prompt:
                return verifier_resp
            if MEMO_WRITER_KEY in prompt:
                return memo_resp
            raise KeyError(f"unexpected prompt: {prompt[:80]!r}")
        finally:
            in_flight -= 1

    # Satisfy the mock_llm fixture's "called at least once" teardown by
    # configuring it with a sentinel callable, then invoking the sync seam.
    # The fixture patches both sync and async; we override async after with
    # our concurrency-tracking version.
    mock_llm(lambda _prompt: None)
    import llm as _llm
    _llm.call_llm("seed", None)

    monkeypatch.setattr("llm.call_llm_async", patched_async)

    report = asyncio.run(run_pipeline(doc_set))

    assert max_in_flight <= 5
    assert max_in_flight >= 2  # confirm we did achieve some parallelism
    assert isinstance(report, Report)
    assert len(report.citations) == 12


def test_run_pipeline_single_agent_failure(mock_llm, mock_lookup):
    doc_set = _doc_set()

    citation_resp = _citation_extractor_payload(SYNTHETIC_BRIEF_TEXT)
    claim_resp = _claim_extractor_payload(SYNTHETIC_BRIEF_TEXT)
    verifier_resp = _citation_verifier_payload()
    crossdoc_resp = _crossdoc_checker_payload("Record A", "the incident occurred Wednesday")
    memo_resp = _memo_payload()

    verifier_call_count = {"n": 0}

    def respond(prompt: str):
        if CITATION_EXTRACTOR_KEY in prompt:
            return citation_resp
        if CLAIM_EXTRACTOR_KEY in prompt:
            return claim_resp
        if CITATION_VERIFIER_KEY in prompt:
            verifier_call_count["n"] += 1
            if verifier_call_count["n"] == 2:
                raise LlmError("simulated verifier failure")
            return verifier_resp
        if CROSSDOC_CHECKER_KEY in prompt:
            return crossdoc_resp
        if MEMO_WRITER_KEY in prompt:
            return memo_resp
        raise KeyError(prompt[:80])

    mock_llm(respond)
    mock_lookup(lambda citation: _found_lookup_result(citation.cite))

    report = asyncio.run(run_pipeline(doc_set))

    failed = [c for c in report.citations if c.verdict == "could_not_verify"]
    assert len(failed) >= 1
    assert any("agent error" in c.reasoning for c in failed)
    assert any(pf.agent == "citation_verifier" for pf in report.meta.partial_failures)


def test_run_pipeline_all_agents_fail(mock_llm):
    doc_set = _doc_set()

    # Seed the fixture's "called at least once" contract before flipping to
    # the all-raises responder. No mock_lookup here - the citation extractor
    # fails first, so there are no citations to look up and the network
    # seam is never reached.
    seeded = {"done": False}

    def respond(prompt: str):
        if not seeded["done"]:
            seeded["done"] = True
            return None
        raise LlmError("everything is on fire")

    mock_llm(respond)
    import llm as _llm
    _llm.call_llm("seed", None)  # consumes the one non-raising response

    report = asyncio.run(run_pipeline(doc_set))

    assert isinstance(report, Report)
    assert report.citations == []
    assert report.consistency == []
    assert report.memo is not None
    assert report.memo.text
    assert report.meta.partial_failures, "expected partial failures"


def test_run_pipeline_span_validation_unknown_doc(mock_llm, monkeypatch):
    """A finding whose evidence_span points at a doc not in the set must be
    downgraded to could_not_verify and produce a PartialFailure."""

    doc_set = _doc_set()

    from agents.citation_extractor import CitationDraft, CitationExtractionResponse
    from agents.claim_extractor import ClaimExtractionResponse

    cite = "Doe v. Roe, 111 F.3d 222 (9th Cir. 1999)"
    s = SYNTHETIC_BRIEF_TEXT.find(cite)
    citation_resp = CitationExtractionResponse(
        citations=[
            CitationDraft(
                cite=cite,
                proposition="The sky is blue.",
                quoted_language=None,
                span_start=s,
                span_end=s + len(cite),
            )
        ]
    )
    empty_claims = ClaimExtractionResponse(claims=[])

    # Patch verify_citation in the pipeline namespace to return a finding
    # whose evidence_span points at "ghost-doc" (not in the synthetic set).
    import pipeline as pipeline_mod
    from schemas import (
        CaseLookupResult,
        CitationFinding,
        TextSpan,
    )

    async def fake_verify(citation, lookup, *, usage=None):
        return CitationFinding(
            citation=citation,
            verdict="contradicted",
            confidence=0.9,
            reasoning="Pretend a contradiction.",
            evidence_quote="something",
            evidence_span=TextSpan(
                document_id="ghost-doc",
                start=0,
                end=5,
                excerpt="ghost",
            ),
            lookup=CaseLookupResult(
                found=True,
                canonical_cite=cite,
                holding_text="...",
                quoted_text_match=None,
                source_url=None,
                lookup_status="found",
                notes=None,
            ),
        )

    monkeypatch.setattr(pipeline_mod, "verify_citation", fake_verify)

    def respond(prompt: str):
        if CITATION_EXTRACTOR_KEY in prompt:
            return citation_resp
        if CLAIM_EXTRACTOR_KEY in prompt:
            return empty_claims
        if MEMO_WRITER_KEY in prompt:
            return _memo_payload()
        raise KeyError(f"unexpected prompt: {prompt[:80]!r}")

    mock_llm(respond)

    report = asyncio.run(run_pipeline(doc_set))

    assert len(report.citations) == 1
    assert report.citations[0].verdict == "could_not_verify"
    assert any(
        "unknown document_id" in pf.error or "ghost-doc" in pf.error
        for pf in report.meta.partial_failures
    )


def test_run_pipeline_agnostic_to_document_ids(mock_llm, mock_lookup):
    """Run with two differently-shaped synthetic DocumentSets. Both must
    produce shape-equivalent Reports, proving the orchestrator doesn't care
    about specific document_ids."""

    set_one = _doc_set(
        brief_id="alpha-brief",
        records=[("alpha-r1", "Alpha One", SYNTHETIC_RECORD_A_TEXT)],
    )
    set_two = _doc_set(
        brief_id="zeta-brief",
        records=[
            ("zeta-r1", "Zeta One", SYNTHETIC_RECORD_A_TEXT),
            ("zeta-r2", "Zeta Two", SYNTHETIC_RECORD_B_TEXT),
            ("zeta-r3", "Zeta Three", "Yet another record paragraph.\n"),
        ],
    )

    mock_lookup(lambda citation: _found_lookup_result(citation.cite))

    mock_llm(_happy_responder(SYNTHETIC_BRIEF_TEXT, "Alpha One", "the incident occurred Wednesday"))
    report_one = asyncio.run(run_pipeline(set_one))

    mock_llm(_happy_responder(SYNTHETIC_BRIEF_TEXT, "Zeta One", "the incident occurred Wednesday"))
    report_two = asyncio.run(run_pipeline(set_two))

    assert isinstance(report_one, Report)
    assert isinstance(report_two, Report)
    assert len(report_one.citations) == len(report_two.citations) == 2
    assert report_one.memo.text and report_two.memo.text
    assert report_one.meta.elapsed_ms > 0 and report_two.meta.elapsed_ms > 0


def test_run_pipeline_token_usage_aggregates(mock_llm, mock_lookup, monkeypatch):
    """Patch call_llm_async with a tracker that bumps the UsageCollector
    directly so we can verify the orchestrator threads `usage` through every
    LLM-bound agent call."""

    doc_set = _doc_set()
    happy = _happy_responder(SYNTHETIC_BRIEF_TEXT, "Record A", "the incident occurred Wednesday")

    total_calls = {"n": 0}
    usage_calls = {"n": 0}

    async def patched_async(prompt: str, *args, **kwargs):
        total_calls["n"] += 1
        usage = kwargs.get("usage")
        if usage is not None:
            usage_calls["n"] += 1
            usage.add(prompt=10, completion=5)
        return happy(prompt)

    mock_llm(lambda _prompt: None)
    import llm as _llm
    _llm.call_llm("seed", None)

    monkeypatch.setattr("llm.call_llm_async", patched_async)
    mock_lookup(lambda citation: _found_lookup_result(citation.cite))

    report = asyncio.run(run_pipeline(doc_set))

    # Every LLM-bound agent (extractors, verifier judgment, crossdoc, memo)
    # must thread `usage`. The lookup tool is a separate seam and does not
    # touch the LLM at all under CourtListenerLookup. The aggregate must
    # reflect every usage-threaded call.
    assert usage_calls["n"] >= 5
    assert report.meta.token_usage.prompt == 10 * usage_calls["n"]
    assert report.meta.token_usage.completion == 5 * usage_calls["n"]
    assert report.meta.elapsed_ms > 0
