---
description: Fix something noticed after plan execution - diagnose, patch, log under "Post-Build Refinements" in the plan.
argument-hint: <plan filename> -- <what's wrong, in plain English>
---

The user noticed something off after `/execute` finished. Your job: diagnose, fix it minimally, and log the change under the plan's **Post-Build Refinements** section. The user input is: $ARGUMENTS

Style note: any text you write to the user or to the plan should follow [AGENTS.md](../../AGENTS.md) - dashes (`-`), no emdashes, talk like a person.

## Step 1 - Parse

Expect input shaped like `<plan-filename> -- <description of the issue>`. If the plan filename is missing or ambiguous, list `.claude/plans/*.md` and ask. If the issue description is vague, ask one clarifying question (with a concrete example of what info you need) before doing anything else.

## Step 2 - Read the plan + relevant code

1. Open the plan file.
2. Re-read [AGENTS.md](../../AGENTS.md) and [ARCHITECTURE.md](../../ARCHITECTURE.md) so the fix stays in style.
3. Locate the code paths the issue touches. Use the original plan's task list and dependency graph as a map - figure out which task(s) own the affected code.
4. Reproduce the issue if at all possible. Don't fix what you can't reproduce - ask the user for repro steps if you can't trigger it.

## Step 3 - Fix

- **Minimal diff.** Fix the actual problem. Don't refactor surroundings, rename things, or "improve" untouched code. AGENTS.md is explicit about this.
- **Root-cause, not symptom.** If the LLM is hallucinating, don't add a regex band-aid - fix the prompt or the schema. If a test is flaky, find out why. A band-aid that masks a single bad flag is worse than the flag itself - it hides a class of errors and silently lets the same failure mode reach a judge next time. Prefer prompt or schema fixes over post-hoc filtering.
- **Tests come with the fix.** Add a regression test that fails before your change and passes after. If the fix is purely a prompt change, add an eval case that catches the regression.
- **No scope creep.** If you find adjacent issues, write them down in step 4. Don't fix them in this tweak.

## Step 4 - Log under Post-Build Refinements

Append (don't overwrite) to the plan's `## 6. Post-Build Refinements` section, in this format:

```markdown
### R<N> - <one-line title> (<YYYY-MM-DD>)

**Issue:** <what the user reported, in their own words>
**Root cause:** <what was actually wrong>
**Fix:** <what you changed, in 2-4 bullets>
**Files touched:** <list>
**Regression test:** <test name + path + command to run it>
**Adjacent issues spotted (not fixed):** <list, or "none">
```

Number refinements R1, R2, R3... by reading the existing section and incrementing.

## Step 5 - Report

Print to the user:
- The R<N> entry you just appended (verbatim)
- Test command output proving the fix works (last 20 lines)
- Any adjacent issues you noticed and chose not to fix

If the user runs `/tweak` again on the same plan, you'll just append R<N+1>. The plan stays the source of truth for what was built and what was patched.
