import logging
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

import llm
from agents.prompts.crossdoc_checker import build_crossdoc_checker_prompt
from schemas import (
    ConsistencyFinding,
    ExtractedClaim,
    ParsedRecord,
    TextSpan,
)
from usage import UsageCollector


logger = logging.getLogger(__name__)


_DOWNGRADE_REASON = (
    "Model returned a doc/quote that could not be located in the record."
)


# The cross-doc checker emits a narrower vocabulary than the full `Verdict`:
# `unsupported` belongs to citation-checking (source exists, doesn't back the
# proposition); `could_not_verify` is reserved for true model failures and is
# only produced by the orchestrator/downgrade path, never by the model.
CrossDocVerdict = Literal["supported", "contradicted", "undisputed"]


class CrossDocCheckerResponse(BaseModel):
    """Structured-output envelope for the cross-doc checker LLM call."""

    model_config = ConfigDict(extra="forbid")

    verdict: CrossDocVerdict = Field(
        description="One of 'supported', 'contradicted', 'undisputed'."
    )
    confidence: float = Field(description="Confidence in [0.0, 1.0].")
    reasoning: str = Field(
        description="One or two sentences explaining the verdict."
    )
    evidence_quote: str | None = Field(
        default=None,
        description=(
            "Verbatim passage from the record document the verdict relies "
            "on, or null when there is no supporting/contradicting passage."
        ),
    )
    evidence_doc_name: str | None = Field(
        default=None,
        description=(
            "Display name of the record document the evidence_quote came "
            "from, or null. Must match a record block label in the prompt."
        ),
    )


def _whitespace_normalized_find(haystack: str, needle: str) -> tuple[int, int] | None:
    """Locate `needle` in `haystack` ignoring whitespace differences.

    Records often format key/value blocks with column padding ("Employer:
    Apex Staffing Solutions"); the model echoes the values with single
    spaces ("Employer: Apex Staffing Solutions"). A literal `find` misses,
    we downgrade to could_not_verify, and a real `supported` finding gets
    suppressed. Build a regex that matches the needle's tokens with `\\s+`
    between them.

    If the full needle does not match - which happens when the model's
    quote stitches together non-adjacent lines from a record (omitting
    intermediate fields) - fall back to the longest single line of the
    needle and try to anchor on that. A partial-but-real evidence span is
    better than a downgrade, and the model's reasoning can still be
    surfaced verbatim above the span.

    Returns `(start, end)` offsets into `haystack` for the matched span, or
    None if no match.
    """

    tokens = needle.split()
    if not tokens:
        return None
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    match = re.search(pattern, haystack)
    if match is not None:
        return match.start(), match.end()

    # Fallback: try each line of the needle, longest first, and anchor on
    # whichever exists in the haystack. Skips trivial 1-2 token lines so
    # we don't anchor on something like "Name:" alone.
    lines = sorted(
        (line for line in needle.splitlines() if len(line.split()) >= 3),
        key=lambda s: len(s.split()),
        reverse=True,
    )
    for line in lines:
        line_tokens = line.split()
        line_pattern = r"\s+".join(re.escape(t) for t in line_tokens)
        m = re.search(line_pattern, haystack)
        if m is not None:
            return m.start(), m.end()
    return None


def _resolve_evidence_span(
    records: list[ParsedRecord],
    evidence_doc_name: str | None,
    evidence_quote: str | None,
) -> TextSpan | None:
    """Map (display_name, quote) back to a TextSpan against a real record.

    Returns None if either the display name does not match any record or the
    quote cannot be located in that record's raw text - even after
    whitespace-normalized search. The caller treats None as a downgrade
    signal.
    """

    if not evidence_doc_name or not evidence_quote:
        return None

    record = next(
        (r for r in records if r.display_name == evidence_doc_name), None
    )
    if record is None:
        return None

    found = record.raw_text.find(evidence_quote)
    if found >= 0:
        return TextSpan(
            document_id=record.document_id,
            start=found,
            end=found + len(evidence_quote),
            excerpt=record.raw_text[found : found + len(evidence_quote)],
        )

    fuzzy = _whitespace_normalized_find(record.raw_text, evidence_quote)
    if fuzzy is None:
        return None
    start, end = fuzzy
    return TextSpan(
        document_id=record.document_id,
        start=start,
        end=end,
        excerpt=record.raw_text[start:end],
    )


async def check_claim(
    claim: ExtractedClaim,
    records: list[ParsedRecord],
    *,
    usage: UsageCollector | None = None,
) -> ConsistencyFinding:
    """Check one factual claim against every record document.

    Records are labeled to the model by `display_name` only - `document_id`s
    never enter the prompt. The model returns the display name it cited from
    plus a verbatim quote. We resolve the display name back to a record and
    locate the quote in that record's raw text. If either resolution step
    fails we downgrade the verdict to `could_not_verify` - the model failed
    to produce evidence we can stand behind, which is a verification failure
    distinct from `undisputed` (the legitimate "no record contradicts this"
    outcome).
    """

    prompt = build_crossdoc_checker_prompt(
        claim.claim_text,
        [(r.display_name, r.raw_text) for r in records],
    )
    response: CrossDocCheckerResponse = await llm.call_llm_async(
        prompt,
        CrossDocCheckerResponse,
        usage=usage,
    )

    checked_documents = [r.document_id for r in records]

    if response.verdict in ("supported", "contradicted"):
        evidence_span = _resolve_evidence_span(
            records, response.evidence_doc_name, response.evidence_quote
        )
        if evidence_span is None:
            logger.warning(
                "crossdoc_checker: downgrading %s -> could_not_verify "
                "(doc_name=%r, quote=%r)",
                response.verdict,
                response.evidence_doc_name,
                response.evidence_quote,
            )
            return ConsistencyFinding(
                claim=claim,
                verdict="could_not_verify",
                confidence=response.confidence,
                reasoning=f"{_DOWNGRADE_REASON} Original reasoning: {response.reasoning}",
                evidence_quote=None,
                evidence_span=None,
                checked_documents=checked_documents,
            )
        return ConsistencyFinding(
            claim=claim,
            verdict=response.verdict,
            confidence=response.confidence,
            reasoning=response.reasoning,
            evidence_quote=response.evidence_quote,
            evidence_span=evidence_span,
            checked_documents=checked_documents,
        )

    return ConsistencyFinding(
        claim=claim,
        verdict=response.verdict,
        confidence=response.confidence,
        reasoning=response.reasoning,
        evidence_quote=None,
        evidence_span=None,
        checked_documents=checked_documents,
    )
