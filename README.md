# BS Detector

Legal briefs lie. Not always intentionally — but they do. They cite cases that don't say what they claim. They quote authority with words quietly removed. They state facts that contradict the documents sitting right next to them.

Your task: build an AI pipeline that catches it.

## Why this matters (Stefano's framing)

This sits inside Learned Hand's product DNA: tools judges trust to do more with current resources. Trust is the whole game - one fabricated finding erodes confidence in every other finding the system has produced. The pipeline I'm building reflects that with three load-bearing commitments:

- **Surface, don't decide.** Every flag is a question for a judge with evidence attached. The system flags; the human rules.
- **Verifiability is the schema.** Every finding carries a `TextSpan` back to the source so a judge can click from a flag straight into the document. A finding that floats free of its source is malformed.
- **Impartiality.** Both sides' claims get equal skepticism. Prompts speak in role labels, not party names.

The detail on these lives in [AGENTS.md](AGENTS.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

## A note on `.claude/` (Stefano's submission)

The `.claude/` folder is committed on purpose. The README says "Use everything. We want to see how you use it" - so I'm showing you the Claude Code workflow I built for this challenge instead of hiding it. If you peek inside you'll find:

- `.claude/commands/` - slash commands I wrote for this project. `/plan` grills me on requirements and writes an executable plan. `/execute` orchestrates subagents to build it task-by-task with TDD. `/verify` audits the result against the plan. `/tweak` patches issues post-build and logs them on the plan. `/eval` runs the eval suite against real LLMs. `/write-notes` appends to my running diary in [NOTES.md](NOTES.md).
- `.claude/plans/` - the actual plans I executed. Each one has a spec, a task graph, per-task subagent prompts with TDD instructions, and a post-build refinements log. They're the closest thing to a project journal of what I built and why.

Read [AGENTS.md](AGENTS.md) for the code rules I followed and [ARCHITECTURE.md](ARCHITECTURE.md) for where things live and why. [NOTES.md](NOTES.md) is my running diary. The reflection doc at the end of the project pulls from all of these.

## Setup

### Docker (recommended)

```bash
cp .env.example .env      # Add your OpenAI API key
docker compose up --build
```

The API runs at `http://localhost:8002`. The UI runs at `http://localhost:5175`.

Both services hot-reload — edit files on your host and changes appear automatically.

### Manual Setup

#### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # Add your OpenAI API key
uvicorn main:app --reload
```

The API runs at `http://localhost:8002`.

#### Frontend

```bash
cd frontend
npm install
npm run dev
```

The UI runs at `http://localhost:5175`.

## The Task

Inside `backend/documents/` you'll find a small case file: a Motion for Summary Judgment in a personal injury lawsuit (*Rivera v. Harmon Construction Group*), along with a police report, medical records, and a witness statement.

Build a multi-agent pipeline that analyzes these documents and produces a structured verification report. Your pipeline should:

**Core (Tier 1)**
- Extract all citations from the Motion for Summary Judgment
- For each citation, assess whether the cited authority actually supports the proposition as stated
- Flag direct quotes for accuracy
- Produce structured output (JSON) — not a wall of prose

**Expected (Tier 2)**
- Build an eval harness that measures your pipeline's output quality. It must be runnable via a single command (e.g., `python run_evals.py`). At minimum, measure precision (avoiding false flags), recall (catching known flaws), and hallucination rate (not fabricating findings). You choose the approach — there's no prescribed framework or tooling.
- Cross-document consistency check: compare facts stated in the MSJ against the police report, medical records, and witness statement
- Express uncertainty appropriately — "could not verify" rather than fabricating a finding
- Pass structured data between agents, not raw text blobs

**Stretch (Tier 3)**
- At least 4 well-defined agents with distinct, non-overlapping roles
- A confidence scoring layer: each flag rated by how certain the pipeline is, with reasoning
- A judicial memo agent: synthesizes the top findings into a one-paragraph summary written for a judge
- Agent orchestration that handles failures gracefully
- A UI that displays the report in a structured, readable way — not just raw JSON
- A reflection document explaining the tradeoffs you made and what you'd do differently

## Deliverables

1. A working `POST /analyze` endpoint that returns a structured verification report
2. Agent code with clear, named agents and explicit prompts
3. A runnable eval suite with instructions in your README on how to run it
4. A brief reflection (in the repo or as a separate file) on your design decisions and tradeoffs

## Time

6 hours. This is intentionally scoped beyond what most candidates will finish. Where you invest your time matters more than finishing everything. A well-tested pipeline that catches 3 flaws is stronger than an untested one that attempts 10.

## Citation lookup

The citation pillar checks each cite against [CourtListener](https://www.courtlistener.com), Free Law Project's free case-law API. We use the unauthenticated tier, so there's no API key to set.

A few details worth knowing:

- We hit the v4 search endpoint (`/api/rest/v4/search/?type=o&citation=...`) and post-filter results by exact normalized cite match. The older v3 `citation-lookup` endpoint that does the same job in one call now requires auth, so we get there with a search + filter on the unauth tier.
- A cite that CourtListener can't find lands as `lookup.lookup_status == "not_found"`. The verifier short-circuits to `verdict: "could_not_verify"` with high confidence and emits no fabricated holding text. That's how the pipeline catches made-up citations: the source doesn't exist, so no agent gets to invent one.
- Running `python3 -m evals.run` requires an internet connection. There's no on-disk cache of CourtListener responses; each eval run hits the live API. With only ten cites in the Rivera case, we're nowhere near the 5000 req/day rate limit.
- Unit tests never touch the network. They use the `mock_lookup` fixture in [backend/tests/conftest.py](backend/tests/conftest.py) to stand in for `CourtListenerLookup.fetch`.

## Evals

We run your eval suite as part of our review. Document how to run it in your README. We care more about thoughtful metric design than perfect scores — an eval that honestly reports 60% recall tells us more than one that reports 100% on cherry-picked cases.

## AI Usage

Use everything. That's the job. We want to see how you use it, not whether you do.

## Evaluation

We are evaluating:

1. How you decompose the problem into agents
2. How precisely you write prompts
3. The quality of your eval approach — do you measure what matters?
4. How far you get through the spec
5. How honest your reflection is

Not lines of code.
