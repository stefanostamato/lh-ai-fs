import json
from pathlib import Path

import pytest

from evals.labels import (
    CaseDoc,
    CaseLabels,
    ExpectedFinding,
    ExpectedSupported,
    ExpectedUndisputed,
)
from evals.metrics import Metrics, classify_findings, compute
from evals.report import EvalRunReport
from evals.run import build_run_report, load_case
from schemas import (
    CaseLookupResult,
    CitationFinding,
    ConsistencyFinding,
    DocumentInput,
    DocumentRole,
    DocumentSet,
    DocumentSummary,
    ExtractedCitation,
    ExtractedClaim,
    JudicialMemo,
    Report,
    ReportMeta,
    TextSpan,
    TokenUsage,
)


BRIEF_TEXT = (
    "The motion claims the incident happened on March 14, 2021. It also "
    "cites Doe v. Roe, 111 F.3d 222 (9th Cir. 1999) for a proposition.\n"
)
RECORD_TEXT = (
    "The police report states the incident occurred on March 12, 2021 "
    "at the construction site.\n"
)


def _doc_set() -> DocumentSet:
    return DocumentSet(
        documents=[
            DocumentInput(
                document_id="brief",
                display_name="Motion",
                role=DocumentRole.BRIEF,
                text=BRIEF_TEXT,
            ),
            DocumentInput(
                document_id="police",
                display_name="Police Report",
                role=DocumentRole.RECORD,
                text=RECORD_TEXT,
            ),
        ]
    )


def _claim_span(doc_id: str, text: str, excerpt: str) -> TextSpan:
    start = text.index(excerpt)
    return TextSpan(
        document_id=doc_id, start=start, end=start + len(excerpt), excerpt=excerpt
    )


def _make_consistency(
    *,
    claim_text: str,
    verdict: str,
    evidence_doc: str | None = "police",
    evidence_text: str | None = "March 12, 2021",
) -> ConsistencyFinding:
    if claim_text in BRIEF_TEXT:
        claim_span = _claim_span("brief", BRIEF_TEXT, claim_text)
    else:
        # Test fixture: claim_text isn't literally in the synthetic brief.
        # Mint a plausible span so the finding is well-formed.
        claim_span = TextSpan(
            document_id="brief", start=0, end=len(claim_text), excerpt=claim_text
        )
    evidence_span = None
    if evidence_doc is not None and evidence_text is not None:
        # Allow the test to inject a hallucinated excerpt by passing text not in
        # the record - we still mint a span to exercise the finding shape.
        if evidence_text in RECORD_TEXT:
            start = RECORD_TEXT.index(evidence_text)
            evidence_span = TextSpan(
                document_id=evidence_doc,
                start=start,
                end=start + len(evidence_text),
                excerpt=evidence_text,
            )
        else:
            evidence_span = TextSpan(
                document_id=evidence_doc,
                start=0,
                end=len(evidence_text),
                excerpt=evidence_text,
            )
    return ConsistencyFinding(
        claim=ExtractedClaim(claim_text=claim_text, claim_span=claim_span),
        verdict=verdict,
        confidence=0.9,
        reasoning="...",
        evidence_quote=evidence_text,
        evidence_span=evidence_span,
        checked_documents=["police"],
    )


def _make_citation(
    *,
    cite: str,
    verdict: str,
) -> CitationFinding:
    claim_span = _claim_span("brief", BRIEF_TEXT, cite)
    return CitationFinding(
        citation=ExtractedCitation(
            cite=cite,
            proposition="some proposition",
            quoted_language=None,
            claim_span=claim_span,
        ),
        verdict=verdict,
        confidence=0.85,
        reasoning="...",
        evidence_quote=None,
        evidence_span=None,
        lookup=CaseLookupResult(
            found=False,
            canonical_cite=None,
            holding_text=None,
            quoted_text_match=None,
            source_url=None,
            lookup_status="not_found",
            notes=None,
        ),
    )


def _empty_memo() -> JudicialMemo:
    return JudicialMemo(text="memo", top_findings=[], partial_failure_note=None)


def _empty_meta() -> ReportMeta:
    return ReportMeta(
        model="gpt-4o",
        elapsed_ms=1000,
        token_usage=TokenUsage(prompt=0, completion=0),
        partial_failures=[],
        documents=[
            DocumentSummary(
                document_id="brief", display_name="Motion", role=DocumentRole.BRIEF
            ),
            DocumentSummary(
                document_id="police",
                display_name="Police Report",
                role=DocumentRole.RECORD,
            ),
        ],
    )


def _report(
    *,
    consistency: list[ConsistencyFinding] | None = None,
    citations: list[CitationFinding] | None = None,
) -> Report:
    return Report(
        consistency=consistency or [],
        citations=citations or [],
        memo=_empty_memo(),
        meta=_empty_meta(),
    )


def _compute(
    report: Report,
    *,
    expected_findings: list[ExpectedFinding] | None = None,
    expected_supported: list[ExpectedSupported] | None = None,
    expected_undisputed: list[ExpectedUndisputed] | None = None,
) -> Metrics:
    return compute(
        actual_report=report,
        expected_findings=expected_findings or [],
        expected_supported=expected_supported or [],
        expected_undisputed=expected_undisputed or [],
        doc_set=_doc_set(),
    )


def _classify(
    report: Report,
    *,
    expected_findings: list[ExpectedFinding] | None = None,
    expected_supported: list[ExpectedSupported] | None = None,
    expected_undisputed: list[ExpectedUndisputed] | None = None,
):
    return classify_findings(
        actual_report=report,
        expected_findings=expected_findings or [],
        expected_supported=expected_supported or [],
        expected_undisputed=expected_undisputed or [],
        doc_set=_doc_set(),
    )


# ---------- metrics tests ----------


def test_perfect_match_precision_recall_one():
    finding = _make_consistency(
        claim_text="March 14, 2021",
        verdict="contradicted",
        evidence_text="March 12, 2021",
    )
    expected = [
        ExpectedFinding(
            finding_type="consistency",
            claim_substring="March 14, 2021",
            verdict=["contradicted"],
            expected_evidence_doc="police",
        )
    ]

    metrics = _compute(_report(consistency=[finding]), expected_findings=expected)

    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.hallucination_rate == 0.0
    assert metrics.true_positives == 1
    assert metrics.false_positives == 0
    assert metrics.false_negatives == 0


def test_supported_match_against_expected_supported_is_true_negative():
    """A `supported` finding that matches an `expected_supported` label is a TN."""

    finding = _make_consistency(
        claim_text="Apex Staffing Solutions",
        verdict="supported",
        evidence_text="March 12, 2021",
    )
    metrics = _compute(
        _report(consistency=[finding]),
        expected_supported=[
            ExpectedSupported(
                finding_type="consistency",
                claim_substring="Apex Staffing Solutions",
                expected_evidence_doc="police",
            )
        ],
    )
    assert metrics.true_positives == 0
    assert metrics.false_positives == 0
    assert metrics.true_negatives == 1
    assert metrics.tn_rate == 1.0


def test_undisputed_match_against_expected_undisputed_is_true_negative():
    """An `undisputed` finding matching an `expected_undisputed` label is a TN."""

    finding = _make_consistency(
        claim_text="March 14, 2021",
        verdict="undisputed",
        evidence_doc=None,
        evidence_text=None,
    )
    metrics = _compute(
        _report(consistency=[finding]),
        expected_undisputed=[
            ExpectedUndisputed(
                finding_type="consistency",
                claim_substring="March 14, 2021",
            )
        ],
    )
    assert metrics.true_negatives == 1
    assert metrics.false_positives == 0
    assert metrics.tn_rate == 1.0


def test_wrong_verdict_on_expected_supported_is_false_positive_not_double_count():
    """A finding that has the right substring but wrong verdict is FP, not FP+FN."""

    finding = _make_consistency(
        claim_text="Apex Staffing Solutions",
        verdict="contradicted",  # wrong - should have been `supported`
        evidence_text="March 12, 2021",
    )
    metrics = _compute(
        _report(consistency=[finding]),
        expected_supported=[
            ExpectedSupported(
                finding_type="consistency",
                claim_substring="Apex Staffing Solutions",
            )
        ],
    )
    assert metrics.false_positives == 1
    # The label slot is consumed by the verdict-mismatch FP, so it doesn't
    # also inflate `missed_true_negatives`.
    assert metrics.missed_true_negatives == 0
    assert metrics.true_negatives == 0


def test_missed_expected_supported_increments_missed_true_negatives():
    """If the pipeline doesn't emit a finding for an expected_supported claim,
    that's a missed TN, not a false negative on issue-detection."""

    metrics = _compute(
        _report(),
        expected_supported=[
            ExpectedSupported(
                finding_type="consistency",
                claim_substring="Apex Staffing Solutions",
            )
        ],
    )
    assert metrics.true_negatives == 0
    assert metrics.missed_true_negatives == 1
    assert metrics.false_negatives == 0
    assert metrics.tn_rate == 0.0
    # No flaws expected, so recall is vacuously 1.0.
    assert metrics.recall == 1.0


def test_all_false_negatives_when_pipeline_finds_nothing():
    expected = [
        ExpectedFinding(
            finding_type="consistency",
            claim_substring="March 14, 2021",
            verdict=["contradicted"],
        )
    ]
    metrics = _compute(_report(), expected_findings=expected)
    assert metrics.true_positives == 0
    assert metrics.false_positives == 0
    assert metrics.false_negatives == 1
    assert metrics.recall == 0.0
    # Precision is undefined when there are zero findings - convention: 1.0.
    assert metrics.precision == 1.0


def test_hallucination_when_evidence_excerpt_not_in_doc():
    finding = _make_consistency(
        claim_text="March 14, 2021",
        verdict="contradicted",
        evidence_text="fake quote that isn't in the doc",
    )
    metrics = _compute(_report(consistency=[finding]))
    assert metrics.hallucinations == 1
    assert metrics.hallucination_rate == 1.0
    # Hallucinations also count as false positives.
    assert metrics.false_positives == 1


def test_no_hallucination_when_evidence_excerpt_in_doc():
    finding = _make_consistency(
        claim_text="March 14, 2021",
        verdict="contradicted",
        evidence_text="March 12, 2021",
    )
    metrics = _compute(_report(consistency=[finding]))
    assert metrics.hallucinations == 0
    assert metrics.hallucination_rate == 0.0


def test_no_evidence_span_does_not_count_as_hallucination():
    """undisputed and could_not_verify findings legitimately have no evidence_span."""

    claim = ExtractedClaim(
        claim_text="something",
        claim_span=_claim_span("brief", BRIEF_TEXT, "March 14, 2021"),
    )
    finding = ConsistencyFinding(
        claim=claim,
        verdict="undisputed",
        confidence=0.0,
        reasoning="record does not contradict",
        evidence_quote=None,
        evidence_span=None,
        checked_documents=["police"],
    )
    metrics = _compute(_report(consistency=[finding]))
    assert metrics.hallucinations == 0


def test_substring_match_is_case_insensitive():
    finding = _make_consistency(
        claim_text="March 14, 2021", verdict="contradicted"
    )
    expected = [
        ExpectedFinding(
            finding_type="consistency",
            claim_substring="march 14, 2021",  # lowercase
            verdict=["contradicted"],
        )
    ]
    metrics = _compute(_report(consistency=[finding]), expected_findings=expected)
    assert metrics.true_positives == 1


def test_type_must_match():
    """A consistency finding cannot satisfy a citation expected_finding."""

    finding = _make_consistency(
        claim_text="March 14, 2021", verdict="contradicted"
    )
    expected = [
        ExpectedFinding(
            finding_type="citation",
            claim_substring="March 14, 2021",
            verdict=["contradicted"],
        )
    ]
    metrics = _compute(_report(consistency=[finding]), expected_findings=expected)
    assert metrics.true_positives == 0
    assert metrics.false_positives == 1
    assert metrics.false_negatives == 1


def test_verdict_any_of_match():
    """expected_finding.verdict is a list - any of them match."""

    finding = _make_consistency(
        claim_text="March 14, 2021", verdict="unsupported"
    )
    expected = [
        ExpectedFinding(
            finding_type="consistency",
            claim_substring="March 14, 2021",
            verdict=["contradicted", "unsupported"],
        )
    ]
    metrics = _compute(_report(consistency=[finding]), expected_findings=expected)
    assert metrics.true_positives == 1


def test_verdict_mismatch_consumes_label_once():
    """A finding with right substring + wrong verdict is FP, but the label slot
    is consumed so we don't also inflate FN for that same label."""

    finding = _make_consistency(
        claim_text="March 14, 2021", verdict="supported"
    )
    expected = [
        ExpectedFinding(
            finding_type="consistency",
            claim_substring="March 14, 2021",
            verdict=["contradicted"],
        )
    ]
    metrics = _compute(_report(consistency=[finding]), expected_findings=expected)
    assert metrics.true_positives == 0
    assert metrics.false_positives == 1
    # Verdict-mismatch consumes the label slot, so FN doesn't double-count.
    assert metrics.false_negatives == 0


def test_expected_evidence_doc_must_match_evidence_span():
    finding = _make_consistency(
        claim_text="March 14, 2021",
        verdict="contradicted",
        evidence_doc="police",
        evidence_text="March 12, 2021",
    )
    expected_wrong_doc = [
        ExpectedFinding(
            finding_type="consistency",
            claim_substring="March 14, 2021",
            verdict=["contradicted"],
            expected_evidence_doc="medical",
        )
    ]
    metrics = _compute(
        _report(consistency=[finding]),
        expected_findings=expected_wrong_doc,
    )
    # Substring matches but evidence_doc doesn't, so the finding-level match
    # fails and the substring-only fallback fires - that consumes the label
    # slot as an over-flag.
    assert metrics.true_positives == 0
    assert metrics.false_positives == 1


def test_citation_matching_uses_cite_substring():
    finding = _make_citation(
        cite="Doe v. Roe, 111 F.3d 222 (9th Cir. 1999)",
        verdict="unsupported",
    )
    expected = [
        ExpectedFinding(
            finding_type="citation",
            cite_substring="Doe v. Roe",
            verdict=["unsupported", "could_not_verify"],
        )
    ]
    metrics = _compute(_report(citations=[finding]), expected_findings=expected)
    assert metrics.true_positives == 1


# ---------- case loader tests ----------


def _write_case_json(tmp_path: Path, *, brief_text: str, record_text: str) -> Path:
    brief_file = tmp_path / "brief.txt"
    record_file = tmp_path / "record.txt"
    brief_file.write_text(brief_text)
    record_file.write_text(record_text)

    case = {
        "case_id": "synthetic_case",
        "documents": [
            {
                "document_id": "msj",
                "display_name": "Motion",
                "role": "brief",
                "text_path": str(brief_file),
            },
            {
                "document_id": "police",
                "display_name": "Police Report",
                "role": "record",
                "text_path": str(record_file),
            },
        ],
        "expected_findings": [
            {
                "finding_type": "consistency",
                "claim_substring": "March 14",
                "verdict": ["contradicted"],
                "expected_evidence_doc": "police",
            }
        ],
        "expected_supported": [],
        "expected_undisputed": [],
    }
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(case))
    return case_path


def test_load_case_builds_well_formed_document_set(tmp_path):
    case_path = _write_case_json(
        tmp_path, brief_text="brief body", record_text="record body"
    )
    labels, doc_set = load_case(case_path)

    assert isinstance(labels, CaseLabels)
    assert labels.case_id == "synthetic_case"
    assert len(labels.expected_findings) == 1
    assert isinstance(doc_set, DocumentSet)
    assert doc_set.brief().document_id == "msj"
    assert [r.document_id for r in doc_set.records()] == ["police"]
    assert doc_set.brief().text == "brief body"
    assert doc_set.by_id("police").text == "record body"


def test_load_case_missing_text_path_raises_clear_error(tmp_path):
    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "case_id": "broken",
                "documents": [
                    {
                        "document_id": "msj",
                        "display_name": "Motion",
                        "role": "brief",
                        "text_path": str(tmp_path / "does_not_exist.txt"),
                    }
                ],
                "expected_findings": [],
                "expected_supported": [],
                "expected_undisputed": [],
            }
        )
    )

    with pytest.raises(FileNotFoundError) as excinfo:
        load_case(case_path)
    assert "does_not_exist.txt" in str(excinfo.value)


def test_load_case_text_path_resolves_relative_to_repo_root(tmp_path, monkeypatch):
    """`text_path` strings like `backend/documents/foo.txt` should resolve
    relative to the repo root, not the case JSON's directory."""

    repo_root = tmp_path / "repo"
    docs_dir = repo_root / "backend" / "documents"
    docs_dir.mkdir(parents=True)
    (docs_dir / "foo.txt").write_text("brief content")
    (docs_dir / "bar.txt").write_text("record content")
    cases_dir = repo_root / "backend" / "evals" / "cases"
    cases_dir.mkdir(parents=True)
    case_path = cases_dir / "fake.json"
    case_path.write_text(
        json.dumps(
            {
                "case_id": "fake",
                "documents": [
                    {
                        "document_id": "msj",
                        "display_name": "Motion",
                        "role": "brief",
                        "text_path": "backend/documents/foo.txt",
                    },
                    {
                        "document_id": "police",
                        "display_name": "Police",
                        "role": "record",
                        "text_path": "backend/documents/bar.txt",
                    },
                ],
                "expected_findings": [],
                "expected_supported": [],
                "expected_undisputed": [],
            }
        )
    )

    monkeypatch.chdir(repo_root)
    labels, doc_set = load_case(case_path)
    assert doc_set.brief().text == "brief content"
    assert doc_set.by_id("police").text == "record content"


def test_load_real_rivera_case_file():
    """Smoke test - the committed `rivera_v_harmon.json` parses cleanly and
    every label substring is present in the brief at the declared text_path."""

    repo_root = Path(__file__).resolve().parents[2]
    case_path = repo_root / "backend" / "evals" / "cases" / "rivera_v_harmon.json"
    labels, doc_set = load_case(case_path)

    # one BRIEF, three RECORDs
    assert doc_set.brief().document_id == "msj"
    assert {r.document_id for r in doc_set.records()} == {"police", "medical", "witness"}
    assert labels.expected_findings, "expected_findings must not be empty"
    assert labels.expected_supported, "expected_supported must not be empty"
    assert labels.expected_undisputed, "expected_undisputed must not be empty"

    brief_text = doc_set.brief().text
    for ef in labels.expected_findings:
        substring = ef.cite_substring or ef.claim_substring
        assert substring is not None, f"expected_finding missing substring: {ef}"
        assert substring.lower() in brief_text.lower(), (
            f"label substring {substring!r} not found in brief text"
        )
        if ef.expected_evidence_doc is not None:
            assert doc_set.has_id(ef.expected_evidence_doc), (
                f"expected_evidence_doc {ef.expected_evidence_doc!r} not in doc_set"
            )

    for es in labels.expected_supported:
        substring = es.cite_substring or es.claim_substring
        assert substring is not None
        assert substring.lower() in brief_text.lower(), (
            f"expected_supported substring {substring!r} not found in brief text"
        )

    for eu in labels.expected_undisputed:
        substring = eu.cite_substring or eu.claim_substring
        assert substring is not None
        assert substring.lower() in brief_text.lower(), (
            f"expected_undisputed substring {substring!r} not found in brief text"
        )


def test_metrics_dataclass_has_all_required_fields():
    """Sanity check on the Metrics shape - the runner serializes this to JSONL."""

    m = Metrics(
        precision=1.0,
        recall=1.0,
        hallucination_rate=0.0,
        tn_rate=1.0,
        total_findings=0,
        true_positives=0,
        false_positives=0,
        false_negatives=0,
        missed_true_negatives=0,
        true_negatives=0,
        hallucinations=0,
        cost_usd=0.0,
        latency_ms=0,
    )
    assert m.precision == 1.0
    assert m.tn_rate == 1.0


# ---------- per-finding classification tests ----------


def test_classify_findings_labels_true_positive():
    """A finding that matches an expected_finding is classified true_positive."""

    finding = _make_consistency(
        claim_text="March 14, 2021",
        verdict="contradicted",
        evidence_text="March 12, 2021",
    )
    expected = [
        ExpectedFinding(
            finding_type="consistency",
            claim_substring="March 14, 2021",
            verdict=["contradicted"],
            expected_evidence_doc="police",
        )
    ]

    matches = _classify(_report(consistency=[finding]), expected_findings=expected)

    assert len(matches) == 1
    assert matches[0].classification == "true_positive"
    assert matches[0].finding_type == "consistency"
    assert matches[0].finding_index == 0
    assert matches[0].matched_label is not None
    assert matches[0].finding_summary.verdict == "contradicted"
    assert matches[0].finding_summary.evidence_doc == "police"


def test_classify_findings_labels_true_negative_for_supported():
    finding = _make_consistency(
        claim_text="Apex Staffing Solutions",
        verdict="supported",
        evidence_text="March 12, 2021",
    )
    matches = _classify(
        _report(consistency=[finding]),
        expected_supported=[
            ExpectedSupported(
                finding_type="consistency",
                claim_substring="Apex Staffing Solutions",
            )
        ],
    )
    assert len(matches) == 1
    assert matches[0].classification == "true_negative"
    assert matches[0].matched_label is not None


def test_classify_findings_labels_true_negative_for_undisputed():
    finding = _make_consistency(
        claim_text="March 14, 2021",
        verdict="undisputed",
        evidence_doc=None,
        evidence_text=None,
    )
    matches = _classify(
        _report(consistency=[finding]),
        expected_undisputed=[
            ExpectedUndisputed(
                finding_type="consistency",
                claim_substring="March 14, 2021",
            )
        ],
    )
    assert len(matches) == 1
    assert matches[0].classification == "true_negative"


def test_classify_findings_labels_false_positive_via_verdict_mismatch():
    """Substring matches an expected_supported, wrong verdict -> false_positive."""

    finding = _make_consistency(
        claim_text="Apex Staffing Solutions",
        verdict="contradicted",
        evidence_text="March 12, 2021",
    )
    matches = _classify(
        _report(consistency=[finding]),
        expected_supported=[
            ExpectedSupported(
                finding_type="consistency",
                claim_substring="Apex Staffing Solutions",
            )
        ],
    )
    assert len(matches) == 1
    assert matches[0].classification == "false_positive"
    assert matches[0].matched_label is not None


def test_classify_findings_labels_hallucination():
    """A finding whose excerpt isn't in the source is a hallucination."""

    finding = _make_consistency(
        claim_text="March 14, 2021",
        verdict="contradicted",
        evidence_text="totally fabricated quote not in doc",
    )
    matches = _classify(_report(consistency=[finding]))
    assert len(matches) == 1
    assert matches[0].classification == "hallucination"


def test_classify_findings_labels_false_positive_when_unlabeled():
    """A finding with no matching label and grounded evidence is FP."""

    finding = _make_consistency(
        claim_text="March 14, 2021",
        verdict="contradicted",
        evidence_text="March 12, 2021",
    )
    matches = _classify(_report(consistency=[finding]))
    assert len(matches) == 1
    assert matches[0].classification == "false_positive"
    assert matches[0].matched_label is None


def test_classify_findings_aligns_with_compute_aggregate():
    """Per-finding classifications match the aggregate counts from compute()."""

    findings = [
        _make_consistency(  # TP
            claim_text="March 14, 2021",
            verdict="contradicted",
            evidence_text="March 12, 2021",
        ),
        _make_consistency(  # TN
            claim_text="Apex Staffing Solutions",
            verdict="supported",
            evidence_text="March 12, 2021",
        ),
        _make_consistency(  # hallucination
            claim_text="cites Doe v. Roe, 111 F.3d",
            verdict="unsupported",
            evidence_text="not in any doc",
        ),
    ]
    expected = [
        ExpectedFinding(
            finding_type="consistency",
            claim_substring="March 14, 2021",
            verdict=["contradicted"],
        )
    ]
    expected_sup = [
        ExpectedSupported(
            finding_type="consistency",
            claim_substring="Apex Staffing Solutions",
        )
    ]
    report = _report(consistency=findings)
    matches = _classify(
        report,
        expected_findings=expected,
        expected_supported=expected_sup,
    )
    metrics = _compute(
        report,
        expected_findings=expected,
        expected_supported=expected_sup,
    )
    tp_count = sum(1 for m in matches if m.classification == "true_positive")
    tn_count = sum(1 for m in matches if m.classification == "true_negative")
    halluc_count = sum(1 for m in matches if m.classification == "hallucination")
    fp_total = sum(
        1 for m in matches if m.classification in ("false_positive", "hallucination")
    )
    assert tp_count == metrics.true_positives
    assert tn_count == metrics.true_negatives
    assert halluc_count == metrics.hallucinations
    assert fp_total == metrics.false_positives


# ---------- run report tests ----------


def test_build_run_report_shape_passes_floors():
    """A clean run produces a valid EvalRunReport with no regression."""

    finding = _make_consistency(
        claim_text="March 14, 2021",
        verdict="contradicted",
        evidence_text="March 12, 2021",
    )
    labels = CaseLabels(
        case_id="synthetic",
        documents=[],
        expected_findings=[
            ExpectedFinding(
                finding_type="consistency",
                claim_substring="March 14, 2021",
                verdict=["contradicted"],
            )
        ],
    )
    metrics = _compute(
        _report(consistency=[finding]),
        expected_findings=labels.expected_findings,
    )
    report = build_run_report(
        per_case=[(labels, _doc_set(), _report(consistency=[finding]), metrics)],
        started_at="2026-05-02T00:00:00+00:00",
        finished_at="2026-05-02T00:00:30+00:00",
        runtime_seconds=30.0,
        git_sha="deadbeef",
    )
    assert report.schema_version == "2"
    assert report.git_sha == "deadbeef"
    assert report.runtime_seconds == 30.0
    assert report.aggregate.cases_run == 1
    assert report.aggregate.true_positives == 1
    assert report.regression.breached is False
    assert report.regression.reasons == []
    assert len(report.cases) == 1
    case = report.cases[0]
    assert case.case_id == "synthetic"
    assert len(case.matches) == 1
    assert case.matches[0].classification == "true_positive"
    assert case.misses == []


def test_build_run_report_flags_recall_regression():
    """Recall below the floor sets regression.breached to True."""

    labels = CaseLabels(
        case_id="synthetic",
        documents=[],
        expected_findings=[
            ExpectedFinding(
                finding_type="consistency",
                claim_substring="March 14, 2021",
                verdict=["contradicted"],
            )
        ],
    )
    metrics = _compute(_report(), expected_findings=labels.expected_findings)
    report = build_run_report(
        per_case=[(labels, _doc_set(), _report(), metrics)],
        started_at="2026-05-02T00:00:00+00:00",
        finished_at="2026-05-02T00:00:30+00:00",
        runtime_seconds=30.0,
        git_sha=None,
    )
    assert report.regression.breached is True
    assert any("recall" in r for r in report.regression.reasons)
    assert report.cases[0].misses, "missed expected finding should appear in misses"
    assert report.cases[0].misses[0].label.claim_substring == "March 14, 2021"
    assert report.cases[0].misses[0].miss_type == "false_negative"


def test_build_run_report_surfaces_missed_true_negatives():
    """Expected_supported labels not satisfied by the pipeline appear in
    `case.misses` with miss_type=missed_true_negative."""

    labels = CaseLabels(
        case_id="synthetic",
        documents=[],
        expected_supported=[
            ExpectedSupported(
                finding_type="consistency",
                claim_substring="Apex Staffing Solutions",
            )
        ],
    )
    metrics = _compute(_report(), expected_supported=labels.expected_supported)
    report = build_run_report(
        per_case=[(labels, _doc_set(), _report(), metrics)],
        started_at="2026-05-02T00:00:00+00:00",
        finished_at="2026-05-02T00:00:30+00:00",
        runtime_seconds=30.0,
        git_sha=None,
    )
    assert report.cases[0].misses
    assert report.cases[0].misses[0].miss_type == "missed_true_negative"


def test_build_run_report_flags_hallucination_regression():
    """Hallucination rate over the ceiling sets regression.breached to True."""

    finding = _make_consistency(
        claim_text="March 14, 2021",
        verdict="contradicted",
        evidence_text="not in any doc",
    )
    labels = CaseLabels(case_id="synthetic", documents=[])
    metrics = _compute(_report(consistency=[finding]))
    report = build_run_report(
        per_case=[(labels, _doc_set(), _report(consistency=[finding]), metrics)],
        started_at="2026-05-02T00:00:00+00:00",
        finished_at="2026-05-02T00:00:30+00:00",
        runtime_seconds=30.0,
        git_sha=None,
    )
    assert report.regression.breached is True
    assert any("hallucination" in r for r in report.regression.reasons)


def test_run_report_serializes_round_trip():
    """The report serializes to JSON and re-validates through the Pydantic model."""

    finding = _make_consistency(
        claim_text="March 14, 2021",
        verdict="contradicted",
        evidence_text="March 12, 2021",
    )
    labels = CaseLabels(
        case_id="synthetic",
        documents=[],
        expected_findings=[
            ExpectedFinding(
                finding_type="consistency",
                claim_substring="March 14, 2021",
                verdict=["contradicted"],
            )
        ],
    )
    metrics = _compute(
        _report(consistency=[finding]), expected_findings=labels.expected_findings
    )
    report = build_run_report(
        per_case=[(labels, _doc_set(), _report(consistency=[finding]), metrics)],
        started_at="2026-05-02T00:00:00+00:00",
        finished_at="2026-05-02T00:00:30+00:00",
        runtime_seconds=30.0,
        git_sha=None,
    )
    blob = report.model_dump_json()
    revived = EvalRunReport.model_validate_json(blob)
    assert revived.schema_version == "2"
    assert revived.aggregate.cases_run == 1
    assert revived.cases[0].matches[0].classification == "true_positive"


def test_runner_writes_report_file_when_report_out_set(tmp_path, monkeypatch):
    """`--report-out PATH` writes a JSON file matching the EvalRunReport schema."""

    import asyncio

    from evals import run as run_mod

    finding = _make_consistency(
        claim_text="March 14, 2021",
        verdict="contradicted",
        evidence_text="March 12, 2021",
    )
    labels = CaseLabels(
        case_id="synthetic",
        documents=[
            CaseDoc(
                document_id="msj",
                display_name="Motion",
                role="brief",
                text_path="/tmp/never_read",
            )
        ],
        expected_findings=[
            ExpectedFinding(
                finding_type="consistency",
                claim_substring="March 14, 2021",
                verdict=["contradicted"],
            )
        ],
    )
    canned_report = _report(consistency=[finding])
    canned_doc_set = _doc_set()

    def fake_load_case(path):
        return labels, canned_doc_set

    async def fake_run_pipeline(doc_set):
        return canned_report

    monkeypatch.setattr(run_mod, "load_case", fake_load_case)
    monkeypatch.setattr(run_mod, "run_pipeline", fake_run_pipeline)
    # Point HISTORY_PATH at the tmp dir so the test doesn't pollute the real file.
    monkeypatch.setattr(run_mod, "HISTORY_PATH", tmp_path / "history.jsonl")

    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "fake.json").write_text("{}")

    out_path = tmp_path / "report.json"
    rc = asyncio.run(
        run_mod._main_async(cases_dir=cases_dir, report_out=out_path, verbose=False)
    )
    assert rc == 0
    assert out_path.exists()
    revived = EvalRunReport.model_validate_json(out_path.read_text())
    assert revived.schema_version == "2"
    assert revived.aggregate.cases_run == 1
    assert revived.cases[0].case_id == "synthetic"
    assert revived.cases[0].matches[0].classification == "true_positive"


def test_runner_archives_pre_v2_history(tmp_path, monkeypatch):
    """First v2 run with a pre-v2 history.jsonl rotates it to history.v1.jsonl."""

    from evals import run as run_mod

    history_path = tmp_path / "history.jsonl"
    # Pre-v2 line: no schema_version field.
    history_path.write_text(
        json.dumps(
            {
                "ts": "2026-05-02T00:00:00+00:00",
                "case_id": "old",
                "scope": "case",
                "precision": 0.5,
                "recall": 1.0,
            }
        )
        + "\n"
    )
    monkeypatch.setattr(run_mod, "HISTORY_PATH", history_path)
    run_mod._archive_pre_v2_history()
    assert not history_path.exists()
    archive = tmp_path / "history.v1.jsonl"
    assert archive.exists()
    assert "old" in archive.read_text()


def test_runner_does_not_archive_v2_history(tmp_path, monkeypatch):
    """A history.jsonl that is already v2 is left alone."""

    from evals import run as run_mod

    history_path = tmp_path / "history.jsonl"
    history_path.write_text(
        json.dumps(
            {
                "schema_version": "2",
                "ts": "2026-05-02T00:00:00+00:00",
                "case_id": "x",
                "scope": "case",
            }
        )
        + "\n"
    )
    monkeypatch.setattr(run_mod, "HISTORY_PATH", history_path)
    run_mod._archive_pre_v2_history()
    assert history_path.exists()
    assert not (tmp_path / "history.v1.jsonl").exists()


# ---------- pipeline dedupe regression test ----------


def test_pipeline_dedupes_consistency_findings_with_same_evidence_span():
    """Two `contradicted` findings pointing at the same evidence_span collapse to one."""

    from pipeline import _dedupe_consistency_findings

    f1 = _make_consistency(
        claim_text="March 14, 2021",
        verdict="contradicted",
        evidence_text="March 12, 2021",
    )
    f2 = _make_consistency(
        claim_text="March 14, 2021",  # different sentence in the brief, same fact
        verdict="contradicted",
        evidence_text="March 12, 2021",
    )
    out = _dedupe_consistency_findings([f1, f2])
    assert len(out) == 1


def test_pipeline_dedupe_keeps_findings_with_different_evidence_spans():
    from pipeline import _dedupe_consistency_findings

    f1 = _make_consistency(
        claim_text="March 14, 2021",
        verdict="contradicted",
        evidence_text="March 12, 2021",
    )
    # Same verdict, different evidence span (different start offset).
    f2_evidence_text = "March 12, 2021 "  # trailing space - different end offset
    f2 = _make_consistency(
        claim_text="March 14, 2021",
        verdict="contradicted",
        evidence_text=f2_evidence_text,
    )
    out = _dedupe_consistency_findings([f1, f2])
    assert len(out) == 2


def test_pipeline_dedupe_keeps_unrelated_undisputed_findings_without_evidence():
    """Two undisputed findings with disjoint claim text are not duplicates."""

    from pipeline import _dedupe_consistency_findings

    f1 = ConsistencyFinding(
        claim=ExtractedClaim(
            claim_text="claim about something",
            claim_span=_claim_span("brief", BRIEF_TEXT, "March 14, 2021"),
        ),
        verdict="undisputed",
        confidence=0.5,
        reasoning="...",
        evidence_quote=None,
        evidence_span=None,
        checked_documents=["police"],
    )
    f2 = ConsistencyFinding(
        claim=ExtractedClaim(
            claim_text="totally different fact entirely",
            claim_span=_claim_span("brief", BRIEF_TEXT, "Doe v. Roe"),
        ),
        verdict="undisputed",
        confidence=0.5,
        reasoning="...",
        evidence_quote=None,
        evidence_span=None,
        checked_documents=["police"],
    )
    out = _dedupe_consistency_findings([f1, f2])
    assert len(out) == 2


def test_pipeline_dedupe_collapses_evidence_less_findings_sharing_distinctive_phrase():
    """Two `undisputed` findings whose claims share a distinctive 3-word
    phrase ("march 10 2023") should collapse to one - the brief asserts
    the same fact in different surrounding sentences."""

    from pipeline import _dedupe_consistency_findings

    f1 = ConsistencyFinding(
        claim=ExtractedClaim(
            claim_text="Rivera filed the instant action on March 10, 2023.",
            claim_span=_claim_span("brief", BRIEF_TEXT, "March 14, 2021"),
        ),
        verdict="undisputed",
        confidence=0.7,
        reasoning="record does not address",
        evidence_quote=None,
        evidence_span=None,
        checked_documents=["police"],
    )
    f2 = ConsistencyFinding(
        claim=ExtractedClaim(
            claim_text="Rivera did not file his complaint until March 10, 2023, after the incident.",
            claim_span=_claim_span("brief", BRIEF_TEXT, "Doe v. Roe"),
        ),
        verdict="undisputed",
        confidence=0.7,
        reasoning="record does not address",
        evidence_quote=None,
        evidence_span=None,
        checked_documents=["police"],
    )
    out = _dedupe_consistency_findings([f1, f2])
    assert len(out) == 1
    assert out[0] is f1


def test_pipeline_dedupe_does_not_collapse_across_verdicts():
    """A `supported` and an `undisputed` finding for the same date must
    both survive - they say different things about the same fact."""

    from pipeline import _dedupe_consistency_findings

    f1 = ConsistencyFinding(
        claim=ExtractedClaim(
            claim_text="Rivera filed on March 10, 2023.",
            claim_span=_claim_span("brief", BRIEF_TEXT, "March 14, 2021"),
        ),
        verdict="undisputed",
        confidence=0.7,
        reasoning="...",
        evidence_quote=None,
        evidence_span=None,
        checked_documents=["police"],
    )
    f2 = ConsistencyFinding(
        claim=ExtractedClaim(
            claim_text="Filing date of March 10, 2023 is correct.",
            claim_span=_claim_span("brief", BRIEF_TEXT, "Doe v. Roe"),
        ),
        verdict="contradicted",
        confidence=0.7,
        reasoning="...",
        evidence_quote=None,
        evidence_span=None,
        checked_documents=["police"],
    )
    out = _dedupe_consistency_findings([f1, f2])
    assert len(out) == 2
