---
description: Run the eval suite against real LLM calls and report precision, recall, and hallucination rate.
argument-hint: [optional: extra flags forwarded to the runner, e.g. "--cases-dir evals/cases"]
---

You are running the eval suite. Real LLM calls, real numbers, no mocking. Extra flags (if any): $ARGUMENTS

## What you're doing

The README grades us on eval quality. This command is the iteration loop: change a prompt, run `/eval`, see what moved. It hits the real OpenAI API so it costs tokens and takes time. That's the point.

Your job is to invoke the Python runner, then parse the verbose JSON report it writes. You do not recompute metrics or re-judge findings yourself - the Python code is the source of truth. You read its output and present it.

## Steps

1. **Pre-flight.** Check that `OPENAI_API_KEY` is set. If not, tell the user and stop. Check that `backend/evals/run.py` exists - if it doesn't, stop and tell the user the eval harness hasn't been built yet (point them at `/plan`).

2. **Run the eval.** Invoke:

   ```bash
   cd backend && python -m evals.run --report-out /tmp/eval_report.json $ARGUMENTS
   ```

   This prints the per-case + aggregate human summary AND writes the verbose JSON report to `/tmp/eval_report.json`. The runner also echoes `EVAL_REPORT_PATH=<path>` near the end of stdout - if `$ARGUMENTS` overrides the path, pick it up from there. Exit code: `0` pass, `2` regression (recall floor or hallucination ceiling breached), `1` no cases. Any other non-zero is a crash - report the crash and stop.

3. **Read the JSON.** Load `/tmp/eval_report.json`. Confirm `schema_version == "1"` - if not, tell the user the report format drifted and stop. Everything below comes out of this file. Do not redo any matching or metric arithmetic.

4. **Read history for the regression diff.** Read `backend/evals/history.jsonl`. Filter to lines where `case_id == "__aggregate__"`. The last line is the run you just did; the line before it is the previous run. If only one aggregate line exists, say "first run, no regression baseline yet" in the regressions section.

5. **Report.** Print this exact structure (fill in real numbers from the JSON, not from the summary stdout - the JSON is canonical):

   ```
   # Eval run - <report.finished_at>
   git_sha: <report.git_sha or "unknown">

   ## Aggregate
   - Precision:           XX.X%  (TP / (TP+FP))
   - Recall:              XX.X%  (TP / (TP+FN))
   - Hallucination rate:  XX.X%  (hallucinated / total findings)
   - Cases run:           <aggregate.cases_run>
   - Findings:            TP=<n> FP=<n> FN=<n> hallucinations=<n> total=<n>
   - Cost:                $<aggregate.cost_usd>
   - Runtime:             <runtime_seconds>s

   ## Per-case
   | Case | TP | FP | FN | Halluc. | Precision | Recall | Verdict |
   |------|----|----|----|---------|-----------|--------|---------|
   | ...  | .. | .. | .. | ..      | ..        | ..     | PASS/FAIL |

   Verdict per case is PASS if recall >= 0.6 AND hallucination_rate <= 0.1, else FAIL.

   ## Notable misses (false negatives)
   For each case, list every entry in `case.misses`. Show the label substring
   (cite_substring or claim_substring) and the case_id. If none, write "none".

   ## Notable hallucinations
   For each case, list every match where `classification == "hallucination"`.
   Show: case_id, finding_type, finding_summary.cite_or_claim_text (truncate
   to ~80 chars), and finding_summary.evidence_doc (the doc the pipeline
   pointed at). If none, write "none".

   ## Partial failures
   For each case with non-empty `partial_failures`, list agent + error.
   If none across all cases, write "none".

   ## Regressions vs last run
   If two or more aggregate lines exist in history.jsonl, diff the latest
   against the previous one:
     - Precision delta:           +/- X.X pp
     - Recall delta:              +/- X.X pp
     - Hallucination delta:       +/- X.X pp
   Flag any metric that moved by more than 5 percentage points.
   If only one aggregate line exists, write: "first run, no regression baseline yet."

   ## What moved and why
   2-3 sentences interpreting the result. Tie back to specific misses or
   hallucinations from the sections above. If recall dropped, name the
   labels that moved from caught to missed. If a regression floor was
   breached, lead with that and quote `report.regression.reasons`.
   ```

## Rules

- **Never mock.** This command runs against the real model. Fast deterministic feedback is what unit tests are for.
- **Never tweak prompts mid-run** to "fix" a failing case. Report the truth, then let the user decide.
- **Honest numbers only.** Numbers come from the JSON report, full stop. Don't round up, don't filter to easy cases.
- **Don't recompute.** Aggregate fractions, classifications, hallucination flags, regression breach status - all live in the JSON. You just read and format.
- **If the suite crashes, report the crash.** Don't paper over it with partial results. If the JSON file is missing or malformed, say so.
- **If `report.regression.breached` is true, lead with it.** Quote `report.regression.reasons` verbatim before any other interpretation.
