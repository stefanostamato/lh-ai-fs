---
description: Run the eval suite against real LLM calls and report precision, recall, and hallucination rate.
argument-hint: [optional: case filter, e.g. "citations" to run only citation cases]
---

You are running the eval suite. Real LLM calls, real numbers, no mocking. Filter (if any): $ARGUMENTS

## What you're doing

The README grades us on eval quality and runs our suite during review. This command is the iteration loop: change a prompt, run `/eval`, see what moved. It hits the actual OpenAI API so it costs tokens and takes time. That's the point.

## Steps

1. **Pre-flight.** Check that `OPENAI_API_KEY` is set. If not, tell the user and stop. Check that `backend/evals/run.py` exists - if it doesn't, stop and tell the user the eval harness hasn't been built yet (point them at `/plan`).
2. **Run it.** Invoke the eval suite the way the README says it should be runnable - one command. Use `python -m evals.run` (or `python run_evals.py`, whichever the project settled on). If `$ARGUMENTS` is non-empty, pass it as a filter flag.
3. **Capture everything.** Token usage, per-case results, aggregate metrics, runtime. Don't summarize away the per-case detail - regressions hide there.
4. **Report.** Print a structured summary:

   ```
   # Eval run - <timestamp>

   ## Aggregate
   - Precision:           XX.X%  (Y true positives / Z flagged)
   - Recall:              XX.X%  (Y caught / Z known flaws)
   - Hallucination rate:  XX.X%  (findings without source evidence)
   - Verifiability rate:  XX.X%  (findings whose TextSpan resolves to the cited document range)
   - Cases run:           N
   - Runtime:             Xs
   - Tokens:              prompt=X, completion=Y

   ## Per-side breakdown (where data supports it)
   - Plaintiff-flagged: precision X% / recall X%
   - Defense-flagged:   precision X% / recall X%
   - Asymmetry note:    <flag if one side is consistently over-flagged>


   ## Per-case
   | Case | Expected | Got | Verdict |
   |------|----------|-----|---------|
   | ...  | ...      | ... | PASS/FAIL |

   ## Regressions vs last run
   <if a previous run log exists, diff the numbers>

   ## What moved and why
   <2-3 sentences interpreting the result>
   ```

5. **Save the run.** Append the aggregate line to `backend/evals/history.jsonl` with timestamp, git SHA, and metrics, so you can diff future runs against this one.

## Rules

- **Never mock.** This command exists specifically to test against the real model. If the user wants fast feedback, that's what unit tests are for.
- **Never tweak prompts mid-run** to "fix" a failing case. Report the truth, then let the user decide.
- **Honest numbers only.** If recall is 60%, recall is 60%. Don't round up, don't filter to easy cases, don't skip the negative cases that hurt precision.
- **If the suite crashes, report the crash** - don't paper over it with partial results.
