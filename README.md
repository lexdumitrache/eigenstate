# Eigenstate v1

Describe an operational problem in plain language. Eigenstate maps it to one of three supported optimization templates — **assignment**, **allocation**, or **simple scheduling** — validates your assumptions, solves it, and explains the result. No free-form expression interpretation. **V1 blocks solving until the user resolves detected ambiguities. The next iteration applies clarification answers as explicit model transformations.**

> **v1 scope**: the system supports a controlled set of operational optimization templates. Arbitrary LP/MILP problems, multi-objective models, and routing/VRP are out of scope.

```
Natural Language Problem (+ optional CSV/Excel)
→ AI Parser (classify, extract entities/objective/constraints, detect ambiguities)
→ Column Mapping Confirmation (if files uploaded)        ← gate
→ Clarification Gate (mandatory before solving)          ← gate
→ Structured OptimizationSpec (validated Pydantic)
→ Validation Layer (schema + references + feasibility pre-check)
→ Model Builder (one hardcoded builder per (problem_type, constraint_type))
→ Solver (PuLP/CBC for assignment & allocation · OR-Tools CP-SAT for scheduling)
→ Results → Natural Language Explanation → Dashboard
```

## Problem families (v1 scope)

| Family | Examples | Solver |
|---|---|---|
| Assignment | workers↔tasks, drivers↔deliveries (no routing) | PuLP MILP |
| Allocation / simplified dispatch | budget splits, which van gets which job | PuLP LP/MILP |
| Scheduling | shifts, jobs on machines, precedence, no-overlap | OR-Tools CP-SAT |

Route sequencing (visiting order, VRP) is **detected and refused with a v2 message** — the system optimizes which van handles which deliveries, not the order of stops.

## Run it

### Docker (recommended for demos)

```bash
cp .env.example .env          # fill in your API key
docker compose up --build     # http://localhost
```

The frontend is served by nginx on port 80. `/api` requests are proxied to the backend container. Feedback is persisted in a named Docker volume across restarts.

### Local development

```bash
cp .env.example .env          # fill in ANTHROPIC_API_KEY / OPENAI_API_KEY

# Backend
pip install -r requirements.txt
source .env                   # or export vars individually
cd backend && uvicorn api.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
rm -rf node_modules   # drop any node_modules from another environment
npm ci                # reproducible install from package-lock.json
npm run dev           # http://localhost:5173
```

## API (spec §5)

```
POST /api/sessions                       create session
POST /api/sessions/{id}/files            upload CSV/Excel (before parse)
POST /api/sessions/{id}/parse            {"text": "..."}
POST /api/sessions/{id}/column-mapping   confirm/edit a file's mapping
POST /api/sessions/{id}/clarify          {"answers": {ambiguity_id: "..."}}
POST /api/sessions/{id}/solve            validate → model → solve → explain
GET  /api/sessions/{id}                  full state for UI rendering
```

The session state machine: `PARSED → AWAITING_COLUMN_MAPPING → AWAITING_CLARIFICATION → READY → VALIDATED → MODELED → SOLVED → EXPLAINED`. `POST /solve` returns **409** unless the session is `READY` — the clarification gate is enforced server-side, not just in the UI.

## What Eigenstate is (and isn't)

Eigenstate is an **LLM-assisted optimization compiler**: it translates natural language into a validated, solver-ready model and explains the result in plain language. It is not an adaptive ML platform. There is no model that learns to optimize differently over time.

The feedback module (`feedback/store.py`) logs accepted/rejected decisions and infers plain-English preference summaries from user corrections. These are fed into the explainer on future solves for the same problem type — so explanations become more contextually relevant as you use the system. The optimizer itself does not change.

A personalization layer that applies stored preferences as explicit constraint modifications is on the v2 backlog.

## Design decisions worth knowing

- **No expression strings.** The LLM emits constraints as `{constraint_type, parameters}` against a fixed enum; `modeling/builder.py` maps each `(problem_type, constraint_type)` pair to a hand-written builder. An extracted constraint outside that map becomes a blocking ambiguity ("v1 can't model this — proceed without it?"), never a silent drop.
- **Coefficient resolution** (`pair_coefficient`): for objective field `cost`, the builder checks the task attribute `cost_{agent_id}` (wide-format cost matrix from CSV), then `cost` on the task, then on the agent, else 1.0.
- **Allocation has two shapes**: one entity category → continuous LP (budget split); two categories → binary dispatch reusing the assignment builders with capacity sums.
- **Feasibility pre-check**: total demand vs total capacity is verified before the solver runs, so obvious data errors fail fast with a readable message.
- **Explanations can't hallucinate**: a deterministic digest converts variable values back into decisions; the optional LLM pass only rephrases that digest.
- **Provider-agnostic**: `parser/llm_adapter.py` exposes one `complete_json` interface with Anthropic, OpenAI, and an offline `DeterministicStub` used by the test suite.

## Packaging / distributing a zip

Before zipping for handoff, run the cleanup script from the repo root:

```bash
bash scripts/clean-for-handoff.sh
```

This removes everything that must not be shipped:

| Path | Why |
|---|---|
| `frontend/node_modules/` | Native binaries compiled for your OS — breaks on the recipient's machine |
| `frontend/dist/` | Generated build output — recipient builds it themselves |
| `backend/eigenstate.db` | Contains live session rows, tokens, and uploaded data |
| `feedback_log.json` | Runtime state |
| `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/` | Generated caches |
| `.claude/` | Local editor/agent state |

Then zip, excluding `.git` (omit `--exclude` if you intentionally want to share history):

```bash
zip -r ../eigenstate.zip . --exclude '*.git*'
```

Recipients unpack and build from scratch:

```bash
cd frontend
rm -rf node_modules
npm ci
npm run build
```

## Tests

```bash
cd backend && python -m pytest tests/ -v
```

Seven offline E2E tests (deterministic — no API key needed): one per problem family, file-upload + column-mapping flow, clarification-gate enforcement (solve refused until resolved), routing-scope detection, and the infeasibility pre-check.

### LLM parsing eval (requires API key)

Measures how reliably the real LLM converts messy natural language into correct `OptimizationSpec`s — the gap the offline tests cannot cover.

```bash
cd backend
ANTHROPIC_API_KEY=sk-... python -m tests.eval_llm_parsing          # Anthropic (default)
OPENAI_API_KEY=sk-...    python -m tests.eval_llm_parsing --openai  # OpenAI
python -m tests.eval_llm_parsing --subset asgn-01,alloc-02,route-01 # subset run
```

40 cases across 5 categories (10 each): assignment, allocation, scheduling, ambiguous, out-of-scope routing. Reports per-category: problem\_type accuracy, constraint recall/precision, ambiguity detection rate, routing-flag rate, and end-to-end solver success rate.

## Docs

- [docs/architecture.md](docs/architecture.md) — full pipeline diagram and component map
- [docs/modeling_scope.md](docs/modeling_scope.md) — supported problem families, constraint types, and explicit out-of-scope list
- [docs/demo_script.md](docs/demo_script.md) — walkthrough script for all three problem families plus edge cases

## v2 backlog

Vehicle routing (OR-Tools Routing), persistent session store (the in-memory `SessionStore` is interface-compatible with a Redis/DB swap), pairwise cost tables as a first-class upload, and solver sensitivity reports.
