"""Verbose eval report shapes.

The runner emits one of these per invocation when `--report-out` is set. The
slash-command at `.claude/commands/eval.md` reads the JSON and produces a
markdown summary. The Python code is the source of truth for metrics; the
slash-command never recomputes anything.

Schema version "2" (since R5): added `true_negative` to the classification
enum, `tn_rate` and `true_negatives` to the aggregate, and the matched_label
union now includes `ExpectedSupported` / `ExpectedUndisputed`. Old `v1`
reports are not forward-compatible; the slash-command checks
schema_version and stops if it doesn't match.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evals.labels import ExpectedFinding, ExpectedSupported, ExpectedUndisputed
from schemas import PartialFailure, Report


Classification = Literal[
    "true_positive",
    "true_negative",
    "false_positive",
    "hallucination",
]


MatchedLabel = ExpectedFinding | ExpectedSupported | ExpectedUndisputed


class FindingSummary(BaseModel):
    """The fields of an actual finding worth surfacing in the report."""

    model_config = ConfigDict(extra="forbid")

    cite_or_claim_text: str
    verdict: str
    confidence: float
    evidence_doc: str | None


class FindingMatch(BaseModel):
    """One actual finding from the pipeline plus how it was classified."""

    model_config = ConfigDict(extra="forbid")

    finding_type: Literal["citation", "consistency"]
    finding_index: int
    classification: Classification
    matched_label: MatchedLabel | None
    finding_summary: FindingSummary


class ExpectedMiss(BaseModel):
    """A label the pipeline didn't satisfy.

    Covers all three expectation types: a missed `ExpectedFinding` is a false
    negative on issue-detection (the pipeline failed to flag a real flaw); a
    missed `ExpectedSupported` / `ExpectedUndisputed` is a true-negative
    miss (the pipeline failed to confirm a consistent or undisputed claim).
    """

    model_config = ConfigDict(extra="forbid")

    label: MatchedLabel
    miss_type: Literal["false_negative", "missed_true_negative"]
    note: str


class EvalCaseReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    case_path: str
    metrics: dict
    matches: list[FindingMatch]
    misses: list[ExpectedMiss]
    pipeline_report: Report
    partial_failures: list[PartialFailure]


class EvalAggregate(BaseModel):
    """Aggregate metrics across cases. Mirrors the Metrics dataclass + cases_run."""

    model_config = ConfigDict(extra="forbid")

    precision: float
    recall: float
    hallucination_rate: float
    tn_rate: float
    total_findings: int
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    hallucinations: int
    cost_usd: float
    latency_ms: int
    cases_run: int


class Regression(BaseModel):
    model_config = ConfigDict(extra="forbid")

    breached: bool
    reasons: list[str] = Field(default_factory=list)


class EvalRunReport(BaseModel):
    """The full verbose report - one per `python -m evals.run` invocation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2"] = "2"
    started_at: str
    finished_at: str
    runtime_seconds: float
    cases: list[EvalCaseReport]
    aggregate: EvalAggregate
    regression: Regression
    git_sha: str | None
