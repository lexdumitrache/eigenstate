# Modeling Scope — Eigenstate v1

## Supported problem families

### Assignment
Binary decision: each agent gets at most one task (or exactly one, if constrained), each task fulfilled by exactly one agent.

- Workers ↔ tasks
- Drivers ↔ deliveries (no routing — *which* driver, not *what order*)
- Seats ↔ candidates

Solver: **PuLP MILP** (`modeling/assignment_model.py`)

### Allocation
Continuous or binary split of a resource across recipients.

- Single category → continuous LP (budget across departments)
- Two categories → binary dispatch reusing assignment builders with capacity sums (vans ↔ jobs)

Solver: **PuLP LP / MILP** (`modeling/allocation_model.py`)

### Scheduling
Time-based placement with precedence, no-overlap, and shift constraints.

- Shift scheduling (workers × time slots)
- Job-shop (jobs on machines, precedence)
- Simple project scheduling (task ordering, resource windows)

Solver: **OR-Tools CP-SAT** (`modeling/scheduling_model.py`)

## Constraint types (v1 enum)

Constraints are typed against a fixed enum in `spec/enums.py`. The LLM emits `{constraint_type, parameters}`; the builder maps each `(problem_type, constraint_type)` pair to a hand-written function. A constraint the LLM extracts that falls outside this map becomes a blocking ambiguity — it is never silently dropped.

| Constraint type | Description |
|---|---|
| `one_per_entity` | Each worker/van does at most (or exactly) N tasks |
| `demand_coverage` | Each task/shift/package must be covered by exactly one agent |
| `capacity` | Resource/worker capacity ceiling; for scheduling, limits concurrent tasks (cumulative) |
| `time_budget` | Max hours or time units per entity (assignment/allocation) or a global finish deadline (scheduling) |
| `budget_limit` | Total spend must not exceed a budget ceiling (allocation) |
| `min_allocation` | An entity must receive at least X units (allocation) |
| `max_allocation` | An entity must receive at most X units (allocation) |
| `no_overlap` | No two tasks may run at the same time on the same resource (scheduling) |
| `precedence` | Task A must end before task B starts (scheduling) |
| `compatibility` | `x[i,j] = 0` when agent and task fields don't match (assignment/allocation) |

### Which constraints apply to which problem types

| Constraint type | Assignment | Allocation | Scheduling |
|---|:---:|:---:|:---:|
| `one_per_entity` | ✓ | ✓ | |
| `demand_coverage` | ✓ | ✓ | ✓ |
| `capacity` | ✓ | ✓ | ✓ |
| `time_budget` | ✓ | ✓ | ✓ |
| `budget_limit` | | ✓ | |
| `min_allocation` | | ✓ | |
| `max_allocation` | | ✓ | |
| `no_overlap` | | | ✓ |
| `precedence` | | | ✓ |
| `compatibility` | ✓ | ✓ | |

A constraint type used outside its supported problem family is surfaced to the user as an ambiguity; it is never silently dropped or coerced.

## What is explicitly out of scope (v1)

| Pattern | Behaviour |
|---|---|
| Vehicle routing / VRP (visiting order, TSP) | Detected by LLM flag and **refused** with a v2 message |
| Multi-objective optimisation | Not modelled — single objective only |
| Free-form LP/MILP expressions | Not accepted — constraint must map to a known type |
| Stochastic / robust optimisation | Out of scope |
| Continuous scheduling (non-discrete time) | Out of scope |

Routing detection uses a dedicated LLM flag (`is_routing`). If set, the pipeline returns an error before modelling.

## Coefficient resolution

For an objective field (e.g. `cost`), the builder resolves the per-pair coefficient in this order:

1. `cost_{agent_id}` attribute on the task (wide-format cost matrix from CSV)
2. `cost` attribute on the task
3. `cost` attribute on the agent
4. Default: `1.0`

## Implemented v1 features

These features are fully implemented and active:

- **Persistent sessions** — `SQLiteSessionStore` (`api/session_store.py`) backs all sessions with SQLite. Sessions survive restarts and are preloaded into a memory cache on startup. The `EIGENSTATE_DB` environment variable overrides the database path.
- **Pairwise cost tables** — `COST_MATRIX` is a first-class `TableRole`. The parser suggests agent/task column mappings for uploaded cost matrices; the user confirms via `POST /api/sessions/{id}/pairwise-table`. The solver reads confirmed tables to resolve per-pair objective coefficients.
- **Clarification gate** — when the LLM flags unresolved ambiguities, the session enters `AWAITING_CLARIFICATION` and the solve endpoint is blocked until `POST /api/sessions/{id}/clarify` is called. Clarification answers are applied as explicit spec mutations (not just gating) before the model is built.

## v2 additions planned

- Vehicle routing (OR-Tools Routing Library)
- Solver sensitivity reports (shadow prices, slack)
- Multi-objective optimisation
