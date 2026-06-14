"""Assignment family: match one entity category (agents) to another (tasks).
PuLP MILP with binary x[agent, task] variables. (spec §2.1)
"""
from __future__ import annotations

import pulp

from spec.enums import ObjectiveSense
from spec.schema import Constraint, Entity, Objective, OptimizationSpec


def split_categories(spec: OptimizationSpec) -> tuple[list[Entity], list[Entity]]:
    """Decide which category plays 'agent' and which plays 'task'.

    Preference order:
      1. spec.roles (explicit LLM declaration — most reliable)
      2. decision-variable indexed_by order
      3. ONE_PER_ENTITY constraint's entity_category as agents
      4. first two categories by data order (fallback)
    """
    cats: dict[str, list[Entity]] = {}
    for e in spec.entities:
        cats.setdefault(e.category, []).append(e)
    names = list(cats)
    if len(names) < 2:
        raise ValueError("Assignment problems need two entity categories "
                         f"(found: {names}).")

    if spec.roles is not None:
        ac, tc = spec.roles.agent_category, spec.roles.task_category
        if ac in cats and tc in cats:
            return cats[ac], cats[tc]

    order = None
    for dv in spec.decision_variables:
        if len(dv.indexed_by) == 2 and all(c in cats for c in dv.indexed_by):
            order = dv.indexed_by
            break
    if order is None:
        for c in spec.constraints:
            agent_cat = c.parameters.get("entity_category")
            if c.constraint_type.value == "one_per_entity" and agent_cat in cats:
                other = next(n for n in names if n != agent_cat)
                order = [str(agent_cat), other]
                break
    if order is None:
        order = names[:2]
    return cats[order[0]], cats[order[1]]


def pair_coefficient(agent: Entity, task: Entity, field: str | None,
                     cost_table: dict[tuple[str, str], float] | None = None) -> float:
    """Resolve the objective coefficient for an (agent, task) pair.

    Priority: pairwise cost table lookup → wide-format matrix column on task
    (`{field}_{agent_id}`) → `{field}` on task → `{field}` on agent → 1.0.
    """
    if cost_table is not None:
        val = cost_table.get((agent.id, task.id))
        if val is not None:
            return val
    if not field:
        return 1.0
    for source, key in ((task, f"{field}_{agent.id}"), (task, field), (agent, field)):
        val = source.attributes.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return 1.0


class AssignmentModelBuilder:
    name = "assignment"

    def init_model(self, spec: OptimizationSpec, _data: dict):
        agents, tasks = split_categories(spec)
        sense = (pulp.LpMinimize if spec.objective.sense == ObjectiveSense.MINIMIZE
                 else pulp.LpMaximize)
        prob = pulp.LpProblem("assignment", sense)
        x = {(a.id, t.id): pulp.LpVariable(f"x[{a.id},{t.id}]", cat="Binary")
             for a in agents for t in tasks}
        variables = {"x": x, "agents": agents, "tasks": tasks,
                     "cost_table": _data.get("cost_tables") or None}
        return prob, variables

    def set_objective(self, prob, variables, objective: Objective, _data: dict):
        x, agents, tasks = variables["x"], variables["agents"], variables["tasks"]
        cost_table = variables.get("cost_table")
        prob += pulp.lpSum(
            pair_coefficient(a, t, objective.coefficient_field, cost_table) * x[a.id, t.id]
            for a in agents for t in tasks), "objective"


# ---------------- constraint builders ----------------

def _scalar(params: dict, *keys, default=None):
    for k in keys:
        if k in params:
            return params[k]
    return default


def build_one_per_entity_assignment(prob, variables, c: Constraint, _data):
    """Each agent does at most/exactly N tasks (default: at most 1)."""
    x, agents, tasks = variables["x"], variables["agents"], variables["tasks"]
    n = float(_scalar(c.parameters, "count", "limit", default=1))
    sense = str(_scalar(c.parameters, "sense", default="<="))
    target = str(_scalar(c.parameters, "entity_category",
                         default=agents[0].category))
    if agents and target == agents[0].category:
        group, axis = agents, 0
    else:
        group, axis = tasks, 1
    for g in group:
        total = pulp.lpSum(x[a.id, t.id] for a in agents for t in tasks
                           if (a.id if axis == 0 else t.id) == g.id)
        if sense == ">=":
            prob += total >= n, f"{c.name}[{g.id}]"
        elif sense == "==":
            prob += total == n, f"{c.name}[{g.id}]"
        else:
            prob += total <= n, f"{c.name}[{g.id}]"


def build_demand_coverage_assignment(prob, variables, c: Constraint, _data):
    """Each task is covered exactly once (or >= 1 if sense given)."""
    x, agents, tasks = variables["x"], variables["agents"], variables["tasks"]
    sense = str(_scalar(c.parameters, "sense", default="=="))
    n = float(_scalar(c.parameters, "count", default=1))
    for t in tasks:
        total = pulp.lpSum(x[a.id, t.id] for a in agents)
        if sense == ">=":
            prob += total >= n, f"{c.name}[{t.id}]"
        else:
            prob += total == n, f"{c.name}[{t.id}]"


def build_capacity_assignment(prob, variables, c: Constraint, _data):
    """sum(task demand assigned to agent) <= agent capacity."""
    x, agents, tasks = variables["x"], variables["agents"], variables["tasks"]
    res_f = str(_scalar(c.parameters, "resource_field", default="capacity"))
    dem_f = str(_scalar(c.parameters, "demand_field", default="demand"))
    for a in agents:
        cap = float(a.attributes.get(res_f, _scalar(c.parameters, "limit", default=0)))
        prob += pulp.lpSum(
            float(t.attributes.get(dem_f, 0)) * x[a.id, t.id] for t in tasks
        ) <= cap, f"{c.name}[{a.id}]"


def build_time_budget_assignment(prob, variables, c: Constraint, _data):
    """sum(task duration assigned to agent) <= agent time budget."""
    x, agents, tasks = variables["x"], variables["agents"], variables["tasks"]
    dur_f = str(_scalar(c.parameters, "duration_field", "demand_field",
                        default="duration"))
    budget_f = _scalar(c.parameters, "resource_field")
    scalar_limit = _scalar(c.parameters, "limit")
    for a in agents:
        budget = float(a.attributes.get(str(budget_f), scalar_limit or 0)) \
            if budget_f else float(scalar_limit or 0)
        prob += pulp.lpSum(
            float(t.attributes.get(dur_f, 0)) * x[a.id, t.id] for t in tasks
        ) <= budget, f"{c.name}[{a.id}]"


def build_compatibility_assignment(prob, variables, c: Constraint, _data):
    """Force x[i,j] = 0 whenever agent and task are incompatible.

    Two complementary checks:
    1. Relationship table allowed_pairs: if present, any pair NOT in the set is blocked.
    2. Field-based: agent_field vs task_field attribute matching (comma-separated lists).
    """
    x, agents, tasks = variables["x"], variables["agents"], variables["tasks"]

    allowed_pairs: set[tuple[str, str]] | None = _data.get("allowed_pairs")
    if allowed_pairs is not None:
        for a in agents:
            for t in tasks:
                if (a.id, t.id) not in allowed_pairs:
                    prob += x[a.id, t.id] == 0, f"{c.name}_rel[{a.id},{t.id}]"

    agent_field = str(_scalar(c.parameters, "agent_field", default="skills"))
    task_field = str(_scalar(c.parameters, "task_field", default="required_skill"))
    for a in agents:
        raw = a.attributes.get(agent_field)
        agent_vals = (
            {v.strip() for v in str(raw).split(",") if v.strip()}
            if raw is not None else set()
        )
        for t in tasks:
            raw_t = t.attributes.get(task_field)
            if raw_t is None:
                continue  # no requirement → compatible with everyone
            task_vals = {v.strip() for v in str(raw_t).split(",") if v.strip()}
            if not (agent_vals & task_vals):
                prob += x[a.id, t.id] == 0, f"{c.name}_field[{a.id},{t.id}]"
