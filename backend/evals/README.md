# Eval harness

The single place we use real LLM calls in the test loop. Unit tests mock at the `llm.py` boundary; this is the real-world quality bar.

## How to run

From inside `backend/`:

```bash
cd backend && python -m evals.run
```

Costs real money. Each case fires the full pipeline (~10-15 LLM calls per case at gpt-4o pricing). The run prints per-case + aggregate metrics, appends each result as a JSONL line to `backend/evals/history.jsonl`, and exits non-zero if metrics regress past the floors below.

You can point the runner at an alternate cases directory:

```bash
cd backend && python -m evals.run --cases-dir evals/cases
```

### Verbose JSON report (for tooling)

The default stdout summary is for humans. For machine consumption - the `/eval` slash-command, dashboards, anything that diffs runs - pass `--report-out`:

```bash
cd backend && python -m evals.run --report-out /tmp/eval_report.json
```

The runner still prints the human summary to stdout, plus one extra line: `EVAL_REPORT_PATH=<path>`. The JSON file at that path is the source of truth for the run. Add `--verbose` to also dump a human-readable rendering of the verbose report to stdout - useful when piping the runner through a model that can't read files.

```bash
cd backend && python -m evals.run --report-out /tmp/eval_report.json --verbose
```

The JSON shape (full schema in `backend/evals/report.py`):

```
EvalRunReport
  schema_version: "1"
  started_at, finished_at, runtime_seconds
  git_sha                   # best-effort; null if not in a repo
  cases: [
    EvalCaseReport
      case_id, case_path
      metrics                # the existing Metrics dataclass, serialized
      matches: [             # one entry per actual finding the pipeline produced
        FindingMatch
          finding_type, finding_index
          classification     # true_positive | false_positive | hallucination | uncategorized
          matched_label      # the ExpectedFinding/ExpectedNonFinding it matched, or null
          finding_summary    # cite_or_claim_text, verdict, confidence, evidence_doc
      ]
      misses: [              # expected_findings the pipeline didn't catch (FNs)
        ExpectedMiss { label, note }
      ]
      pipeline_report        # the full Report (memo, findings, meta)
      partial_failures       # also present in pipeline_report.meta - lifted here for skim
  ]
  aggregate                  # Metrics fields + cases_run
  regression
    breached: bool
    reasons: list[str]       # e.g. "recall 0.45 < floor 0.60"
```

The classifications above are exclusive per finding, in this order of precedence: matched an unmatched `expected_finding` -> `true_positive`; otherwise structurally hallucinated (excerpt not in source) -> `hallucination`; otherwise matched an `expected_non_finding` -> `false_positive`; otherwise -> `uncategorized` (the pipeline produced a finding nobody labeled). Aggregate counts in `metrics` still come from the same `metrics.compute()` you've always seen - the per-finding view is additive.

## How to read the output

Per case and in aggregate the runner prints:

- `precision` - of the findings the pipeline raised, what fraction matched a labeled `expected_finding`. False flags hurt trust more than missed flags, so this is the metric to watch first.
- `recall` - of the labeled `expected_findings`, what fraction the pipeline caught. The acceptance floor is `0.6`.
- `hallucination_rate` - of all findings produced, what fraction had an `evidence_span.excerpt` that doesn't appear verbatim in the source document. The acceptance ceiling is `0.1`. This is structural, not LLM-judged - if the pipeline cites text that isn't in the doc, the metric catches it.
- `findings (TP/FP/FN/total)` - raw counts behind precision and recall.
- `cost_usd` - approximate spend at gpt-4o pricing constants in `metrics.py`.
- `latency_ms` - wall-clock time the pipeline took on this case.

Exit code:

- `0` - pass.
- `1` - no cases found.
- `2` - regression: `recall < 0.6` or `hallucination_rate > 0.1`.

## How matching works

A pipeline finding matches an `expected_finding` when:

1. **Type matches.** Citation findings only match citation labels; consistency findings only match consistency labels.
2. **Substring matches** (case-insensitive). Citation labels carry `cite_substring`, consistency labels carry `claim_substring`. The substring must appear in the actual finding's `citation.cite` or `claim.claim_text`.
3. **Verdict is in the label's allowed list.** `verdict` on a label is a list - any of the listed verdicts on the actual finding satisfies the match. This lets a fabricated cite pass as either `unsupported` or `could_not_verify` without us having to predict which one the pipeline picks.
4. **`expected_evidence_doc` matches** if set. The actual finding's `evidence_span.document_id` must equal the labeled `expected_evidence_doc` (the case's chosen `document_id`, e.g. `"police"` - not the file stem).

Unmatched findings count as false positives. Unmatched expected findings count as false negatives. Findings that match an `expected_non_finding` count as false positives explicitly - these are the labels that say "this looks suspicious but is actually fine; don't flag it."

A finding is a hallucination when its `evidence_span.excerpt` is non-null and doesn't appear verbatim in the text of the document referenced by `evidence_span.document_id`. Findings without an `evidence_span` (legitimate for `could_not_verify`) are not hallucinations.

## How to add a case

Drop a JSON file in `backend/evals/cases/`. Schema:

```json
{
  "case_id": "<slug>",
  "documents": [
    {
      "document_id": "msj",
      "display_name": "Motion for Summary Judgment",
      "role": "brief",
      "text_path": "backend/documents/some_brief.txt"
    },
    {
      "document_id": "police",
      "display_name": "Police Report",
      "role": "record",
      "text_path": "backend/documents/some_record.txt"
    }
  ],
  "expected_findings": [
    {
      "finding_type": "consistency",
      "claim_substring": "<verbatim substring from the brief>",
      "verdict": ["contradicted"],
      "expected_evidence_doc": "police",
      "note": "<why this is a flaw>"
    },
    {
      "finding_type": "citation",
      "cite_substring": "<verbatim substring from the brief's citation>",
      "verdict": ["unsupported", "could_not_verify"],
      "note": "<why this is a flaw>"
    }
  ],
  "expected_non_findings": [
    {
      "finding_type": "consistency",
      "claim_substring": "<a claim that looks suspicious but is actually fine>",
      "note": "<why this should NOT be flagged>"
    }
  ]
}
```

Rules:

- **Exactly one document with `role: "brief"`.** The pipeline validates this at construction.
- **`document_id`s are unique within a case.** Pick whatever short ids you want (`"msj"`, `"police"`, `"medical"` for Rivera). The labels reference these ids in `expected_evidence_doc`, not file stems.
- **`text_path` resolves against the repo root** when relative. So `backend/documents/foo.txt` works regardless of where you run the command from. Absolute paths also work; relative to the case JSON's directory works as a last fallback.
- **Every label substring must appear in the brief text.** This is asserted by `tests/test_evals.py::test_load_real_rivera_case_file`. Cross-check with grep before committing.
- **Pair findings with non-findings.** The non-findings grade precision. Without them, a pipeline that flags everything wins on recall and the harness can't tell.

Run `pytest backend/tests/test_evals.py` to confirm the new case parses and its substrings cross-reference. Then run the eval suite to see the numbers.

## What's intentionally not here

- **No mocking.** Eval is the real thing. If you want fast deterministic tests, those live under `backend/tests/`.
- **No LLM-as-judge metrics.** Hallucination is structural. Precision and recall come from labels you can grep. Adding judged metrics is a future call - it costs more and introduces a new failure mode (the judge LLM has its own biases).
- **No retry policy at the runner level.** The pipeline already swallows agent failures into `could_not_verify` findings + `partial_failures`. If a case crashes the pipeline outright, that's a real failure and the runner should crash with it.
