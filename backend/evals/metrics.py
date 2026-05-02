"""Eval metrics: precision, recall, hallucination rate, true-negative rate.

Each `actual` finding from a `Report` is matched against the case's labels.
The matching rules are:

- Type must match (citation vs consistency).
- The case-insensitive substring (`claim_substring` for consistency,
  `cite_substring` for citation) must appear in the corresponding text on
  the actual finding.
- For `ExpectedFinding`: the actual finding's verdict must be one of the
  `verdict` list (any-of). If `expected_evidence_doc` is set it must equal
  the actual finding's `evidence_span.document_id`. If
  `expected_lookup_status` is set and the finding is a citation, the
  actual `lookup.lookup_status` must be in that list.
- For `ExpectedSupported`: the actual finding's verdict must be `supported`.
  If `expected_evidence_doc` is set it must equal the evidence_span doc.
- For `ExpectedUndisputed`: the actual finding's verdict must be
  `undisputed`. (No evidence_span constraint - undisputed findings don't
  carry one.)

A hallucination is structural: the actual finding's `evidence_span.excerpt`
must appear verbatim in the source text of the document referenced by
`evidence_span.document_id`. Findings without an `evidence_span`
(legitimate for `undisputed` and `could_not_verify`) are not hallucinations.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemas import (
    CitationFinding,
    ConsistencyFinding,
    DocumentSet,
    Report,
)

if TYPE_CHECKING:
    from evals.report import FindingMatch
    from evals.labels import (
        ExpectedFinding,
        ExpectedSupported,
        ExpectedUndisputed,
    )


# gpt-4o pricing per 1M tokens, as of 2024-08
# https://openai.com/api/pricing/
_PRICE_INPUT_PER_M = 2.50
_PRICE_OUTPUT_PER_M = 10.00


@dataclass
class Metrics:
    precision: float
    recall: float
    hallucination_rate: float
    tn_rate: float
    fabrication_detection_rate: float
    total_findings: int
    true_positives: int
    false_positives: int
    false_negatives: int      # missed `expected_findings` only - drives recall
    missed_true_negatives: int  # missed expected_supported + undisputed - drives tn_rate
    true_negatives: int
    hallucinations: int
    fabrication_targets: int  # expected findings with "not_found" in expected_lookup_status
    fabrications_caught: int  # of those, how many a TP claimed with actual lookup_status="not_found"
    cost_usd: float
    latency_ms: int


def _finding_substring(
    finding: CitationFinding | ConsistencyFinding, finding_type: str
) -> str:
    if finding_type == "citation":
        return finding.citation.cite  # type: ignore[union-attr]
    return finding.claim.claim_text  # type: ignore[union-attr]


def _evidence_doc(finding: CitationFinding | ConsistencyFinding) -> str | None:
    return finding.evidence_span.document_id if finding.evidence_span else None


def _substring_matches(
    finding: CitationFinding | ConsistencyFinding,
    finding_type: str,
    label,
) -> bool:
    if label.finding_type != finding_type:
        return False
    substring = label.cite_substring or label.claim_substring
    if substring is None:
        return False
    return substring.lower() in _finding_substring(finding, finding_type).lower()


def _match_expected_finding(
    finding: CitationFinding | ConsistencyFinding,
    finding_type: str,
    expected: "ExpectedFinding",
) -> bool:
    if not _substring_matches(finding, finding_type, expected):
        return False
    if expected.verdict and finding.verdict not in expected.verdict:
        return False
    if (
        expected.expected_evidence_doc is not None
        and _evidence_doc(finding) != expected.expected_evidence_doc
    ):
        return False
    if (
        expected.expected_lookup_status
        and isinstance(finding, CitationFinding)
        and finding.lookup.lookup_status not in expected.expected_lookup_status
    ):
        return False
    return True


def _match_expected_supported(
    finding: CitationFinding | ConsistencyFinding,
    finding_type: str,
    expected: "ExpectedSupported",
) -> bool:
    if not _substring_matches(finding, finding_type, expected):
        return False
    if finding.verdict != "supported":
        return False
    if (
        expected.expected_evidence_doc is not None
        and _evidence_doc(finding) != expected.expected_evidence_doc
    ):
        return False
    return True


def _match_expected_undisputed(
    finding: CitationFinding | ConsistencyFinding,
    finding_type: str,
    expected: "ExpectedUndisputed",
) -> bool:
    if not _substring_matches(finding, finding_type, expected):
        return False
    return finding.verdict == "undisputed"


def _is_hallucination(
    finding: CitationFinding | ConsistencyFinding, doc_set: DocumentSet
) -> bool:
    span = finding.evidence_span
    if span is None:
        return False
    if not doc_set.has_id(span.document_id):
        return True
    source_text = doc_set.by_id(span.document_id).text
    return span.excerpt not in source_text


def _estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return (
        prompt_tokens * _PRICE_INPUT_PER_M / 1_000_000
        + completion_tokens * _PRICE_OUTPUT_PER_M / 1_000_000
    )


def _classify_one(
    finding: CitationFinding | ConsistencyFinding,
    finding_type: str,
    expected_findings: "list[ExpectedFinding]",
    expected_supported: "list[ExpectedSupported]",
    expected_undisputed: "list[ExpectedUndisputed]",
    matched_ef: set[int],
    matched_es: set[int],
    matched_eu: set[int],
    doc_set: DocumentSet,
    tp_lookup_by_ef: dict[int, str] | None = None,
) -> tuple[
    str,
    "ExpectedFinding | ExpectedSupported | ExpectedUndisputed | None",
]:
    """Resolve one actual finding into (classification, matched_label).

    Mutates the `matched_*` sets to claim each label at most once. A single
    actual finding can claim a label slot even when its verdict does not
    qualify (e.g. a contradicted finding for a claim labeled
    `expected_supported`); that's an over-flag, scored as `false_positive`,
    and we still consume the label slot so the same mistake doesn't also
    inflate the false-negative count for that label.

    Order of precedence:
      1. Full ExpectedFinding match (substring + verdict + evidence_doc)
         -> true_positive.
      2. Full ExpectedSupported match (substring + verdict=`supported` +
         evidence_doc) -> true_negative.
      3. Full ExpectedUndisputed match (substring + verdict=`undisputed`)
         -> true_negative.
      4. Substring-only match against any label slot -> false_positive
         (the pipeline emitted the right *finding* but the wrong *verdict*
         for that label). Consumes the label slot.
      5. Structural hallucination (excerpt not in doc) -> hallucination.
      6. Otherwise -> false_positive (uncategorized flagged finding).
    """

    for i, ef in enumerate(expected_findings):
        if i in matched_ef:
            continue
        if _match_expected_finding(finding, finding_type, ef):
            matched_ef.add(i)
            if tp_lookup_by_ef is not None and isinstance(finding, CitationFinding):
                tp_lookup_by_ef[i] = finding.lookup.lookup_status
            return "true_positive", ef

    for i, es in enumerate(expected_supported):
        if i in matched_es:
            continue
        if _match_expected_supported(finding, finding_type, es):
            matched_es.add(i)
            return "true_negative", es

    for i, eu in enumerate(expected_undisputed):
        if i in matched_eu:
            continue
        if _match_expected_undisputed(finding, finding_type, eu):
            matched_eu.add(i)
            return "true_negative", eu

    # No full label match. Try a substring-only match so a verdict mismatch
    # is scored exactly once as an over-flag, not also as a missed label.
    for i, ef in enumerate(expected_findings):
        if i in matched_ef:
            continue
        if _substring_matches(finding, finding_type, ef):
            matched_ef.add(i)
            return "false_positive", ef
    for i, es in enumerate(expected_supported):
        if i in matched_es:
            continue
        if _substring_matches(finding, finding_type, es):
            matched_es.add(i)
            return "false_positive", es
    for i, eu in enumerate(expected_undisputed):
        if i in matched_eu:
            continue
        if _substring_matches(finding, finding_type, eu):
            matched_eu.add(i)
            return "false_positive", eu

    if _is_hallucination(finding, doc_set):
        return "hallucination", None

    return "false_positive", None


def compute(
    actual_report: Report,
    expected_findings: "list[ExpectedFinding]",
    expected_supported: "list[ExpectedSupported]",
    expected_undisputed: "list[ExpectedUndisputed]",
    doc_set: DocumentSet,
    latency_ms: int = 0,
) -> Metrics:
    """Compute precision/recall/hallucination_rate/tn_rate against the labels.

    Precision and recall are issue-detection metrics: TP is a flagged finding
    that matches a labeled flaw, FP is a flagged finding that doesn't match a
    flaw label, FN is a flaw label with no matching flagged finding.
    True-negative rate measures how reliably the pipeline says "this is fine"
    for claims that should come back consistent or undisputed.
    """

    actuals: list[tuple[str, CitationFinding | ConsistencyFinding]] = [
        ("citation", c) for c in actual_report.citations
    ] + [("consistency", c) for c in actual_report.consistency]

    matched_ef: set[int] = set()
    matched_es: set[int] = set()
    matched_eu: set[int] = set()
    tp_lookup_by_ef: dict[int, str] = {}

    true_positives = 0
    false_positives = 0
    true_negatives = 0
    hallucinations = 0

    for ftype, finding in actuals:
        cls, _ = _classify_one(
            finding,
            ftype,
            expected_findings,
            expected_supported,
            expected_undisputed,
            matched_ef,
            matched_es,
            matched_eu,
            doc_set,
            tp_lookup_by_ef=tp_lookup_by_ef,
        )
        if cls == "true_positive":
            true_positives += 1
        elif cls == "true_negative":
            true_negatives += 1
        elif cls == "hallucination":
            hallucinations += 1
            false_positives += 1
        else:  # "false_positive" - substring-mismatch over-flag, or unlabeled
            false_positives += 1

    # A slot is "missed" if no actual finding consumed it - whether by a TP
    # (or TN), or by a substring-only FP claim. An over-flag (substring
    # match, wrong verdict) is one mistake, not two: the FP counter captures
    # it; we don't also bump the missed-target count.
    missed_findings = len(expected_findings) - len(matched_ef)
    missed_true_negatives = (
        len(expected_supported) - len(matched_es)
        + len(expected_undisputed) - len(matched_eu)
    )

    total_findings = len(actuals)

    if true_positives + false_positives == 0:
        # No findings produced - precision is undefined; report 1.0 so a
        # silent pipeline doesn't get penalized on precision while still
        # tanking recall.
        precision = 1.0
    else:
        precision = true_positives / (true_positives + false_positives)

    expected_flaws = len(expected_findings)
    recall = true_positives / expected_flaws if expected_flaws else 1.0

    hallucination_rate = (
        hallucinations / total_findings if total_findings else 0.0
    )

    tn_denominator = true_negatives + missed_true_negatives
    tn_rate = true_negatives / tn_denominator if tn_denominator else 1.0

    fabrication_indices = [
        i for i, ef in enumerate(expected_findings)
        if "not_found" in ef.expected_lookup_status
    ]
    fabrication_targets = len(fabrication_indices)
    fabrications_caught = sum(
        1 for i in fabrication_indices if tp_lookup_by_ef.get(i) == "not_found"
    )
    fabrication_detection_rate = (
        fabrications_caught / fabrication_targets if fabrication_targets else 1.0
    )

    cost = _estimate_cost(
        actual_report.meta.token_usage.prompt,
        actual_report.meta.token_usage.completion,
    )

    return Metrics(
        precision=precision,
        recall=recall,
        hallucination_rate=hallucination_rate,
        tn_rate=tn_rate,
        fabrication_detection_rate=fabrication_detection_rate,
        total_findings=total_findings,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=missed_findings,
        missed_true_negatives=missed_true_negatives,
        true_negatives=true_negatives,
        hallucinations=hallucinations,
        fabrication_targets=fabrication_targets,
        fabrications_caught=fabrications_caught,
        cost_usd=cost,
        latency_ms=latency_ms or actual_report.meta.elapsed_ms,
    )


def classify_findings(
    actual_report: Report,
    expected_findings: "list[ExpectedFinding]",
    expected_supported: "list[ExpectedSupported]",
    expected_undisputed: "list[ExpectedUndisputed]",
    doc_set: DocumentSet,
) -> "list[FindingMatch]":
    """Per-finding classification (TP / TN / FP / hallucination / uncategorized).

    Mirrors `compute()` exactly so aggregate counts stay consistent.
    """

    from evals.report import FindingMatch, FindingSummary

    actuals: list[
        tuple[str, int, CitationFinding | ConsistencyFinding]
    ] = []
    for i, c in enumerate(actual_report.citations):
        actuals.append(("citation", i, c))
    for i, c in enumerate(actual_report.consistency):
        actuals.append(("consistency", i, c))

    matched_ef: set[int] = set()
    matched_es: set[int] = set()
    matched_eu: set[int] = set()
    matches: list[FindingMatch] = []

    for ftype, fidx, finding in actuals:
        cls, label = _classify_one(
            finding,
            ftype,
            expected_findings,
            expected_supported,
            expected_undisputed,
            matched_ef,
            matched_es,
            matched_eu,
            doc_set,
        )
        # `cls` is "false_positive" semantically when uncategorized; the
        # label only differs in name. The caller wants per-finding bucket
        # names that match the public Classification enum.
        classification = "false_positive" if cls == "uncategorized" else cls

        if ftype == "citation":
            cite_or_claim = finding.citation.cite  # type: ignore[union-attr]
        else:
            cite_or_claim = finding.claim.claim_text  # type: ignore[union-attr]

        matches.append(
            FindingMatch(
                finding_type=ftype,  # type: ignore[arg-type]
                finding_index=fidx,
                classification=classification,  # type: ignore[arg-type]
                matched_label=label,
                finding_summary=FindingSummary(
                    cite_or_claim_text=cite_or_claim,
                    verdict=finding.verdict,
                    confidence=finding.confidence,
                    evidence_doc=_evidence_doc(finding),
                ),
            )
        )

    return matches
