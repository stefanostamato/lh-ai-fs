import asyncio
import re
import time
from typing import Awaitable, Callable

from agents.brief_parser import parse_brief
from agents.citation_extractor import extract_citations
from agents.citation_verifier import verify_citation
from agents.claim_extractor import extract_claims
from agents.crossdoc_checker import check_claim
from agents.memo_writer import MemoVerdictPromotionError, write_memo
from agents.record_parser import parse_record
from agents.tools.case_lookup import CaseLawLookup, ParametricLLMLookup
from llm import DEFAULT_MODEL, LlmError
from schemas import (
    CaseLookupResult,
    CitationFinding,
    ConsistencyFinding,
    DocumentSet,
    DocumentSummary,
    ExtractedCitation,
    ExtractedClaim,
    FindingRef,
    JudicialMemo,
    ParsedRecord,
    PartialFailure,
    Report,
    ReportMeta,
    TextSpan,
    TokenUsage,
)
from usage import UsageCollector


VERDICT_WEIGHTS: dict[str, float] = {
    "contradicted": 1.0,
    "unsupported": 0.7,
    "could_not_verify": 0.4,
    "undisputed": 0.0,
    "supported": 0.0,
}

CONCURRENCY_CAP: int = 5
TOP_N: int = 5

# The verifier swallows its own LLM-judgment errors and returns a
# could_not_verify finding whose reasoning starts with this prefix. The
# orchestrator surfaces those as agent-error partial failures so the report
# meta is honest about the failure even though the agent didn't raise.
_VERIFIER_JUDGMENT_FAIL_PREFIX = "Judgment LLM call failed"

FindingPair = tuple[FindingRef, CitationFinding | ConsistencyFinding]


def rank_findings(
    citations: list[CitationFinding],
    consistency: list[ConsistencyFinding],
    top_n: int = TOP_N,
) -> list[FindingRef]:
    """Sort findings by `verdict_weight * confidence` descending, take top_n.

    Stable: ties keep their original position. Citations are considered before
    consistency findings in the merged list, and within each list the input
    order is preserved.
    """

    refs: list[FindingRef] = [
        FindingRef(finding_type="citation", finding_index=i) for i in range(len(citations))
    ] + [
        FindingRef(finding_type="consistency", finding_index=i) for i in range(len(consistency))
    ]

    def score(ref: FindingRef) -> float:
        finding = (
            citations[ref.finding_index]
            if ref.finding_type == "citation"
            else consistency[ref.finding_index]
        )
        return VERDICT_WEIGHTS[finding.verdict] * finding.confidence

    refs.sort(key=score, reverse=True)
    return refs[:top_n]


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


def _citation_could_not_verify(
    citation: ExtractedCitation, reason: str, lookup: CaseLookupResult | None = None
) -> CitationFinding:
    return CitationFinding(
        citation=citation,
        verdict="could_not_verify",
        confidence=0.0,
        reasoning=reason,
        evidence_quote=None,
        evidence_span=None,
        lookup=lookup or _failed_lookup(),
    )


def _consistency_could_not_verify(
    claim: ExtractedClaim, records: list[ParsedRecord], reason: str
) -> ConsistencyFinding:
    return ConsistencyFinding(
        claim=claim,
        verdict="could_not_verify",
        confidence=0.0,
        reasoning=reason,
        evidence_quote=None,
        evidence_span=None,
        checked_documents=[r.document_id for r in records],
    )


def _stub_memo() -> JudicialMemo:
    return JudicialMemo(
        text="No memo could be drafted for this run.",
        top_findings=[],
        partial_failure_note="Memo writer failed.",
    )


async def _safe_verify(
    citation: ExtractedCitation,
    lookup: CaseLawLookup,
    failures: list[PartialFailure],
    sem: asyncio.Semaphore,
    usage: UsageCollector,
) -> CitationFinding:
    async with sem:
        try:
            finding = await verify_citation(citation, lookup, usage=usage)
        except Exception as exc:
            failures.append(PartialFailure(agent="citation_verifier", error=str(exc)))
            return _citation_could_not_verify(citation, f"agent error: {exc}")

    if (
        finding.verdict == "could_not_verify"
        and finding.reasoning.startswith(_VERIFIER_JUDGMENT_FAIL_PREFIX)
    ):
        failures.append(
            PartialFailure(agent="citation_verifier", error=finding.reasoning)
        )
        return _citation_could_not_verify(
            finding.citation,
            f"agent error: {finding.reasoning}",
            lookup=finding.lookup,
        )
    return finding


async def _safe_check(
    claim: ExtractedClaim,
    records: list[ParsedRecord],
    failures: list[PartialFailure],
    sem: asyncio.Semaphore,
    usage: UsageCollector,
) -> ConsistencyFinding:
    async with sem:
        try:
            return await check_claim(claim, records, usage=usage)
        except Exception as exc:
            failures.append(PartialFailure(agent="crossdoc_checker", error=str(exc)))
            return _consistency_could_not_verify(claim, records, f"agent error: {exc}")


async def _safe_extractor(
    name: str,
    coro_factory: Callable[[], Awaitable[list]],
    failures: list[PartialFailure],
) -> list:
    try:
        return await coro_factory()
    except Exception as exc:
        failures.append(PartialFailure(agent=name, error=str(exc)))
        return []


async def _safe_memo(
    top_findings: list[FindingPair],
    failures: list[PartialFailure],
    usage: UsageCollector,
) -> JudicialMemo:
    try:
        return await write_memo(top_findings, failures, usage=usage)
    except (LlmError, MemoVerdictPromotionError, Exception) as exc:
        failures.append(PartialFailure(agent="memo_writer", error=str(exc)))
        return _stub_memo()


def _bad_doc_id(finding, doc_set: DocumentSet, claim_span: TextSpan) -> str | None:
    """Return the offending document_id if a finding's spans reference a doc
    that isn't in the active set, else None."""

    if not doc_set.has_id(claim_span.document_id):
        return claim_span.document_id
    if finding.evidence_span is not None and not doc_set.has_id(
        finding.evidence_span.document_id
    ):
        return finding.evidence_span.document_id
    return None


def _validate_citation_spans(
    findings: list[CitationFinding],
    doc_set: DocumentSet,
    failures: list[PartialFailure],
) -> list[CitationFinding]:
    out: list[CitationFinding] = []
    for f in findings:
        bad = _bad_doc_id(f, doc_set, f.citation.claim_span)
        if bad is None:
            out.append(f)
            continue
        msg = f"span referenced unknown document_id {bad!r}"
        failures.append(PartialFailure(agent="citation_verifier", error=msg))
        out.append(_citation_could_not_verify(f.citation, f"agent error: {msg}"))
    return out


def _validate_consistency_spans(
    findings: list[ConsistencyFinding],
    doc_set: DocumentSet,
    records: list[ParsedRecord],
    failures: list[PartialFailure],
) -> list[ConsistencyFinding]:
    out: list[ConsistencyFinding] = []
    for f in findings:
        bad = _bad_doc_id(f, doc_set, f.claim.claim_span)
        if bad is None:
            out.append(f)
            continue
        msg = f"span referenced unknown document_id {bad!r}"
        failures.append(PartialFailure(agent="crossdoc_checker", error=msg))
        out.append(
            _consistency_could_not_verify(f.claim, records, f"agent error: {msg}")
        )
    return out


_CLAIM_NORMALIZE_RE = re.compile(r"\W+")
_DEDUPE_PHRASE_LEN = 3  # words; 3 covers dates ("march 10 2023") and short noun phrases


def _claim_phrases(text: str, n: int = _DEDUPE_PHRASE_LEN) -> set[str]:
    """Return the set of n-word normalized phrases inside `text`.

    Lowercased, punctuation stripped. Used to spot two near-duplicate
    consistency findings that re-assert the same distinctive fact in
    different surrounding prose ("Rivera filed... on March 10, 2023" and
    "Rivera did not file... until March 10, 2023" both contain
    `march 10 2023`).
    """

    words = _CLAIM_NORMALIZE_RE.sub(" ", text.lower()).split()
    if len(words) < n:
        return set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def _dedupe_consistency_findings(
    findings: list[ConsistencyFinding],
) -> list[ConsistencyFinding]:
    """Collapse consistency findings that say the same thing twice.

    Two passes:
      1. Findings with an `evidence_span` collapse when they share
         `(verdict, evidence_span.document_id, evidence_span.start, end)`.
         The judge sees "the record disputes this date" once, not once per
         sentence in the brief that asserts the bad date.
      2. Findings without an `evidence_span` (undisputed, could_not_verify)
         collapse when they share a verdict and any 3-word phrase between
         their claim texts. The brief often re-asserts the same fact in
         different surrounding prose; emitting "this is undisputed" twice
         is noise.

    The first occurrence wins; later duplicates are dropped.
    """

    seen_evidence: set[tuple[str, str, int, int]] = set()
    kept_phrases_by_verdict: dict[str, set[str]] = {}
    out: list[ConsistencyFinding] = []
    for f in findings:
        if f.evidence_span is not None:
            key = (
                f.verdict,
                f.evidence_span.document_id,
                f.evidence_span.start,
                f.evidence_span.end,
            )
            if key in seen_evidence:
                continue
            seen_evidence.add(key)
            out.append(f)
            continue
        phrases = _claim_phrases(f.claim.claim_text)
        already = kept_phrases_by_verdict.setdefault(f.verdict, set())
        if phrases and phrases & already:
            continue
        already |= phrases
        out.append(f)
    return out


async def run_pipeline(doc_set: DocumentSet) -> Report:
    """Wire every agent end-to-end on a `DocumentSet`. Returns a `Report`.

    The orchestrator is agnostic to which documents the set contains - it
    only knows there is one BRIEF and N RECORDs. Failures from any single
    agent are converted to `could_not_verify` findings + `PartialFailure`
    entries in `meta`. The Report is always well-formed.
    """

    started = time.monotonic()
    usage = UsageCollector()
    failures: list[PartialFailure] = []
    sem = asyncio.Semaphore(CONCURRENCY_CAP)
    lookup = ParametricLLMLookup()

    parsed_brief = parse_brief(doc_set.brief())
    parsed_records = [parse_record(r) for r in doc_set.records()]

    citations_extracted, claims_extracted = await asyncio.gather(
        _safe_extractor(
            "citation_extractor",
            lambda: extract_citations(parsed_brief, usage=usage),
            failures,
        ),
        _safe_extractor(
            "claim_extractor",
            lambda: extract_claims(parsed_brief, usage=usage),
            failures,
        ),
    )

    citation_findings, consistency_findings = await asyncio.gather(
        asyncio.gather(
            *[_safe_verify(c, lookup, failures, sem, usage) for c in citations_extracted]
        ),
        asyncio.gather(
            *[_safe_check(cl, parsed_records, failures, sem, usage) for cl in claims_extracted]
        ),
    )

    citation_findings = _validate_citation_spans(citation_findings, doc_set, failures)
    consistency_findings = _validate_consistency_spans(
        consistency_findings, doc_set, parsed_records, failures
    )
    consistency_findings = _dedupe_consistency_findings(consistency_findings)

    refs = rank_findings(citation_findings, consistency_findings, top_n=TOP_N)
    top_pairs: list[FindingPair] = [
        (
            ref,
            citation_findings[ref.finding_index]
            if ref.finding_type == "citation"
            else consistency_findings[ref.finding_index],
        )
        for ref in refs
    ]

    memo = await _safe_memo(top_pairs, failures, usage)

    elapsed_ms = max(int((time.monotonic() - started) * 1000), 1)
    document_summaries = [
        DocumentSummary(
            document_id=d.document_id,
            display_name=d.display_name,
            role=d.role,
        )
        for d in doc_set.documents
    ]
    return Report(
        consistency=consistency_findings,
        citations=citation_findings,
        memo=memo,
        meta=ReportMeta(
            model=DEFAULT_MODEL,
            elapsed_ms=elapsed_ms,
            token_usage=TokenUsage(prompt=usage.prompt, completion=usage.completion),
            partial_failures=failures,
            documents=document_summaries,
        ),
    )
