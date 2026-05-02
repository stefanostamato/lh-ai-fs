from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


Verdict = Literal[
    "supported",
    "contradicted",
    "unsupported",
    "undisputed",
    "could_not_verify",
]
DocumentId = str


class DocumentRole(str, Enum):
    BRIEF = "brief"
    RECORD = "record"


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: DocumentId
    display_name: str
    role: DocumentRole
    text: str


class TextSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: DocumentId
    start: int
    end: int
    excerpt: str


class ParagraphSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: DocumentId
    paragraph_index: int
    span: TextSpan


class DocumentSet(BaseModel):
    """The pipeline's input envelope. Exactly one BRIEF, N RECORDs, unique ids."""

    model_config = ConfigDict(extra="forbid")

    documents: list[DocumentInput]

    @model_validator(mode="after")
    def _validate(self) -> "DocumentSet":
        briefs = [d for d in self.documents if d.role == DocumentRole.BRIEF]
        if len(briefs) != 1:
            raise ValueError(
                f"DocumentSet must contain exactly one BRIEF; found {len(briefs)}"
            )
        ids = [d.document_id for d in self.documents]
        if len(set(ids)) != len(ids):
            raise ValueError(f"document_ids must be unique; got {ids}")
        return self

    def brief(self) -> DocumentInput:
        return next(d for d in self.documents if d.role == DocumentRole.BRIEF)

    def records(self) -> list[DocumentInput]:
        return [d for d in self.documents if d.role == DocumentRole.RECORD]

    def by_id(self, document_id: DocumentId) -> DocumentInput:
        for d in self.documents:
            if d.document_id == document_id:
                return d
        raise KeyError(document_id)

    def has_id(self, document_id: DocumentId) -> bool:
        return any(d.document_id == document_id for d in self.documents)


class ParsedBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: DocumentId
    raw_text: str
    paragraphs: list[ParagraphSpan]


class ParsedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: DocumentId
    display_name: str
    raw_text: str
    paragraphs: list[ParagraphSpan]


class ExtractedCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cite: str
    proposition: str
    quoted_language: str | None
    claim_span: TextSpan


class ExtractedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_text: str
    claim_span: TextSpan


class CaseLookupResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool
    canonical_cite: str | None
    holding_text: str | None
    quoted_text_match: bool | None
    source_url: str | None
    lookup_status: Literal["found", "not_found", "ambiguous", "lookup_failed"]
    notes: str | None


class CitationFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation: ExtractedCitation
    verdict: Verdict
    confidence: float
    reasoning: str
    evidence_quote: str | None
    evidence_span: TextSpan | None
    lookup: CaseLookupResult


class ConsistencyFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: ExtractedClaim
    verdict: Verdict
    confidence: float
    reasoning: str
    evidence_quote: str | None
    evidence_span: TextSpan | None
    checked_documents: list[DocumentId]


class FindingRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_type: Literal["citation", "consistency"]
    finding_index: int


class JudicialMemo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    top_findings: list[FindingRef]
    partial_failure_note: str | None


class PartialFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str
    error: str


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: int
    completion: int


class DocumentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: DocumentId
    display_name: str
    role: DocumentRole


class ReportMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    elapsed_ms: int
    token_usage: TokenUsage
    partial_failures: list[PartialFailure]
    documents: list[DocumentSummary]


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consistency: list[ConsistencyFinding]
    citations: list[CitationFinding]
    memo: JudicialMemo
    meta: ReportMeta
