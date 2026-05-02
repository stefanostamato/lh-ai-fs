"""Case-label shapes used by the eval runner, metrics, and report.

These live in their own module so `python -m evals.run` doesn't double-load
them: when the runner is invoked that way, `run.py` runs as `__main__` and
also gets re-imported as `evals.run`, which would otherwise produce two
copies of these classes and break Pydantic's union validators on
`FindingMatch.matched_label`.

Schema v2 vocabulary (since R5):

- `expected_findings`     - the pipeline should flag this as a real flaw
                            (verdict in {contradicted, unsupported}).
- `expected_supported`    - the pipeline should emit this finding with
                            verdict=`supported`. Catching consistent claims
                            is a true negative for issue-detection and a
                            real product output ("the record confirms this").
- `expected_undisputed`   - the pipeline should emit this finding with
                            verdict=`undisputed`. The record either is silent
                            on the topic or covers it without contradiction.
                            Also a true negative for issue-detection.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas import Verdict


class CaseDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    display_name: str
    role: Literal["brief", "record"]
    text_path: str


class ExpectedFinding(BaseModel):
    """A flaw the pipeline should catch in the case."""

    model_config = ConfigDict(extra="forbid")

    finding_type: Literal["citation", "consistency"]
    cite_substring: str | None = None
    claim_substring: str | None = None
    verdict: list[Verdict] = Field(
        default_factory=list,
        description="Any of these verdicts on the actual finding satisfies the label.",
    )
    expected_evidence_doc: str | None = None
    note: str | None = None


class ExpectedSupported(BaseModel):
    """A claim or cite the pipeline should emit with verdict=`supported`.

    Used for true-negative scoring: catching consistency between the brief and
    the record is a real product output, not noise. Substring matches an
    actual finding's claim_text or cite. The actual finding's verdict must be
    `supported` for the match to count as a true negative; any other verdict
    is over-flagging and counts as a false positive.
    """

    model_config = ConfigDict(extra="forbid")

    finding_type: Literal["citation", "consistency"]
    cite_substring: str | None = None
    claim_substring: str | None = None
    expected_evidence_doc: str | None = None
    note: str | None = None


class ExpectedUndisputed(BaseModel):
    """A claim or cite the pipeline should emit with verdict=`undisputed`.

    Used for true-negative scoring: brief-internal facts that the record does
    not address are a real product output (clerk's "the record does not
    contradict this"), not a precision penalty. Substring matches an actual
    finding's claim_text. The actual finding's verdict must be `undisputed`;
    any other verdict counts as a false positive.
    """

    model_config = ConfigDict(extra="forbid")

    finding_type: Literal["citation", "consistency"]
    cite_substring: str | None = None
    claim_substring: str | None = None
    note: str | None = None


class CaseLabels(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    documents: list[CaseDoc]
    expected_findings: list[ExpectedFinding] = Field(default_factory=list)
    expected_supported: list[ExpectedSupported] = Field(default_factory=list)
    expected_undisputed: list[ExpectedUndisputed] = Field(default_factory=list)
