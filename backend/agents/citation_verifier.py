from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

import llm
from agents.prompts.citation_verifier import build_citation_verifier_prompt
from agents.tools.case_lookup import CaseLawLookup
from llm import LlmError
from schemas import (
    CaseLookupResult,
    CitationFinding,
    ExtractedCitation,
)
from usage import UsageCollector


JudgmentVerdict = Literal["supported", "contradicted", "unsupported"]


NOT_FOUND_CONFIDENCE: float = 0.9
LOOKUP_FAILED_CONFIDENCE: float = 0.5
LLM_ERROR_CONFIDENCE: float = 0.5


class CitationJudgmentResponse(BaseModel):
    """Structured-output shape for the verifier's LLM judgment step.

    The verifier itself does not emit `could_not_verify` - that verdict is
    decided up-front from the lookup status, before this prompt fires. So
    the model's vocabulary here is narrower than the full `Verdict` enum.
    """

    model_config = ConfigDict(extra="forbid")

    verdict: JudgmentVerdict = Field(
        description="How the holding fits the brief's proposition."
    )
    confidence: float = Field(
        description="Calibrated confidence in [0.0, 1.0]. See prompt for buckets."
    )
    reasoning: str = Field(
        description="One or two sentences referencing the holding text."
    )
    evidence_quote: str | None = Field(
        default=None,
        description="Most relevant verbatim excerpt of the holding text, or null.",
    )


async def verify_citation(
    citation: ExtractedCitation,
    lookup: CaseLawLookup,
    *,
    usage: UsageCollector | None = None,
) -> CitationFinding:
    """Verify one extracted citation against a case-law lookup.

    Steps: (1) call the lookup. (2) If we couldn't reach the source -
    `not_found` or `lookup_failed` - short-circuit to `could_not_verify`
    without burning tokens on a judgment we have no evidence for. (3)
    Otherwise ask the LLM whether the holding supports, contradicts, or
    is silent on the brief's proposition (folding the quote-accuracy
    check into the same call when a verbatim quote was attributed).
    """

    lookup_result = await lookup.fetch(citation)

    if lookup_result.lookup_status == "not_found":
        return _could_not_verify(
            citation=citation,
            lookup_result=lookup_result,
            confidence=NOT_FOUND_CONFIDENCE,
            reasoning="Lookup tool could not locate this citation.",
        )
    if lookup_result.lookup_status == "lookup_failed":
        return _could_not_verify(
            citation=citation,
            lookup_result=lookup_result,
            confidence=LOOKUP_FAILED_CONFIDENCE,
            reasoning="Lookup tool failed; cannot reach the source to judge.",
        )
    if lookup_result.holding_text is None:
        # Defensive: any lookup status other than `found` that lacks a
        # holding is a confession we can't verify against. Don't fabricate.
        return _could_not_verify(
            citation=citation,
            lookup_result=lookup_result,
            confidence=LOOKUP_FAILED_CONFIDENCE,
            reasoning=(
                f"Lookup returned status {lookup_result.lookup_status!r} "
                "with no holding text."
            ),
        )

    prompt = build_citation_verifier_prompt(
        proposition=citation.proposition,
        holding_text=lookup_result.holding_text,
        quoted_language=citation.quoted_language,
    )

    try:
        judgment: CitationJudgmentResponse = await llm.call_llm_async(
            prompt,
            CitationJudgmentResponse,
            usage=usage,
        )
    except LlmError as exc:
        return _could_not_verify(
            citation=citation,
            lookup_result=lookup_result,
            confidence=LLM_ERROR_CONFIDENCE,
            reasoning=f"Judgment LLM call failed: {exc}",
        )

    return CitationFinding(
        citation=citation,
        verdict=judgment.verdict,
        confidence=judgment.confidence,
        reasoning=judgment.reasoning,
        evidence_quote=judgment.evidence_quote,
        evidence_span=None,
        lookup=lookup_result,
    )


def _could_not_verify(
    *,
    citation: ExtractedCitation,
    lookup_result: CaseLookupResult,
    confidence: float,
    reasoning: str,
) -> CitationFinding:
    return CitationFinding(
        citation=citation,
        verdict="could_not_verify",
        confidence=confidence,
        reasoning=reasoning,
        evidence_quote=None,
        evidence_span=None,
        lookup=lookup_result,
    )
