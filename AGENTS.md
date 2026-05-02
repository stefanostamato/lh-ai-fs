# AGENTS.md

Operating rules for any agent (human or AI) writing code in this repo. Read this before [ARCHITECTURE.md](ARCHITECTURE.md), then read both before touching code.

The project is a take-home challenge: build an AI pipeline that detects BS in a legal Motion for Summary Judgment by verifying its citations, quotes, and factual claims against source documents. See [README.md](README.md) for the full brief.

## Writing style across the project

These rules apply to every file you write - code comments, command files, docs, notes, commit messages, all of it.

- Write like a person talks. Short sentences. Contractions are fine.
- No business-speak. No "leverage", "utilize", "in order to", "best-in-class". Just say what you mean.
- Use dashes (`-`), never emdashes (`--`). This is a hard rule.
- Audience is experienced engineers. You don't need to over-explain. You also don't need to show off.
- Friendly and a bit fun. Dry humor lands better than corporate cheer.
- Use mermaid diagrams when shape matters more than prose - pipelines, agent graphs, decision trees.

## 1. Spirit of the project

These principles come from the README and from Learned Hand's product DNA (impartial, verifiable, judge-supports-not-replaces). They override convenience.

1. **Trust is the currency. Catch real flaws, don't invent them.** Precision matters more than recall *because* one false flag erodes confidence in every other finding the system has produced. A pipeline that flags 3 real flaws is stronger than one that flags 10 with 4 false positives. The court that catches us hallucinating once stops trusting the other 99 findings.
2. **Express uncertainty. Watch for sycophancy across our own agents.** "Could not verify" beats a fabricated finding. Every flag carries confidence and reasoning. Hallucination is one named failure mode we're graded on - sycophancy is the other: a downstream agent must not strengthen claims an upstream agent marked uncertain. Verifier doesn't trust extractor by default; memo writer cannot upgrade a verdict the verifier left as `unverified`.
3. **Surface, don't decide.** Every flag is a question for a judge with evidence attached. The system flags; it doesn't rule. No agent prompt is allowed to use advocate voice or recommend an outcome. The output is a clerk's note, not a brief.
4. **Every finding cites back to the source.** A judge has to be able to jump from any flag into the document in one click. That means a `TextSpan` (document id + offsets) on every finding type, not just citations. A flag without a span is malformed.
5. **Impartiality.** Treat both sides' claims with equal skepticism. No prompt may bias toward plaintiff or defense. Eval cases should be symmetric where the data allows - if we flag a misquote on one side, we should be ready to flag the same misquote on the other.
6. **Structured data between agents.** No raw text blobs flowing between stages. Use Pydantic models. The shape of inter-agent payloads is part of the design, treat it as such.
7. **Decomposition matters.** Agents have distinct, non-overlapping roles. If two agents do the same thing, or one agent does two unrelated things, redesign.
8. **Eval-first thinking.** A flag without a test that it matters is decoration. Build the eval harness alongside the pipeline, not after.
9. **Honest measurement.** A 60% recall number we trust is more valuable than a 100% number from a cherry-picked test. Report what's actually there.
10. **Prompts are code.** They live in version control, get reviewed, and have tests. Prompt changes get evaluated, not vibes-checked.

## 2. Code-writing rules

Non-negotiable. They mirror what's already in this codebase and extend it.

### Scope discipline
- **Solve the asked problem, nothing more.** No surrounding refactors, no "while I'm here" cleanups, no abstractions for hypothetical futures.
- **Three similar lines beats a premature abstraction.** Wait for the fourth before extracting.
- **Delete unused code immediately.** No commented-out blocks, no `_unused` renames, no "may need this later" stubs.

### Comments
- **Default to none.** Code with named identifiers is self-describing.
- **Write a comment only when the *why* is non-obvious** - a hidden constraint, a workaround for a specific bug, a subtle invariant.
- **Never describe *what* the code does.** That's what the code is for. Never reference tasks, tickets, or callers ("for the X flow", "fixes #123") - those rot.

### Error handling
- **Validate at boundaries** (HTTP request, LLM response, file load) and trust internal code afterward.
- **Don't add try/except for things that can't fail.** It hides real bugs.
- **LLM failures are first-class outcomes.** Malformed JSON, refusals, timeouts, empty responses - these flow into the report as "could not verify", not as exceptions that crash the request.

### Tests
- **TDD when building agents and pipelines.** Write the failing test first, watch it fail, implement, watch it pass.
- **Tests round-trip real schemas.** Don't mock Pydantic models, construct them.
- **LLM calls in unit tests are mocked at the `llm.py` boundary**, not deeper. Use the `mock_llm` fixture from `tests/conftest.py` (see section 3 for the contract). Never reach into `openai.*`.
- **Eval cases are not unit tests.** Unit tests assert code behavior with mocked LLM responses. Eval cases assert system quality with real LLM calls on labeled data. Both exist, in different files, and are run by different commands.

## 3. Python / FastAPI / OpenAI conventions

The existing code already follows these. Don't break them.

### Style
- **Python 3.11+.** Use `dict[str, str]`, `list[X]`, `X | None`. Never `Dict`, `List`, `Optional`.
- **Type-hint every function signature.** Public functions return concrete types, never `Any` unless genuinely unknowable.
- **Module-level constants are `UPPER_SNAKE`.** Functions and variables are `lower_snake`.
- **Imports:** stdlib, then third-party, then local, separated by blank lines.

### FastAPI
- **One router, one concern.** If `main.py` grows past ~100 lines, extract routers under `backend/routers/`.
- **Pydantic models for every request and response body.** Even when it's "just" `{"report": ...}`, define the model. The shape is the API.
- **Async handlers** for anything calling the LLM. Use `AsyncOpenAI`. Don't block the event loop with sync calls inside `async def`.
- **CORS is wired** in [main.py](backend/main.py). Extend `allow_origins` if you add a new dev origin, don't replace it.

### Pydantic
- **`model_config = {"extra": "forbid"}`** on inter-agent contracts so silent field drift fails loud in tests.
- **`Field(description=...)`** liberally - these descriptions become the prompt's structured-output schema when you pass models to OpenAI's `response_format`.
- **`Literal[...]` over enums** for small fixed sets that show up in JSON.
- **Validate at construction time.** If a value can't be empty, declare it. Don't check downstream.

### OpenAI / LLM calls
- **Every call goes through [llm.py](backend/llm.py).** Don't import `OpenAI` anywhere else. This is the seam for retries, structured output, and test mocking.
- **Prefer `response_format={"type": "json_schema", ...}`** over "parse this JSON out of prose". Pass the Pydantic schema. The model that says it returned JSON sometimes lied; structured-output mode does not.
- **Default `temperature=0`** for verification work. Determinism beats creativity here.
- **Always set a timeout.** Cite-check loops that hang are worse than ones that fail.
- **Log token usage** in dev. Token cost is a metric, not a footnote.
- **Retries: at most one, on the parse step, not the whole request.** If the model returned malformed JSON once, ask it to fix that specific JSON. Don't re-run the whole prompt.

### Test fixtures
The `mock_llm` fixture is the contract for unit tests:

```python
# backend/tests/conftest.py
@pytest.fixture
def mock_llm(monkeypatch):
    """Patches llm.call_llm. Pass a dict of {prompt_substring: response_payload}
    or a callable. Asserts the patched function was called at least once."""
    ...
```

Tests use `mock_llm({"extract citations": citation_json_payload})` - keyed by something distinctive in the prompt so the same fixture handles multi-step tests. Never patch `openai.*` directly.

### Prompt hygiene
- **Prompts live in `.py` files** as constants or template functions. Not YAML, not inline in handlers. Version-controlled, importable, testable.
- **Each agent's prompt declares: role, inputs, output schema, what counts as uncertainty.** No "be helpful" filler.
- **Voice is a clerk writing for a judge, not an advocate.** Prompts must forbid recommending an outcome, picking a side, or upgrading a verdict beyond what the evidence supports. Report what you can verify *and* what you can't, symmetrically. "Find problems" is the wrong frame - the right frame is "report what the source does and doesn't support."
- **Few-shot examples come from real labeled cases** (your eval data), not invented ones. Where possible, pair examples symmetrically across plaintiff and defense claims so the model doesn't learn one-sided suspicion.
- **Never tell the model "don't hallucinate".** Tell it "if you cannot verify X from the provided source, return `verdict: 'unverified'` with a `reason` field, and a `span` pointing at the part of the source you checked."

## 4. React / Vite / JS conventions

The frontend is intentionally tiny. Keep it that way unless the report visualization genuinely needs more.

- **Functional components + hooks.** No class components.
- **No styling framework yet.** Inline styles are fine for this volume. If component count goes past ~5, introduce a single `styles.css` or component-level modules. Don't reach for Tailwind.
- **One concern per component.** A component that fetches AND renders AND handles errors is three components.
- **API client lives in `src/api/`.** No raw `fetch()` in components past the trivial case in [App.jsx](frontend/src/App.jsx). Centralize once there's more than one endpoint.
- **No state libraries** (Redux, Zustand). `useState` is enough for one screen.
- **`vite.config.js` port is 5175.** Don't change it - backend CORS is wired to it.

## 5. File and module layout

Follow [ARCHITECTURE.md](ARCHITECTURE.md). When in doubt:

- New agent goes in `backend/agents/<name>.py`, one agent per file, exporting a single function or callable class.
- New schema goes in `backend/schemas.py` (or split into `schemas/` if it grows).
- New LLM helper extends [backend/llm.py](backend/llm.py), don't make a parallel module.
- New eval case goes in `backend/evals/cases/` (one file per labeled case). The runner is `backend/evals/run.py`.

## 6. Working with subagents (for `/execute`)

When this file is read by a subagent spawned by `/execute`:

- You have one task, defined in the plan. Don't touch files outside its declared list.
- TDD: failing test, then minimum implementation, then green, then refactor.
- Stop when done. No "while I'm here" anything.
- Report back: files changed, test command output (last 20 lines), any acceptance criteria you couldn't satisfy with the reason.
- If the task is impossible as specified, stop and explain. Don't improvise around the spec.

## 7. What "done" means

A task is done when:
1. Every acceptance criterion in the plan is checked off with evidence.
2. Tests pass via the documented command.
3. No code outside the task's declared files was changed.
4. The diff would survive a code review by a senior engineer who's allergic to over-engineering.

If any of those is false, it's not done. It's almost done, which is not the same thing.
