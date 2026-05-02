# BS Detector - Stefano's submission

A multi-agent pipeline that audits a Motion for Summary Judgment for fabricated citations, doctored quotes, and facts that contradict the supporting record. Built for the Learned Hand take-home challenge.

The original challenge brief is preserved in [BRIEF.md](BRIEF.md). This README is for the reviewer.

## Quickstart

```bash
cp .env.example .env      # add OPENAI_API_KEY
docker compose up --build
```

Open the UI at [http://localhost:5175](http://localhost:5175) and click **Analyze**. The API is at [http://localhost:8002](http://localhost:8002) (`POST /analyze`, no body - loads the Rivera case from `backend/documents/`). Both services hot-reload.

Manual setup is documented in [SETUP.md](SETUP.md) if Docker is inconvenient.

## What was built

Legend: ✅ shipped and solid. ⚠️ shipped but with a caveat I'd want to fix - see notes.

| Tier | Requirement | Status | Where to look / caveat |
|---|---|---|---|
| 1 | Extract citations from the MSJ | ✅ | [agents/citation_extractor.py](backend/agents/citation_extractor.py) |
| 1 | Verify each citation against authority | ✅ | [agents/citation_verifier.py](backend/agents/citation_verifier.py) + [agents/tools/](backend/agents/tools) (CourtListener) |
| 1 | Flag direct quotes for accuracy | ⚠️ | folded into the verifier as `quoted_text_match` rather than a dedicated agent. Works, but the citation pillar overall is rougher than the rest - see [REFLECTIONS.md](REFLECTIONS.md) |
| 1 | Structured JSON output | ✅ | [schemas.py](backend/schemas.py) - `Report` is the response shape |
| 2 | Eval harness, single command | ⚠️ | `cd backend && python -m evals.run`. One labeled case (Rivera) - recall = 1.0 is suspect until a second held-out case is added. See [evals/README.md](backend/evals/README.md) |
| 2 | Precision, recall, hallucination rate | ✅ | [evals/metrics.py](backend/evals/metrics.py) (+ cost & latency). Hallucination rate is structural, not LLM-judged |
| 2 | Cross-doc consistency check | ✅ | [agents/crossdoc_checker.py](backend/agents/crossdoc_checker.py) |
| 2 | Express uncertainty | ✅ | `could_not_verify` is a first-class verdict, distinct from `unsupported` |
| 2 | Structured data between agents | ✅ | Pydantic with `extra: "forbid"` everywhere - see `schemas.py` |
| 3 | ≥ 4 distinct agents | ✅ | 7 modules in [agents/](backend/agents) - 5 LLM specialists (citation extractor, citation verifier, claim extractor, cross-doc checker, memo writer) + 2 deterministic parsers (brief, record) |
| 3 | Confidence scoring with reasoning | ⚠️ | `confidence: float` + `reasoning: str` on every finding, but uncalibrated. No Brier score or reliability diagram yet - the numbers are a vibe |
| 3 | Judicial memo agent | ✅ | [agents/memo_writer.py](backend/agents/memo_writer.py) - clerk voice, may not upgrade verdicts |
| 3 | Graceful failure orchestration | ⚠️ | `_safe_call` in [pipeline.py](backend/pipeline.py) catches agent crashes as `could_not_verify` + `partial_failures`. Retry/backoff for OpenAI 429s was added reactively after eval failures, not by design |
| 3 | Structured UI | ✅ | [frontend/src/](frontend/src) - `ReportView` + `FlagCard` |
| 3 | Reflection doc | ✅ | [REFLECTIONS.md](REFLECTIONS.md) |

## Reviewer's map (your evaluation criteria)

> 1. **How you decompose the problem into agents** → architecture diagram and rationale in [ARCHITECTURE.md §3.1-§3.3](ARCHITECTURE.md#31-the-pipeline-at-a-glance). Decomposition emerged from the four BS categories I found in the manual case-file analysis ([NOTES.md stage 2](NOTES.md)) - see [REFLECTIONS.md "Specialist agents over a generalist"](REFLECTIONS.md).
>
> 2. **How precisely you write prompts** → prompts live as Python constants in [backend/agents/prompts/](backend/agents/prompts). Each declares role, inputs, output schema, and what counts as uncertainty. Voice is "clerk for a judge", never advocate. Prompt rules are codified in [AGENTS.md §3 "Prompt hygiene"](AGENTS.md).
>
> 3. **Quality of eval approach** → [backend/evals/README.md](backend/evals/README.md) covers what the metrics measure and why. Notable: hallucination rate is structural (excerpt-must-appear-in-source), not LLM-judged. Gold labels live in [evals/cases/](backend/evals/cases). Run history in [evals/history.jsonl](backend/evals/history.jsonl).
>
> 4. **How far through the spec** → table above. All of Tier 1, all of Tier 2, all Tier 3 items.
>
> 5. **How honest the reflection is** → [REFLECTIONS.md](REFLECTIONS.md). Calls out: precision was the bottleneck (~85% then 73% after one round of false-positive fixes), recall = 1.0 is suspicious and probably overfit, citation pillar shipped rougher than the rest, confidence scores are uncalibrated.

## Run the evals

```bash
cd backend && python -m evals.run
```

Hits the live OpenAI and CourtListener APIs. Each case fires the full pipeline (~10-15 LLM calls). Prints per-case + aggregate `precision`, `recall`, `hallucination_rate`, `cost_usd`, `latency_ms`. Exits non-zero if `recall < 0.6` or `hallucination_rate > 0.1`.

For machine-readable output: `python -m evals.run --report-out /tmp/eval.json`. Schema in [evals/report.py](backend/evals/report.py).

Adding cases, matching rules, and what's intentionally *not* measured: [backend/evals/README.md](backend/evals/README.md).

## Run the unit tests

```bash
cd backend && pytest
```

Unit tests mock at the `llm.py` boundary via the `mock_llm` fixture and never hit the network. Eval is the only place real LLMs run.

## Citation lookup - one network detail

The citation pillar checks each cite against [CourtListener](https://www.courtlistener.com)'s free v4 search endpoint. No API key. We post-filter results by exact normalized cite match because the older v3 single-shot `citation-lookup` endpoint now requires auth. A cite CourtListener can't find lands as `lookup_status: "not_found"` and the verifier short-circuits to `could_not_verify` with no fabricated holding text - that's how fabricated cites get caught. `python -m evals.run` requires internet; unit tests use the `mock_lookup` fixture and don't.

## A note on `.claude/`

Committed on purpose. The brief said "use everything, we want to see how you use it", so the workflow is in the repo:

- [.claude/commands/](.claude/commands) - `/plan`, `/execute`, `/verify`, `/tweak`, `/eval`, `/write-notes`. The orchestrator for `/execute` runs each task's tests itself before marking it done; the subagent's narrative isn't proof.
- [.claude/plans/](.claude/plans) - the actual plans I executed. Spec, task graph, per-task subagent prompts with TDD, post-build refinements log.

## Reading order

1. **[REFLECTIONS.md](REFLECTIONS.md)** - design decisions, what worked, what didn't, what I'd do differently. The reviewer-facing synthesis.
2. **[ARCHITECTURE.md](ARCHITECTURE.md)** - pipeline shape, agent boundaries, schema contracts, what's deliberately *not* here.
3. **[AGENTS.md](AGENTS.md)** - operating rules I held myself to (and held the subagents to).
4. **[NOTES.md](NOTES.md)** - running diary. Raw, ordered by time, has the play-by-play.
