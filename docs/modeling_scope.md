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

Key constraint types:
- `max_assignments_per_agent`, `min_assignments_per_agent`
- `capacity`, `demand`
- `availability` (binary mask per agent/slot)
- `precedence` (task A before task B)
- `no_overlap`
- `shift_length`, `max_hours`
- `budget` (allocation ceiling)

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

## v2 additions planned

- Vehicle routing (OR-Tools Routing Library)
- Persistent session store (in-memory `SessionStore` is already interface-compatible with a Redis swap)
- Pairwise cost tables as a first-class upload type
- Solver sensitivity reports (shadow prices, slack)
- Clarification answers applied as explicit model transformations (not just gating)
