# ARCHITECTURE.md

How this repo is structured, where things go, and the patterns to follow when extending it. Read [AGENTS.md](AGENTS.md) first - this doc assumes those rules, including the writing style (no emdashes, talk like a person, mermaid for shape).

## 1. What exists today

The starting point is intentionally minimal:

```
lh-ai-fs/
├── README.md                  # Challenge brief
├── AGENTS.md                  # Code rules
├── ARCHITECTURE.md            # This file
├── NOTES.md                   # Stefano's running diary (gets fed by /write-notes)
├── docker-compose.yml         # Two services, hot-reload via volume mounts
├── .env.example               # OPENAI_API_KEY=...
├── .claude/                   # Slash commands and plans (committed - see README)
│   ├── commands/              # /plan, /execute, /tweak, /verify, /eval, /write-notes
│   └── plans/                 # Execution plans written by /plan
│
├── backend/                   # Python 3.11 + FastAPI on :8002
│   ├── Dockerfile
│   ├── entrypoint.sh          # pip install + uvicorn --reload
│   ├── requirements.txt       # fastapi, uvicorn, openai, python-dotenv
│   ├── main.py                # FastAPI app + POST /analyze (currently a TODO stub)
│   ├── llm.py                 # OpenAI client wrapper - the single seam for all LLM calls
│   └── documents/             # The four case-file documents
│       ├── motion_for_summary_judgment.txt
│       ├── police_report.txt
│       ├── medical_records_excerpt.txt
│       └── witness_statement.txt
│
└── frontend/                  # React 18 + Vite on :5175
    ├── Dockerfile
    ├── entrypoint.sh          # npm install + npm run dev
    ├── package.json
    ├── vite.config.js
    ├── index.html
    └── src/
        ├── main.jsx
        └── App.jsx            # Single-screen client: button -> POST /analyze -> render JSON
```

Runtime facts to keep in mind:
- Backend on `http://localhost:8002`, frontend on `http://localhost:5175`. CORS in [backend/main.py](backend/main.py) is wired to that exact frontend origin.
- `docker compose up --build` is the canonical run command. Both services hot-reload from host volume mounts.
- `OPENAI_API_KEY` is read from `.env` at the repo root (compose) or `backend/.env` (manual).
- `/analyze` already loads all `.txt` files in `backend/documents/` into a `dict[str, str]` keyed by stem. That's the pipeline's input.

## 2. Where new code goes

The layout below assumes a multi-agent pipeline. You don't need to create empty folders, but when you create a file of a given type, this is where it goes.

```
backend/
├── main.py                   # FastAPI routes only. Thin. No business logic.
├── llm.py                    # Single LLM client wrapper (sync + async). The ONLY place importing `openai`.
├── schemas.py                # Pydantic models for every cross-agent payload + the API response.
│                             # Split into schemas/<topic>.py if it grows past ~300 LOC.
│
├── agents/                   # One agent per file. Each exports a single callable.
│   ├── __init__.py
│   ├── brief_parser.py       # Deterministic, no LLM. Segments the brief into paragraphs with offsets.
│   ├── record_parser.py      # Deterministic, no LLM. Same job for each record document.
│   ├── citation_extractor.py
│   ├── claim_extractor.py
│   ├── citation_verifier.py  # Also handles quote-accuracy - if a cite carries a verbatim quote, the verifier checks the source.
│   ├── cross_doc_checker.py  # Consistency across the brief and the records.
│   └── memo_writer.py        # Judicial memo. Clerk's voice, not advocate. Cannot upgrade verdicts.
│
├── pipeline.py               # Orchestrator. Wires agents together. Owns the dependency graph.
│                             # Knows about agents; agents do not know about it.
│
├── documents/                # Case file source documents (existing). Read by the pipeline.
│
├── evals/
│   ├── run.py                # Single-command entry point: `python -m evals.run`
│   ├── metrics.py            # Precision / recall / hallucination rate
│   ├── cases/                # Labeled gold-standard cases - one file per case
│   │   ├── citation_smith_v_jones.json
│   │   └── ...
│   ├── history.jsonl         # Append-only log of run results, used to spot regressions
│   └── README.md             # How to run, how to interpret, how to add cases
│
├── tests/
│   ├── conftest.py           # `mock_llm` fixture (the only sanctioned way to mock LLM calls)
│   ├── test_schemas.py       # Pydantic round-trips + validation rules
│   ├── test_agents/          # One test file per agent, mocks the llm.py boundary
│   └── test_pipeline.py      # End-to-end with mocked LLM
│
└── .env.example              # Existing
```

```
frontend/src/
├── main.jsx                  # Existing entry point
├── App.jsx                   # Top-level layout + route to report view
├── api/
│   └── analyze.js            # fetch wrapper for POST /analyze
└── components/               # When the UI grows past App.jsx, and only then
    ├── ReportView.jsx
    ├── FlagCard.jsx          # One flag (citation, quote, fact) with verdict + confidence + reasoning
    └── MemoSection.jsx
```

## 3. Architectural patterns

### 3.1 The pipeline at a glance

```mermaid
flowchart LR
    api[POST /analyze<br/>no body] --> loader[case_loader<br/>load_default_case]
    loader -->|DocumentSet| pipe[run_pipeline]
    pipe --> bp[Brief Parser<br/>deterministic]
    pipe --> rp[Record Parser<br/>deterministic, per record]
    bp -->|ParsedBrief| ce[Citation Extractor]
    bp -->|ParsedBrief| cle[Claim Extractor]
    ce -->|ExtractedCitation per cite| cv[Citation Verifier<br/>uses CaseLawLookup]
    cle -->|ExtractedClaim per claim| cdc[Cross-Doc Checker]
    rp -->|ParsedRecord per doc| cdc
    cl[CaseLawLookup<br/>+ ParametricLLMLookup] -.->|injected| cv
    cv -->|CitationFinding[]| rank[rank_findings<br/>deterministic]
    cdc -->|ConsistencyFinding[]| rank
    rank -->|top-N FindingRef[]| memo[Judicial Memo]
    rank --> assemble
    memo --> assemble[Report assembly<br/>in pipeline.py]
    assemble -->|Report| api
    api --> ui[Frontend ReportView]
    api --> eval[Eval harness<br/>builds DocumentSet from case JSON]
```

Each solid box is an agent module or a deterministic helper. Each arrow is a typed Pydantic payload. The dashed arrow is dependency injection - `CaseLawLookup` is an interface the verifier holds a reference to. Quote-accuracy is not its own agent: when a citation carries a verbatim quoted span, the Citation Verifier checks the lookup result against it as part of its verdict.

### 3.2 The pipeline is a function, not a framework

`backend/pipeline.py` exposes one async function: `run_pipeline(doc_set: DocumentSet) -> Report`. It composes agent calls. It does not implement a generic agent framework, a DAG executor, or a plugin system. We're shipping one pipeline, not a platform.

```python
# Sketch - actual signatures live in schemas.py
async def run_pipeline(doc_set: DocumentSet) -> Report:
    parsed_brief = parse_brief(doc_set.brief())
    parsed_records = [parse_record(r) for r in doc_set.records()]
    citations, claims = await asyncio.gather(
        extract_citations(parsed_brief),
        extract_claims(parsed_brief),
    )
    citation_findings, consistency_findings = await asyncio.gather(
        asyncio.gather(*[verify_citation(c, lookup) for c in citations]),
        asyncio.gather(*[check_claim(cl, parsed_records) for cl in claims]),
    )
    top = rank_findings(citation_findings, consistency_findings, n=5)
    memo = await write_memo(top, partial_failures)
    return Report(...)
```

Parallelism comes from `asyncio.gather` plus a single `asyncio.Semaphore(5)` cap on in-flight LLM calls. Not a custom scheduler. If the pipeline ever gets complex enough that this hurts, *then* introduce structure.

### 3.3 Agents are pure-ish functions of typed inputs to typed outputs

Each agent module defines:
- A **prompt constant or builder function** so it's testable and version-controlled.
- One **public function** that takes Pydantic input(s) and returns a Pydantic output.
- No file I/O. No HTTP. No knowledge of FastAPI or the pipeline. The only side effect is calling `llm.py`.

This is the seam that makes evals possible: `verify_citation(citation, source_text)` runs identically from a test, the pipeline, or the eval suite.

### 3.4 `llm.py` is the only place that imports `openai`

Every LLM call goes through `llm.py`. That gives us one place to:
- Swap models or add a fallback
- Add structured-output via `response_format` with a Pydantic schema
- Add retry-on-malformed-JSON
- Add token logging
- Mock in tests (via the `mock_llm` fixture, which patches `llm.call_llm`)

If you find yourself importing `openai` in an agent, stop and add the helper to `llm.py` instead.

### 3.5 Inter-agent contracts are Pydantic, not dicts

`schemas.py` is load-bearing. Every payload that crosses an agent boundary is a Pydantic model with `model_config = ConfigDict(extra="forbid")`. The locked schemas:

```python
Verdict = Literal["supported", "contradicted", "unsupported", "could_not_verify"]
DocumentId = str  # type alias for clarity at call sites; no value constraint -
                  # the active DocumentSet is the source of truth

class DocumentRole(str, Enum):
    BRIEF = "brief"            # the document being audited
    RECORD = "record"          # supporting documents the brief is checked against

class DocumentInput(BaseModel):
    document_id: DocumentId    # caller-chosen, unique within the set
    display_name: str          # what the UI shows ("Police Report")
    role: DocumentRole
    text: str

class DocumentSet(BaseModel):
    """Pipeline input envelope. Exactly one BRIEF, N RECORDs, unique ids."""
    documents: list[DocumentInput]
    # @model_validator enforces exactly-one-brief and unique-ids
    def brief(self) -> DocumentInput: ...
    def records(self) -> list[DocumentInput]: ...
    def by_id(self, document_id: DocumentId) -> DocumentInput: ...
    def has_id(self, document_id: DocumentId) -> bool: ...

class TextSpan(BaseModel):
    document_id: DocumentId    # must reference a doc in the active DocumentSet
    start: int                 # char offset, inclusive
    end: int                   # char offset, exclusive
    excerpt: str               # the text at [start:end], stored for UI rendering

class ParagraphSpan(BaseModel):
    document_id: DocumentId
    paragraph_index: int       # 0-based
    span: TextSpan

class ParsedBrief(BaseModel):
    document_id: DocumentId
    raw_text: str
    paragraphs: list[ParagraphSpan]

class ParsedRecord(BaseModel):
    document_id: DocumentId
    display_name: str          # carried forward so cross-doc + UI don't re-derive
    raw_text: str
    paragraphs: list[ParagraphSpan]

class ExtractedCitation(BaseModel):
    cite: str                          # "Privette v. Superior Court, 5 Cal.4th 689 (1993)"
    proposition: str                   # what the brief claims this authority supports
    quoted_language: str | None        # verbatim quote attributed to the case, if any
    claim_span: TextSpan               # where in the brief this citation appears

class ExtractedClaim(BaseModel):
    claim_text: str                    # "the incident occurred on March 14, 2021"
    claim_span: TextSpan               # where in the brief this claim appears

class CaseLookupResult(BaseModel):
    found: bool
    canonical_cite: str | None
    holding_text: str | None
    quoted_text_match: bool | None     # null = no quote to check; bool = match result
    source_url: str | None
    lookup_status: Literal["found", "not_found", "ambiguous", "lookup_failed"]
    notes: str | None

class CitationFinding(BaseModel):
    citation: ExtractedCitation
    verdict: Verdict
    confidence: float                  # [0.0, 1.0]
    reasoning: str
    evidence_quote: str | None
    evidence_span: TextSpan | None     # null when verdict is could_not_verify
    lookup: CaseLookupResult           # provenance: where the verifier looked

class ConsistencyFinding(BaseModel):
    claim: ExtractedClaim
    verdict: Verdict
    confidence: float
    reasoning: str
    evidence_quote: str | None
    evidence_span: TextSpan | None     # null when verdict is could_not_verify
    checked_documents: list[DocumentId]

class FindingRef(BaseModel):
    finding_type: Literal["citation", "consistency"]
    finding_index: int                 # index into report.citations or report.consistency

class JudicialMemo(BaseModel):
    text: str                          # one paragraph, clerk voice
    top_findings: list[FindingRef]     # references only; never restates verdicts
    partial_failure_note: str | None   # "Citation verification failed for 2 of 10 cites." etc.

class PartialFailure(BaseModel):
    agent: str
    error: str

class TokenUsage(BaseModel):
    prompt: int
    completion: int

class ReportMeta(BaseModel):
    model: str
    elapsed_ms: int
    token_usage: TokenUsage
    partial_failures: list[PartialFailure]

class Report(BaseModel):
    consistency: list[ConsistencyFinding]
    citations: list[CitationFinding]
    memo: JudicialMemo
    meta: ReportMeta
```

**Verdict vocab is exactly four values.** `supported` and `contradicted` are the affirmative outcomes. `unsupported` means the source exists but doesn't say what the brief claims. `could_not_verify` means the verifier couldn't reach the source at all - lookup failed, ambiguous result, agent crash. The split matters: `unsupported` is a finding (the brief is wrong about its source), `could_not_verify` is a confession (we can't tell). The memo treats them differently and so should the UI.

**`DocumentId` is `str`, not a `Literal`.** The schema is agnostic to which documents exist - the active `DocumentSet` is the runtime source of truth. A `TextSpan` whose `document_id` doesn't match any document in the set is caught at the orchestrator boundary (`_safe_call` in `pipeline.py`) and converted to a `could_not_verify` finding rather than a Pydantic error. The agent hallucinated a doc; that's a finding, not a crash. See §3.11 for the full `DocumentSet` story.

**Invariant: every finding is traceable to a source range.** Each finding type carries a `claim_span` (where in the brief the claim lives) plus (when there's evidence) an `evidence_span` pointing at the supporting or contradicting text. A finding with `verdict != "could_not_verify"` and no `evidence_span` is malformed - the UI's job is to let a judge click through to the document, and we can't do that without offsets.

Field order in finding models is deliberate: claim location first, then evidence, then verdict, then reasoning, then confidence. That's the order a judge scans. Schemas are read top-down; we mirror the human reading order.

Two consequences:
- The `/analyze` response schema is `Report.model_json_schema()`. One source of truth.
- Adding a field is a deliberate act. Update the schema, update the producing agent, update the consuming renderer. No silent dict-key drift.

### 3.6 Confidence, uncertainty, and verifiability are first-class

Every verdict carries a `confidence: float`, a `reasoning: str`, and a `TextSpan` that pins the claim to the source. `could_not_verify` is a real verdict, not an error - and `unsupported` (source exists, doesn't say what the brief claims) is distinct from `could_not_verify` (we couldn't reach the source). The eval suite measures hallucination rate by counting findings that don't have evidence in the source, so an agent that *can't* find evidence has to say so, not invent it.

A finding with `verdict != "could_not_verify"` and no `evidence_span` is malformed - validation should reject it. The whole product premise (a judge trusts this because they can verify it in seconds) collapses if we let findings float free of the document.

### 3.6.1 Sycophancy across our own agents

Sycophancy is one of the BS modes named in the brief - and it's a risk *inside* our pipeline, not just in the documents we analyze. Two design rules to prevent it:

- **Downstream agents cannot upgrade upstream verdicts.** The memo writer sees `could_not_verify` or `unsupported` and may not turn it into `contradicted` in the prose. Verdict promotion is a schema-level invariant - assert it in tests.
- **Verifier agents do not see the extractor's confidence.** They re-derive their own confidence from the source. If we pipe upstream confidence forward, downstream agents anchor on it and the chain agrees with itself by default.

### 3.6.2 Impartiality

Learned Hand sits between adversaries. A pipeline that systematically over-flags one side erodes trust faster than missing flaws. The discipline is in two places:

- **Prompts treat both sides symmetrically.** No prompt mentions plaintiff or defense by name. Prompts say "the moving party's claim" and "the opposing record" - role labels, not parties.
- **Eval cases are paired where the data allows.** If we have a case where the MSJ misquotes a source, we want a paired case where a response brief misquotes a source. Aggregate metrics can hide one-sided suspicion; per-side breakdowns surface it.

For this take-home, the dataset is one MSJ - so impartiality is mostly a prompt-design discipline, not a measured metric. Note this as a design intent in the plan and call it out as a limitation in the reflection.

### 3.7 Evals are code, not vibes

`backend/evals/run.py` is a single-command entry point. The README mentions both `python -m evals.run` and `python run_evals.py` - pick one in the plan and document it. The runner:
1. Loads labeled cases from `evals/cases/`.
2. Runs the pipeline (or a single agent) on each, hitting real LLM APIs.
3. Computes precision, recall, hallucination rate via `evals/metrics.py`.
4. Prints a structured summary, per-case and aggregate.
5. Appends one line to `evals/history.jsonl` so future runs can diff.
6. Exits non-zero if metrics regress past a configured threshold, so it's CI-friendly.

`/eval` is the slash command that drives this. It does not mock - eval is the only place we use real LLMs in the test loop.

Eval cases are versioned JSON: input documents (or pointers to them), expected findings, and expected non-findings (negative cases matter for precision).

### 3.8 Test fixtures live in `tests/conftest.py`

Unit tests mock LLM calls via a single `mock_llm` fixture that patches `llm.call_llm` (and the async variant). Tests never reach into `openai.*` directly. Sketch:

```python
def test_extracts_citations(mock_llm):
    mock_llm({"extract citations": fixture_citations_json})
    result = await extract_citations("...motion text...")
    assert len(result) == 4
```

The fixture is keyed by a substring of the prompt so a single test can serve a multi-step pipeline call.

### 3.9 Frontend stays simple until it can't

`App.jsx` is one screen. When the report grows past a JSON dump:
1. Extract `ReportView` and per-section components into `src/components/`.
2. Move the `fetch` to `src/api/analyze.js`.
3. *Then* consider whether anything else (state library, router, styling system) is justified. For a single-screen tool, it usually isn't.

### 3.10 `rank_findings` is a deterministic helper, not an agent

The memo writer doesn't see every finding - that's how you get prose that lists ten things and emphasizes none. `rank_findings` is a pure-Python function in `pipeline.py` (not an agent, no LLM call) that takes the full list of citation and consistency findings and returns the top-N as `FindingRef`s for the memo writer.

The ranking is `verdict_weight × confidence`, where `verdict_weight` orders the verdict vocab roughly by "how alarming is this to a judge": `contradicted` > `unsupported` > `could_not_verify` > `supported`. Within a verdict bucket, higher confidence wins. N is a constant (currently 5) at the top of `pipeline.py`, not a config knob.

Why not an agent: ranking is a deterministic policy, not a judgment call. Pushing it into an LLM would add cost, latency, and a non-reproducible step for no upside. The memo writer still chooses *what to say* about the top-N - that's the LLM's job. Picking the top-N is arithmetic.

### 3.11 The `DocumentSet` abstraction

The pipeline takes a `DocumentSet`, not a `dict[str, str]`. A `DocumentSet` is one BRIEF + N RECORDs, validated at construction time (exactly one brief, unique `document_id`s). Schemas above show the shape.

Three rules fall out of this:

- **`DocumentId = str`, not `Literal[...]`.** The schema is agnostic to which documents exist. Adding or swapping a document is a runtime concern, not a type-level one. A span pointing at a `document_id` that isn't in the active `DocumentSet` is caught at the orchestrator boundary and downgraded to a `could_not_verify` finding (the agent hallucinated a doc; that's a finding, not a crash).
- **`case_loader.load_default_case()` is the only place specific filenames live.** The four Rivera files (`motion_for_summary_judgment`, `police_report`, `medical_records_excerpt`, `witness_statement`) are referenced by name exactly once in the codebase: in `backend/case_loader.py`, where each is read off disk and assigned a role and display name. Swapping cases is a `case_loader` edit, not a schema or agent edit.
- **Agents never reference `document_id`s by literal value.** No `if doc_id == "police_report":` anywhere. Agents iterate over `doc_set.records()` or take `ParsedRecord`s as input. This is a grep-enforceable rule - the eval acceptance criteria include a grep that fails if a literal filename leaks into agent code.

`POST /analyze` takes no body in v1. The handler calls `case_loader.load_default_case()` and passes the `DocumentSet` to `run_pipeline`. The pipeline is internally agnostic to where the set came from, so a future request-body code path is an additive change in `main.py` only - no schema or agent changes. We left the door open without building the door.

The eval harness builds its own `DocumentSet` from a case-file JSON (`evals/cases/<id>.json` references text files by path and assigns role + display name) and calls `run_pipeline` directly, bypassing `case_loader`. Same pipeline code, different document set. That's the whole point of the abstraction: it's there to keep the pipeline honest about not caring which docs it gets.

## 4. The shape of `POST /analyze`

Concrete enough to build to. The plan refines field names, this doc fixes the shape.

**Request:** no body in v1. The backend chooses the document set via `case_loader.load_default_case()` (see §3.11). The pipeline is internally agnostic - it takes a `DocumentSet` - so adding a request-body code path later is an additive change in `main.py` only. We're not building that today.

**Response:** `Report` (Pydantic model). Quote-accuracy lives inside the citation finding (no separate `quotes` array - if a citation carries a verbatim quoted span, the verifier checks it as part of its verdict). Roughly:

```json
{
  "consistency": [
    {
      "claim": { "claim_text": "...", "claim_span": { "document_id": "motion_for_summary_judgment", "start": 2104, "end": 2183, "excerpt": "..." } },
      "verdict": "contradicted",
      "confidence": 0.88,
      "reasoning": "...",
      "evidence_quote": "...",
      "evidence_span": { "document_id": "police_report", "start": 401, "end": 488, "excerpt": "..." },
      "checked_documents": ["police_report", "medical_records_excerpt", "witness_statement"]
    }
  ],
  "citations": [
    {
      "citation": {
        "cite": "Smith v. Jones, 123 F.3d 456 (9th Cir. 1999)",
        "proposition": "...",
        "quoted_language": "...",
        "claim_span": { "document_id": "motion_for_summary_judgment", "start": 1843, "end": 1922, "excerpt": "..." }
      },
      "verdict": "unsupported",
      "confidence": 0.82,
      "reasoning": "...",
      "evidence_quote": "...",
      "evidence_span": null,
      "lookup": { "found": true, "canonical_cite": "...", "holding_text": "...", "quoted_text_match": false, "source_url": null, "lookup_status": "found", "notes": null }
    }
  ],
  "memo": { "text": "...", "top_findings": [{ "finding_type": "consistency", "finding_index": 0 }], "partial_failure_note": null },
  "meta": {
    "model": "gpt-4o",
    "elapsed_ms": 12340,
    "token_usage": { "prompt": 12000, "completion": 1800 },
    "partial_failures": []
  }
}
```

Field order in each finding is the order a judge reads: claim location, evidence, verdict, reasoning, confidence. Every finding carries a `claim_span` and (when verdict isn't `could_not_verify`) an `evidence_span`. The UI uses these to let the judge click straight into the document. Typed, structured, uncertainty-aware, source-traceable - decided here.

## 5. Things this architecture deliberately doesn't have

- **A generic agent framework.** We're building one pipeline. LangChain / LlamaIndex / Autogen would obscure more than they help at this scope.
- **A database.** Documents live on disk. Reports aren't persisted. If we add persistence, it goes in one new module, not a sprinkled ORM.
- **A queue or worker pool.** `asyncio` handles concurrency; the workload is tiny.
- **A config system.** Constants live in module scope. Env vars go through `os.getenv` in `llm.py`. No `pydantic-settings` until we have ≥5 settings.
- **A logging framework.** `print` and `logging.basicConfig` are enough. Reach for structlog only if logs become a deliverable.

If the plan needs any of these, the plan justifies it. Default answer is no.

## 6. Decision log

When `/plan` makes an architectural choice that overrides or extends this doc, the chosen plan in `.claude/plans/` is the record. Plans get committed (the README explains why), so the plan + this doc + AGENTS.md should be enough for any future contributor to pick up.
