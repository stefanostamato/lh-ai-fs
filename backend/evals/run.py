"""Eval runner. Discovers labeled cases under `cases/`, builds a `DocumentSet`
from each case JSON, runs the real pipeline, computes metrics, prints a
summary, appends to `history.jsonl`, and exits non-zero on regression.

This is the only place we use real LLM calls in the test loop. Unit tests
mock at the `llm.py` boundary; eval is the real-world quality bar.

The runner is intentionally agnostic to which documents a case references -
it builds a `DocumentSet` from `documents[]` in the case JSON. It does NOT
call `case_loader.load_default_case()`; that function is the API handler's
business, not the eval harness's.
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from evals.labels import (
    CaseDoc,
    CaseLabels,
    ExpectedFinding,
    ExpectedSupported,
    ExpectedUndisputed,
)
from evals.metrics import Metrics, classify_findings, compute
from pipeline import run_pipeline
from schemas import (
    DocumentInput,
    DocumentRole,
    DocumentSet,
    Report,
)


# Regression floors. Plan §3.7 Acceptance Criteria.
RECALL_FLOOR: float = 0.6
HALLUCINATION_CEILING: float = 0.1


REPO_ROOT: Path = Path(__file__).resolve().parents[2]
CASES_DIR: Path = Path(__file__).resolve().parent / "cases"
HISTORY_PATH: Path = Path(__file__).resolve().parent / "history.jsonl"
HISTORY_SCHEMA_VERSION: str = "2"


def _resolve_text_path(text_path: str, case_path: Path) -> Path:
    """Resolve a `text_path` from a case JSON to an absolute file.

    Lookup order:
      1. As-is (absolute or already cwd-relative)
      2. Relative to the cwd
      3. Relative to repo root (lets cases declare `backend/documents/...`)
      4. Relative to the case JSON's directory
    """

    raw = Path(text_path)
    if raw.is_absolute() and raw.exists():
        return raw

    candidates = [
        Path.cwd() / raw,
        REPO_ROOT / raw,
        case_path.parent / raw,
    ]
    for c in candidates:
        if c.exists():
            return c
    # Default to the most informative path for the error.
    return Path.cwd() / raw


def load_case(case_path: Path) -> tuple[CaseLabels, DocumentSet]:
    """Read a case JSON and return its labels + a built `DocumentSet`.

    Raises `FileNotFoundError` if any declared `text_path` doesn't exist.
    """

    raw = json.loads(Path(case_path).read_text())
    labels = CaseLabels.model_validate(raw)

    documents: list[DocumentInput] = []
    for doc in labels.documents:
        resolved = _resolve_text_path(doc.text_path, Path(case_path))
        if not resolved.exists():
            raise FileNotFoundError(
                f"case {labels.case_id!r}: text_path not found: {doc.text_path} "
                f"(resolved to {resolved})"
            )
        documents.append(
            DocumentInput(
                document_id=doc.document_id,
                display_name=doc.display_name,
                role=DocumentRole(doc.role),
                text=resolved.read_text(),
            )
        )

    doc_set = DocumentSet(documents=documents)
    return labels, doc_set


async def run_case(
    labels: CaseLabels, doc_set: DocumentSet
) -> tuple[Report, Metrics]:
    """Run the real pipeline on a case and compute metrics. Real LLM calls."""

    started = time.monotonic()
    report = await run_pipeline(doc_set)
    latency_ms = int((time.monotonic() - started) * 1000)

    metrics = compute(
        actual_report=report,
        expected_findings=labels.expected_findings,
        expected_supported=labels.expected_supported,
        expected_undisputed=labels.expected_undisputed,
        doc_set=doc_set,
        latency_ms=latency_ms,
    )
    return report, metrics


def _format_metrics(case_id: str, m: Metrics) -> str:
    return (
        f"  {case_id}\n"
        f"    precision           : {m.precision:.2f}\n"
        f"    recall              : {m.recall:.2f}\n"
        f"    hallucination_rate  : {m.hallucination_rate:.2f}\n"
        f"    tn_rate             : {m.tn_rate:.2f}\n"
        f"    fabrication_detection: {m.fabrication_detection_rate:.2f}\n"
        f"    findings (TP/FP/FN/TN/total): "
        f"{m.true_positives}/{m.false_positives}/{m.false_negatives}/"
        f"{m.true_negatives}/{m.total_findings}\n"
        f"    hallucinations      : {m.hallucinations}\n"
        f"    cost_usd            : ${m.cost_usd:.4f}\n"
        f"    latency_ms          : {m.latency_ms}"
    )


def _aggregate(per_case: list[tuple[str, Metrics]]) -> Metrics:
    """Roll TP/FP/FN/TN/hallucinations across cases. Cost and latency sum."""

    if not per_case:
        return Metrics(
            precision=1.0,
            recall=1.0,
            hallucination_rate=0.0,
            tn_rate=1.0,
            fabrication_detection_rate=1.0,
            total_findings=0,
            true_positives=0,
            false_positives=0,
            false_negatives=0,
            missed_true_negatives=0,
            true_negatives=0,
            hallucinations=0,
            fabrication_targets=0,
            fabrications_caught=0,
            cost_usd=0.0,
            latency_ms=0,
        )

    tp = sum(m.true_positives for _, m in per_case)
    fp = sum(m.false_positives for _, m in per_case)
    fn = sum(m.false_negatives for _, m in per_case)
    tn = sum(m.true_negatives for _, m in per_case)
    missed_tn = sum(m.missed_true_negatives for _, m in per_case)
    hallucinations = sum(m.hallucinations for _, m in per_case)
    total = sum(m.total_findings for _, m in per_case)
    fab_targets = sum(m.fabrication_targets for _, m in per_case)
    fab_caught = sum(m.fabrications_caught for _, m in per_case)
    cost = sum(m.cost_usd for _, m in per_case)
    latency = sum(m.latency_ms for _, m in per_case)

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    hr = hallucinations / total if total else 0.0
    tn_rate = tn / (tn + missed_tn) if (tn + missed_tn) else 1.0
    fab_rate = fab_caught / fab_targets if fab_targets else 1.0

    return Metrics(
        precision=precision,
        recall=recall,
        hallucination_rate=hr,
        tn_rate=tn_rate,
        fabrication_detection_rate=fab_rate,
        total_findings=total,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        missed_true_negatives=missed_tn,
        true_negatives=tn,
        hallucinations=hallucinations,
        fabrication_targets=fab_targets,
        fabrications_caught=fab_caught,
        cost_usd=cost,
        latency_ms=latency,
    )


def _archive_pre_v2_history() -> None:
    """If the existing history.jsonl predates schema v2, move it aside.

    A v1 file has lines without `schema_version`; mixing those with v2 lines
    would confuse the slash-command's regression diff (it would compare
    apples to oranges). Rotate the file to `history.v1.jsonl` once, then
    start a fresh `history.jsonl`. No-op if the file is already v2 or
    doesn't exist.
    """

    if not HISTORY_PATH.exists():
        return
    try:
        first_line = next(
            (
                line
                for line in HISTORY_PATH.read_text().splitlines()
                if line.strip()
            ),
            None,
        )
    except OSError:
        return
    if first_line is None:
        return
    try:
        first = json.loads(first_line)
    except json.JSONDecodeError:
        return
    if first.get("schema_version") == HISTORY_SCHEMA_VERSION:
        return
    archive_path = HISTORY_PATH.parent / "history.v1.jsonl"
    HISTORY_PATH.rename(archive_path)


def _append_history(case_id: str, scope: str, m: Metrics) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "ts": datetime.now(timezone.utc).isoformat(),
        "case_id": case_id,
        "scope": scope,
        "precision": m.precision,
        "recall": m.recall,
        "hallucination_rate": m.hallucination_rate,
        "tn_rate": m.tn_rate,
        "fabrication_detection_rate": m.fabrication_detection_rate,
        "total_findings": m.total_findings,
        "true_positives": m.true_positives,
        "false_positives": m.false_positives,
        "false_negatives": m.false_negatives,
        "missed_true_negatives": m.missed_true_negatives,
        "true_negatives": m.true_negatives,
        "hallucinations": m.hallucinations,
        "fabrication_targets": m.fabrication_targets,
        "fabrications_caught": m.fabrications_caught,
        "cost_usd": m.cost_usd,
        "latency_ms": m.latency_ms,
    }
    with HISTORY_PATH.open("a") as f:
        f.write(json.dumps(line) + "\n")


def _discover_cases(cases_dir: Path) -> list[Path]:
    return sorted(p for p in cases_dir.glob("*.json"))


def _git_sha() -> str | None:
    """Best-effort git SHA. Returns None if not in a repo or git is missing."""

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if out.returncode != 0:
            return None
        sha = out.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if dirty.returncode == 0 and dirty.stdout.strip():
            sha = f"{sha}-dirty"
        return sha or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _case_path_for_report(case_path: Path) -> str:
    try:
        return str(case_path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(case_path)


def build_run_report(
    per_case: list[tuple[CaseLabels, DocumentSet, Report, Metrics]],
    started_at: str,
    finished_at: str,
    runtime_seconds: float,
    git_sha: str | None,
    case_paths: dict[str, Path] | None = None,
):
    """Assemble the verbose `EvalRunReport` from the run outputs.

    Imported lazily because `evals.report` imports from this module.
    """

    from evals.report import (
        EvalAggregate,
        EvalCaseReport,
        EvalRunReport,
        ExpectedMiss,
        Regression,
    )

    case_reports: list[EvalCaseReport] = []
    aggregate_inputs: list[tuple[str, Metrics]] = []

    for labels, doc_set, pipeline_report, metrics in per_case:
        matches = classify_findings(
            actual_report=pipeline_report,
            expected_findings=labels.expected_findings,
            expected_supported=labels.expected_supported,
            expected_undisputed=labels.expected_undisputed,
            doc_set=doc_set,
        )

        # A label is "satisfied" if a TP claimed it (for ExpectedFinding) or
        # a TN claimed it (for ExpectedSupported / ExpectedUndisputed). A
        # substring-only FP claim does NOT satisfy the label - the pipeline
        # got the verdict wrong, so we still surface the label as a miss.
        satisfied_tp: set[tuple[str, str]] = set()
        satisfied_tn: set[tuple[str, str]] = set()
        for m in matches:
            if m.matched_label is None:
                continue
            sub = (
                m.matched_label.cite_substring
                or m.matched_label.claim_substring
            )
            if sub is None:
                continue
            key = (m.matched_label.finding_type, sub)
            if m.classification == "true_positive":
                satisfied_tp.add(key)
            elif m.classification == "true_negative":
                satisfied_tn.add(key)

        misses: list[ExpectedMiss] = []
        for ef in labels.expected_findings:
            sub = ef.cite_substring or ef.claim_substring
            key = (ef.finding_type, sub) if sub is not None else None
            if key is not None and key in satisfied_tp:
                continue
            note = (
                f"no actual {ef.finding_type} finding contained "
                f"{sub!r} with the expected verdict"
                if sub
                else f"no actual {ef.finding_type} finding matched"
            )
            misses.append(
                ExpectedMiss(label=ef, miss_type="false_negative", note=note)
            )
        for es in labels.expected_supported:
            sub = es.cite_substring or es.claim_substring
            key = (es.finding_type, sub) if sub is not None else None
            if key is not None and key in satisfied_tn:
                continue
            note = (
                f"no actual {es.finding_type} finding contained "
                f"{sub!r} with verdict=`supported`"
                if sub
                else f"no actual {es.finding_type} finding marked `supported`"
            )
            misses.append(
                ExpectedMiss(
                    label=es, miss_type="missed_true_negative", note=note
                )
            )
        for eu in labels.expected_undisputed:
            sub = eu.cite_substring or eu.claim_substring
            key = (eu.finding_type, sub) if sub is not None else None
            if key is not None and key in satisfied_tn:
                continue
            note = (
                f"no actual {eu.finding_type} finding contained "
                f"{sub!r} with verdict=`undisputed`"
                if sub
                else f"no actual {eu.finding_type} finding marked `undisputed`"
            )
            misses.append(
                ExpectedMiss(
                    label=eu, miss_type="missed_true_negative", note=note
                )
            )

        case_path = case_paths.get(labels.case_id) if case_paths else None
        case_reports.append(
            EvalCaseReport(
                case_id=labels.case_id,
                case_path=_case_path_for_report(case_path) if case_path else "",
                metrics=asdict(metrics),
                matches=matches,
                misses=misses,
                pipeline_report=pipeline_report,
                partial_failures=list(pipeline_report.meta.partial_failures),
            )
        )
        aggregate_inputs.append((labels.case_id, metrics))

    agg_metrics = _aggregate(aggregate_inputs)
    aggregate = EvalAggregate(
        precision=agg_metrics.precision,
        recall=agg_metrics.recall,
        hallucination_rate=agg_metrics.hallucination_rate,
        tn_rate=agg_metrics.tn_rate,
        total_findings=agg_metrics.total_findings,
        true_positives=agg_metrics.true_positives,
        false_positives=agg_metrics.false_positives,
        false_negatives=agg_metrics.false_negatives,
        true_negatives=agg_metrics.true_negatives,
        hallucinations=agg_metrics.hallucinations,
        cost_usd=agg_metrics.cost_usd,
        latency_ms=agg_metrics.latency_ms,
        cases_run=len(per_case),
    )

    reasons: list[str] = []
    if agg_metrics.recall < RECALL_FLOOR:
        reasons.append(
            f"recall {agg_metrics.recall:.2f} < floor {RECALL_FLOOR:.2f}"
        )
    if agg_metrics.hallucination_rate > HALLUCINATION_CEILING:
        reasons.append(
            f"hallucination_rate {agg_metrics.hallucination_rate:.2f} "
            f"> ceiling {HALLUCINATION_CEILING:.2f}"
        )

    return EvalRunReport(
        schema_version="2",
        started_at=started_at,
        finished_at=finished_at,
        runtime_seconds=runtime_seconds,
        cases=case_reports,
        aggregate=aggregate,
        regression=Regression(breached=bool(reasons), reasons=reasons),
        git_sha=git_sha,
    )


def _format_verbose(report) -> str:
    """Human-readable rendering of the verbose report. Used by `--verbose`."""

    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("VERBOSE REPORT")
    lines.append("=" * 60)
    lines.append(f"schema_version : {report.schema_version}")
    lines.append(f"started_at     : {report.started_at}")
    lines.append(f"finished_at    : {report.finished_at}")
    lines.append(f"runtime_seconds: {report.runtime_seconds:.2f}")
    lines.append(f"git_sha        : {report.git_sha}")
    lines.append("")
    for case in report.cases:
        lines.append(f"-- {case.case_id} ({case.case_path})")
        lines.append(f"   matches: {len(case.matches)} | misses: {len(case.misses)}")
        for m in case.matches:
            lines.append(
                f"     [{m.classification:<14}] {m.finding_type} #{m.finding_index} "
                f"verdict={m.finding_summary.verdict} "
                f"conf={m.finding_summary.confidence:.2f} "
                f"evidence_doc={m.finding_summary.evidence_doc} "
                f"text={m.finding_summary.cite_or_claim_text[:80]!r}"
            )
        for miss in case.misses:
            sub = miss.label.cite_substring or miss.label.claim_substring
            lines.append(f"     [MISS]            {miss.label.finding_type} substring={sub!r}")
        for pf in case.partial_failures:
            lines.append(f"     [partial_failure] agent={pf.agent} error={pf.error}")
        lines.append("")
    lines.append(
        f"regression.breached={report.regression.breached} "
        f"reasons={report.regression.reasons}"
    )
    return "\n".join(lines)


async def _main_async(
    cases_dir: Path,
    report_out: Path | None = None,
    verbose: bool = False,
) -> int:
    paths = _discover_cases(cases_dir)
    if not paths:
        print(f"No cases found in {cases_dir}", file=sys.stderr)
        return 1

    _archive_pre_v2_history()

    per_case_metrics: list[tuple[str, Metrics]] = []
    per_case_full: list[tuple[CaseLabels, DocumentSet, Report, Metrics]] = []
    case_paths: dict[str, Path] = {}
    print(f"Running {len(paths)} eval case(s) from {cases_dir}\n")

    started_at_dt = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()

    for path in paths:
        labels, doc_set = load_case(path)
        print(f"-> {labels.case_id} ({path.name})")
        pipeline_report, metrics = await run_case(labels, doc_set)
        per_case_metrics.append((labels.case_id, metrics))
        per_case_full.append((labels, doc_set, pipeline_report, metrics))
        case_paths[labels.case_id] = path
        print(_format_metrics(labels.case_id, metrics))
        print()
        _append_history(labels.case_id, "case", metrics)

    finished_at_dt = datetime.now(timezone.utc)
    runtime_seconds = time.monotonic() - started_monotonic

    aggregate = _aggregate(per_case_metrics)
    print("=" * 60)
    print("AGGREGATE")
    print(_format_metrics("aggregate", aggregate))
    _append_history("__aggregate__", "aggregate", aggregate)

    run_report = build_run_report(
        per_case=per_case_full,
        started_at=started_at_dt.isoformat(),
        finished_at=finished_at_dt.isoformat(),
        runtime_seconds=runtime_seconds,
        git_sha=_git_sha(),
        case_paths=case_paths,
    )

    if report_out is not None:
        report_out.parent.mkdir(parents=True, exist_ok=True)
        report_out.write_text(run_report.model_dump_json(indent=2))
        print(f"\nEVAL_REPORT_PATH={report_out}")

    if verbose:
        print()
        print(_format_verbose(run_report))

    if run_report.regression.breached:
        for reason in run_report.regression.reasons:
            print(f"\nFAIL: {reason}", file=sys.stderr)
        return 2

    print("\nOK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the eval suite against real LLMs.",
    )
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=CASES_DIR,
        help=f"Directory of case JSON files (default: {CASES_DIR})",
    )
    parser.add_argument(
        "--report-out",
        type=Path,
        default=None,
        help=(
            "If set, write the full verbose JSON report (per-case classifications, "
            "matches, misses, pipeline output, regression flags) to this path."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Also print the verbose report to stdout in human-readable form.",
    )
    args = parser.parse_args()
    return asyncio.run(
        _main_async(
            cases_dir=args.cases_dir,
            report_out=args.report_out,
            verbose=args.verbose,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
