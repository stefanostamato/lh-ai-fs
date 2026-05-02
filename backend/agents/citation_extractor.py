import logging

from pydantic import BaseModel, ConfigDict, Field

import llm
from agents.prompts.citation_extractor import build_citation_extractor_prompt
from schemas import ExtractedCitation, ParsedBrief, TextSpan
from usage import UsageCollector


logger = logging.getLogger(__name__)


class CitationDraft(BaseModel):
    """Pre-validation shape the LLM emits. Internal to this agent."""

    model_config = ConfigDict(extra="forbid")

    cite: str = Field(description="The citation text as written in the brief, verbatim.")
    proposition: str = Field(
        description="The proposition the brief attributes to this authority, in the brief's framing."
    )
    quoted_language: str | None = Field(
        default=None,
        description="Verbatim quoted text from the case the brief reproduces, or null.",
    )
    span_start: int = Field(description="Inclusive char offset of the cite in brief_text.")
    span_end: int = Field(description="Exclusive char offset of the cite in brief_text.")


class CitationExtractionResponse(BaseModel):
    """Structured-output wrapper for the citation extractor."""

    model_config = ConfigDict(extra="forbid")

    citations: list[CitationDraft]


def _resolve_span(brief: ParsedBrief, draft: CitationDraft) -> TextSpan | None:
    raw = brief.raw_text
    start, end = draft.span_start, draft.span_end
    if 0 <= start < end <= len(raw) and raw[start:end] == draft.cite:
        return TextSpan(
            document_id=brief.document_id,
            start=start,
            end=end,
            excerpt=draft.cite,
        )
    found = raw.find(draft.cite)
    if found < 0:
        return None
    return TextSpan(
        document_id=brief.document_id,
        start=found,
        end=found + len(draft.cite),
        excerpt=draft.cite,
    )


async def extract_citations(
    brief: ParsedBrief,
    *,
    usage: UsageCollector | None = None,
) -> list[ExtractedCitation]:
    prompt = build_citation_extractor_prompt(brief.raw_text)
    response: CitationExtractionResponse = await llm.call_llm_async(
        prompt,
        CitationExtractionResponse,
        usage=usage,
    )

    citations: list[ExtractedCitation] = []
    for draft in response.citations:
        span = _resolve_span(brief, draft)
        if span is None:
            logger.warning(
                "citation_extractor: dropping cite %r - not found in brief %s",
                draft.cite,
                brief.document_id,
            )
            continue
        citations.append(
            ExtractedCitation(
                cite=draft.cite,
                proposition=draft.proposition,
                quoted_language=draft.quoted_language,
                claim_span=span,
            )
        )
    return citations
