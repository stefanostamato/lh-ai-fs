import logging

from pydantic import BaseModel, ConfigDict, Field

import llm
from agents.prompts.claim_extractor import build_claim_extractor_prompt
from schemas import ExtractedClaim, ParsedBrief, TextSpan
from usage import UsageCollector


logger = logging.getLogger(__name__)


class ClaimDraft(BaseModel):
    """One claim as the model returned it, before span validation."""

    model_config = ConfigDict(extra="forbid")

    claim_text: str = Field(
        description="Verbatim text of the factual claim, copied from the brief."
    )
    span_start: int = Field(
        description="Character offset (inclusive) where claim_text begins in the brief."
    )
    span_end: int = Field(
        description="Character offset (exclusive) where claim_text ends in the brief."
    )


class ClaimExtractionResponse(BaseModel):
    """Pipeline-internal envelope for the LLM's structured output."""

    model_config = ConfigDict(extra="forbid")

    claims: list[ClaimDraft] = Field(
        description="Factual claims found in the brief. May be empty."
    )


def _resolve_span(
    raw_text: str, draft: ClaimDraft
) -> tuple[int, int] | None:
    """Trust the model's offsets if they match. Otherwise fall back to find.

    Returns (start, end) on success, or None if the claim text does not appear
    in the raw brief at all.
    """

    start = draft.span_start
    end = draft.span_end
    if 0 <= start < end <= len(raw_text) and raw_text[start:end] == draft.claim_text:
        return start, end

    found = raw_text.find(draft.claim_text)
    if found == -1:
        return None
    return found, found + len(draft.claim_text)


async def extract_claims(
    brief: ParsedBrief,
    *,
    usage: UsageCollector | None = None,
) -> list[ExtractedClaim]:
    """Pull factual claims out of a parsed brief.

    Each returned claim carries a `claim_span` whose excerpt is guaranteed to
    appear verbatim in `brief.raw_text`. Claims whose text cannot be located
    in the brief - even after the string-find fallback - are dropped with a
    warning rather than shipped as malformed findings.
    """

    prompt = build_claim_extractor_prompt(brief.raw_text)
    response: ClaimExtractionResponse = await llm.call_llm_async(
        prompt,
        ClaimExtractionResponse,
        usage=usage,
    )

    claims: list[ExtractedClaim] = []
    for draft in response.claims:
        resolved = _resolve_span(brief.raw_text, draft)
        if resolved is None:
            logger.warning(
                "claim_extractor: dropping claim, text not found in brief: %r",
                draft.claim_text,
            )
            continue
        start, end = resolved
        claims.append(
            ExtractedClaim(
                claim_text=draft.claim_text,
                claim_span=TextSpan(
                    document_id=brief.document_id,
                    start=start,
                    end=end,
                    excerpt=brief.raw_text[start:end],
                ),
            )
        )
    return claims
