import re

from pydantic import BaseModel, ConfigDict, Field

import llm
from agents.prompts.memo_writer import (
    _format_finding,
    build_memo_writer_prompt,
)
from schemas import (
    CitationFinding,
    ConsistencyFinding,
    FindingRef,
    JudicialMemo,
    PartialFailure,
)
from usage import UsageCollector


# Words that imply a verdict stronger than "could_not_verify" or
# "unsupported". The memo is allowed to use a forbidden word only when at
# least one input finding actually carries the corresponding verdict.
# "contradicted" is the one verdict that maps to a finding outcome; the rest
# are charged language that the clerk has no business reaching for.
STRONGER_VERDICT_WORDS: tuple[str, ...] = (
    "fabricated",
    "false",
    "misleading",
    "deceptive",
    "lying",
    "contradicted",
)

OUTCOME_RECOMMENDATION_PHRASES: tuple[str, ...] = (
    "should be granted",
    "should be denied",
    "motion fails",
    "motion succeeds",
)

FindingPair = tuple[FindingRef, CitationFinding | ConsistencyFinding]


class MemoVerdictPromotionError(Exception):
    """The memo's prose strengthened a verdict beyond what the findings support,
    or recommended an outcome. Either is a sycophancy failure - we refuse to
    return the memo. The pipeline converts this to a stub memo with a
    `partial_failure_note`.
    """


class MemoResponse(BaseModel):
    """Structured-output shape for the memo writer."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        description="One paragraph in a clerk's voice, no double newlines."
    )


def _summary_for(finding: CitationFinding | ConsistencyFinding) -> str:
    if isinstance(finding, CitationFinding):
        return f"citation {finding.citation.cite!r} for proposition {finding.citation.proposition!r}"
    return f"factual claim {finding.claim.claim_text!r}"


def _findings_block(pairs: list[FindingPair]) -> str:
    if not pairs:
        return "(none)"
    lines: list[str] = []
    for i, (ref, finding) in enumerate(pairs):
        lines.append(
            _format_finding(
                index=i,
                ref_label=ref.finding_type,
                verdict=finding.verdict,
                confidence=finding.confidence,
                summary=_summary_for(finding),
                reasoning=finding.reasoning,
            )
        )
    return "\n".join(lines)


def _failures_block(failures: list[PartialFailure]) -> str:
    if not failures:
        return "(none)"
    return "\n".join(f"- {f.agent}: {f.error}" for f in failures)


def _failure_note(failures: list[PartialFailure]) -> str | None:
    if not failures:
        return None
    names = ", ".join(f.agent for f in failures)
    return (
        f"Partial failures on this run: {names}. The picture is incomplete."
    )


def _word_in_text(word: str, text: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE) is not None


def _check_no_outcome_recommendation(text: str) -> None:
    lowered = text.lower()
    for phrase in OUTCOME_RECOMMENDATION_PHRASES:
        if phrase in lowered:
            raise MemoVerdictPromotionError(
                f"memo recommended an outcome (phrase {phrase!r})"
            )


def _check_no_verdict_promotion(
    text: str, pairs: list[FindingPair]
) -> None:
    verdicts = {finding.verdict for _, finding in pairs}
    has_contradicted = "contradicted" in verdicts
    # Anything that isn't `supported` or `could_not_verify` justifies the
    # softer charged words. `unsupported` is "source doesn't say what the
    # brief claims" - close enough to "misleading" that we don't fight it.
    # `could_not_verify` and `supported` do not justify any of them.
    has_strong_finding = any(
        v in {"contradicted", "unsupported"} for v in verdicts
    )

    for word in STRONGER_VERDICT_WORDS:
        if not _word_in_text(word, text):
            continue
        if word == "contradicted":
            if has_contradicted:
                continue
            raise MemoVerdictPromotionError(
                "memo used 'contradicted' but no finding has verdict='contradicted'"
            )
        if has_strong_finding:
            continue
        raise MemoVerdictPromotionError(
            f"memo used charged word {word!r} over findings that don't justify it"
        )


async def write_memo(
    top_findings: list[FindingPair],
    partial_failures: list[PartialFailure],
    *,
    usage: UsageCollector | None = None,
) -> JudicialMemo:
    """Draft the one-paragraph judicial memo from pre-ranked top findings.

    `top_findings` is a list of `(FindingRef, finding)` pairs. The caller
    (the orchestrator) supplies both because the memo writer needs the
    finding objects to talk about them but the returned `JudicialMemo`
    references findings by index, not by value. Keeping both together at
    the boundary saves the caller a parallel-list bookkeeping problem.
    """

    prompt = build_memo_writer_prompt(
        findings_block=_findings_block(top_findings),
        failures_block=_failures_block(partial_failures),
    )

    response: MemoResponse = await llm.call_llm_async(
        prompt,
        MemoResponse,
        usage=usage,
    )
    text = response.text.strip()

    _check_no_outcome_recommendation(text)
    _check_no_verdict_promotion(text, top_findings)

    return JudicialMemo(
        text=text,
        top_findings=[ref for ref, _ in top_findings],
        partial_failure_note=_failure_note(partial_failures),
    )
