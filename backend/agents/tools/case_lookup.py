from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

import llm
from agents.prompts.parametric_lookup import build_parametric_lookup_prompt
from llm import LlmError
from schemas import CaseLookupResult, ExtractedCitation


CONFIDENCE_THRESHOLD: float = 0.7


class ParametricLookupResponse(BaseModel):
    """The shape we ask the LLM to return for a parametric (memory-only) lookup."""

    model_config = ConfigDict(extra="forbid")

    confidence: float = Field(
        description="How sure the model is that it knows this exact citation and its holding. [0.0, 1.0]."
    )
    canonical_cite: str | None = Field(
        description="Citation in canonical form. Null if the model doesn't know it."
    )
    holding_text: str | None = Field(
        description="Concise holding text. Null if the model isn't confident."
    )
    quoted_text_match: bool | None = Field(
        description="Whether the brief's verbatim quote matches the case as the model knows it. Null if no quote was provided or the model can't tell."
    )
    notes: str | None = Field(
        description="Optional short caveat from the model."
    )


@runtime_checkable
class CaseLawLookup(Protocol):
    """The pluggable case-law lookup seam.

    Today only `ParametricLLMLookup` exists - it asks the model what it knows.
    Tomorrow this same shape covers CourtListener, Westlaw, web search.
    The verifier holds a reference to one of these and never knows which.
    """

    async def fetch(self, citation: ExtractedCitation) -> CaseLookupResult:
        ...


class ParametricLLMLookup:
    """Asks the LLM what it knows about a citation from training data alone.

    This is the lowest-trust lookup. The model has no source to check against,
    so the contract is: low confidence maps to `not_found` (we don't know it),
    LLM error maps to `lookup_failed` (the tool itself broke). Those are
    different signals to the verifier and the memo writer.
    """

    async def fetch(self, citation: ExtractedCitation) -> CaseLookupResult:
        prompt = build_parametric_lookup_prompt(
            cite=citation.cite,
            proposition=citation.proposition,
            quoted_language=citation.quoted_language,
        )
        try:
            response = await llm.call_llm_async(prompt, ParametricLookupResponse)
        except LlmError:
            return _failed_lookup()

        if not isinstance(response, ParametricLookupResponse):
            return _failed_lookup()

        confidently_known = (
            response.confidence >= CONFIDENCE_THRESHOLD
            and response.holding_text is not None
        )

        if not confidently_known:
            return CaseLookupResult(
                found=False,
                canonical_cite=None,
                holding_text=None,
                quoted_text_match=None,
                source_url=None,
                lookup_status="not_found",
                notes=response.notes,
            )

        # Citation has no quoted_language -> nothing to match against, force null.
        quoted_text_match = (
            response.quoted_text_match if citation.quoted_language else None
        )
        return CaseLookupResult(
            found=True,
            canonical_cite=response.canonical_cite,
            holding_text=response.holding_text,
            quoted_text_match=quoted_text_match,
            source_url=None,
            lookup_status="found",
            notes=response.notes,
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
