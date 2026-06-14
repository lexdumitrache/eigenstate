# Eigenstate Architecture

## Pipeline

```
User (browser)
    │
    │  POST /api/sessions/{id}/parse   {"text": "...", files: [...]}
    ▼
FastAPI session API   (backend/api/main.py)
    │
    │  dispatch to pipeline
    ▼
Parser / Column Mapper   (backend/parser/)
    │  LLM classifies problem type, extracts entities,
    │  objective, constraints, and flags ambiguities.
    │  Column mapper aligns uploaded CSV/Excel headers.
    ▼
                ┌─── Gate: AWAITING_COLUMN_MAPPING ───┐
                │  user confirms/edits column mapping  │
                └──────────────────────────────────────┘
                ┌─── Gate: AWAITING_CLARIFICATION ─────┐
                │  user resolves all detected           │
                │  ambiguities before solve is allowed  │
                └──────────────────────────────────────┘
    │
    ▼
OptimizationSpec   (backend/spec/schema.py)
    │  Pydantic model: problem_type, entities, objective,
    │  constraints (typed enum, not free-form strings),
    │  ambiguities, data_file_ref.
    ▼
Validator   (backend/validation/validator.py)
    │  Schema check, reference integrity,
    │  feasibility pre-check (demand vs capacity).
    ▼
Model Builder   (backend/modeling/builder.py)
    │  One hand-written builder per (problem_type, constraint_type).
    │  Routes to:
    │    assignment_model.py  →  PuLP MILP
    │    allocation_model.py  →  PuLP LP / MILP
    │    scheduling_model.py  →  OR-Tools CP-SAT
    ▼
Solver   (backend/solvers/solver_router.py)
    │  PuLP/CBC  or  OR-Tools CP-SAT
    ▼
Explainer   (backend/explanation/explainer.py)
    │  Deterministic digest converts variable values
    │  to decisions; optional LLM pass rephrases.
    │  Past feedback preferences injected here.
    ▼
Result + Explanation → frontend dashboard
```

## Component map

| Path | Role |
|---|---|
| `backend/api/main.py` | FastAPI routes, session lifecycle, feedback endpoints |
| `backend/api/pipeline.py` | Orchestrates parse → validate → model → solve → explain |
| `backend/api/session_store.py` | In-memory session state (interface-compatible with Redis) |
| `backend/parser/parser.py` | LLM prompt construction and response parsing |
| `backend/parser/llm_adapter.py` | Provider-agnostic `complete_json` (Anthropic / OpenAI / Stub) |
| `backend/parser/column_mapper.py` | CSV/Excel header → spec field alignment |
| `backend/spec/schema.py` | `OptimizationSpec` and all Pydantic models |
| `backend/spec/enums.py` | `ProblemType`, `ConstraintType`, `SessionStage` enums |
| `backend/validation/validator.py` | Schema + reference + feasibility pre-check |
| `backend/modeling/builder.py` | Dispatch table: `(problem_type, constraint_type)` → builder |
| `backend/modeling/assignment_model.py` | PuLP assignment / dispatch MILP |
| `backend/modeling/allocation_model.py` | PuLP allocation LP/MILP |
| `backend/modeling/scheduling_model.py` | OR-Tools CP-SAT scheduling |
| `backend/solvers/solver_router.py` | Calls the right solver, normalises result |
| `backend/explanation/explainer.py` | Deterministic digest + optional LLM rephrasing |
| `backend/feedback/store.py` | Logs accept/reject decisions, infers preference summaries |
| `frontend/src/App.jsx` | React UI — step-by-step session flow |

## Session state machine

```
PARSED
  → AWAITING_COLUMN_MAPPING   (if files uploaded)
  → AWAITING_CLARIFICATION    (if ambiguities detected)
  → READY
  → VALIDATED
  → MODELED
  → SOLVED
  → EXPLAINED
```

`POST /solve` returns **409** unless the session is `READY`. The clarification gate is enforced server-side.
