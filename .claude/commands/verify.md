---
description: Verify code matches the plan - review every task's acceptance criteria, run tests, return pass/fail with evidence.
argument-hint: <plan filename or path>
---

You're an independent verifier. The user wants an honest pass/fail on whether the code matches the plan. Lean skeptical - the README explicitly values an honest 60% over a cherry-picked 100%. Plan: $ARGUMENTS

Style note: write reports the way [AGENTS.md](../../AGENTS.md) dictates - dashes (`-`), no emdashes, talk like a person.

## Step 0 - Locate

Resolve the plan path. If `$ARGUMENTS` is empty, list `.claude/plans/*.md` and ask. Read the entire plan, including any Post-Build Refinements.

## Step 1 - Verify each task

For every task T<N> in the plan, do all of:

1. **File presence.** Every file in "Files to create/modify" exists (or was modified, per `git log` / `git diff main...HEAD`).
2. **Acceptance criteria.** Walk every checkbox under the task. For each, find concrete evidence in the code that it's satisfied. "It looks plausible" is not evidence - point to a file:line or a test.
3. **Tests.** Run the task's test command yourself via Bash. Capture pass/fail and the last ~20 lines of output.
4. **Style compliance.** Spot-check that the code follows AGENTS.md (no dead code, structured data between agents, uncertainty surfaced rather than fabricated, prompts are explicit, dashes not emdashes in any new docs). Note specific violations with file:line.

Record results per task:

```
T<N> - <name>: PASS | PARTIAL | FAIL
  ✓ <criterion> - <evidence>
  ✗ <criterion> - <what's missing or wrong>
  Tests: <pass|fail>, command: `<cmd>`, last line: <line>
  Style notes: <anything>
```

## Step 2 - Verify the plan-level criteria

Walk the plan's top-level "Acceptance criteria for the whole plan" the same way. These are what the *plan* commits to delivering, separate from individual task criteria.

## Step 3 - Spot-check independently

Beyond the checklist, do at least these independent checks:

- Run `POST /analyze` end-to-end (via curl or the test client) and inspect the actual JSON response shape. Does it match the data contracts in the plan's Specification section?
- Run the eval suite (if present per the plan). Report the actual numbers - precision, recall, hallucination rate, verifiability rate, whatever the plan defines.
- **Span resolvability.** Pick a random flag from a real `/analyze` response. Take its `TextSpan` (document_id + offsets) and slice that range out of the actual document file. Does the slice match the claim being made? If you can't locate the cited span in the source, that flag is malformed - count it as a hallucination, not a hit.
- **Memo doesn't upgrade verdicts.** Read the memo output alongside the underlying findings. Does the memo make a verdict the verifier didn't make? Does it call something "contradicted" that the schema lists as "unverified"? If yes, FAIL.
- **Voice check.** Read at least one agent prompt and one stretch of memo output. Is the voice a clerk's neutral note, or does it slip into advocacy ("the plaintiff fails to show...", "this clearly establishes...")? Advocate voice is a style violation per AGENTS.md.
- **Eval symmetry.** Skim the eval cases. Is there at least one negative case (system should *not* flag this)? At least one paired case where the same flaw type appears on opposing sides? Aggregate-only metrics can hide one-sided suspicion - flag if no per-side or paired check exists.
- Read at least one agent prompt and judge whether it matches the plan's intent for that agent.
- Skim git diff for any code that doesn't appear to belong to a planned task. Flag uncategorized changes.

## Step 4 - Report

Print a structured verdict:

```
# Verification - <plan name>

**Verdict:** PASS | PARTIAL | FAIL

## Task-level results
<table or list from Step 1>

## Plan-level acceptance criteria
<checklist with evidence>

## Eval results (actual numbers, not claims)
<from running the suite>

## Independent observations
<things that worked, things that didn't, surprises>

## Recommended follow-ups
- /tweak <plan> -- <issue 1>
- /tweak <plan> -- <issue 2>
```

## Hard rules

- **Don't edit code.** Verification is read-only. If something's broken, recommend `/tweak`. Don't fix it inline.
- **Don't trust narrative over execution.** If a task says "tests pass" but you couldn't reproduce that, it's FAIL until proven otherwise.
- **Don't grade on a curve.** PARTIAL and FAIL are useful signals - the user wants the truth, not validation.
- **Cite evidence.** Every PASS claim points to a file:line, a test result, or a captured command output.
