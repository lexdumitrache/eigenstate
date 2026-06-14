"""Allocation family (spec §2.2): two shapes share one family.

* continuous mode — one entity category; x[entity] >= 0 is the amount
  allocated (budget, energy, hours). LP.
* dispatch mode — two categories (vans↔jobs); binary x[agent, task] with
  capacity / time-budget sums. No route sequencing (v2). MILP.

Mode is inferred from the data: two categories → dispatch, one → continuous.
"""
from __future__ import annotations

import pulp

from spec.enums import ObjectiveSense
from spec.schema import Constraint, OptimizationSpec
from .assignment_model import (build_capacity_assignment,
                               build_compatibility_assignment,
                               build_demand_coverage_assignment,
                               build_one_per_entity_assignment,
                               build_time_budget_assignment,
                               pair_coefficient, split_categories)


def _is_dispatch(spec: OptimizationSpec) -> bool:
    return len({e.category for e in spec.entities}) >= 2


class AllocationModelBuilder:
    name = "allocation"

    def init_model(self, spec: OptimizationSpec, _data: dict):
        sense = (pulp.LpMinimize if spec.objective.sense == ObjectiveSense.MINIMIZE
                 else pulp.LpMaximize)
        prob = pulp.LpProblem("allocation", sense)
        if _is_dispatch(spec):
            agents, tasks = split_categories(spec)
            x = {(a.id, t.id): pulp.LpVariable(f"x[{a.id},{t.id}]", cat="Binary")
                 for a in agents for t in tasks}
            return prob, {"mode": "dispatch", "x": x, "agents": agents, "tasks": tasks,
                          "cost_table": _data.get("cost_tables") or None}
        entities = spec.entities
        x = {e.id: pulp.LpVariable(f"x[{e.id}]", lowBound=0) for e in entities}
        return prob, {"mode": "continuous", "x": x, "entities": entities}

    def set_objective(self, prob, variables, objective, _data: dict):
        if variables["mode"] == "dispatch":
            x, agents, tasks = variables["x"], variables["agents"], variables["tasks"]
            cost_table = variables.get("cost_table")
            prob += pulp.lpSum(
                pair_coefficient(a, t, objective.coefficient_field, cost_table) * x[a.id, t.id]
                for a in agents for t in tasks), "objective"
            return
        x, entities = variables["x"], variables["entities"]
        field = objective.coefficient_field
        prob += pulp.lpSum(
            float(e.attributes.get(field, 1.0)) * x[e.id] if field else x[e.id]
            for e in entities), "objective"


def _per_entity_bound(prob, variables, c: Constraint, sense: str):
    x, entities = variables["x"], variables["entities"]
    field = c.parameters.get("resource_field")
    scalar = c.parameters.get("limit", c.parameters.get("min", c.parameters.get("max")))
    for e in entities:
        bound = float(e.attributes.get(str(field), scalar or 0)) \
            if field else float(scalar or 0)
        if sense == ">=":
            prob += x[e.id] >= bound, f"{c.name}[{e.id}]"
        else:
            prob += x[e.id] <= bound, f"{c.name}[{e.id}]"


# ---------------- constraint builders (continuous mode) ----------------

def build_budget_limit(prob, variables, c: Constraint, _data):
    """Total allocated <= limit (dispatch: total pair cost <= limit)."""
    gp = _data.get("global_params", {})
    limit_val = c.parameters.get("limit") \
        or gp.get("limit") or gp.get("budget") or gp.get("total_budget")
    limit = float(limit_val or 0)
    if variables["mode"] == "dispatch":
        x, agents, tasks = variables["x"], variables["agents"], variables["tasks"]
        cost_f = c.parameters.get("demand_field") or c.parameters.get("resource_field")
        prob += pulp.lpSum(
            pair_coefficient(a, t, str(cost_f) if cost_f else None) * x[a.id, t.id]
            for a in agents for t in tasks) <= limit, c.name
    else:
        prob += pulp.lpSum(variables["x"].values()) <= limit, c.name


def build_capacity_allocation(prob, variables, c: Constraint, data):
    if variables["mode"] == "dispatch":
        build_capacity_assignment(prob, variables, c, data)
    else:
        _per_entity_bound(prob, variables, c, "<=")


def build_min_allocation(prob, variables, c: Constraint, _data):
    _per_entity_bound(prob, variables, c, ">=")


def build_max_allocation(prob, variables, c: Constraint, _data):
    _per_entity_bound(prob, variables, c, "<=")


def build_demand_coverage_allocation(prob, variables, c: Constraint, data):
    if variables["mode"] == "dispatch":
        build_demand_coverage_assignment(prob, variables, c, data)
    else:
        # continuous: total allocation must meet a demand floor
        demand = float(c.parameters.get("limit", c.parameters.get("min", 0)))
        prob += pulp.lpSum(variables["x"].values()) >= demand, c.name


def build_one_per_entity_allocation(prob, variables, c: Constraint, data):
    if variables["mode"] != "dispatch":
        raise ValueError("one_per_entity requires dispatch mode (two categories).")
    build_one_per_entity_assignment(prob, variables, c, data)


def build_time_budget_allocation(prob, variables, c: Constraint, data):
    if variables["mode"] == "dispatch":
        build_time_budget_assignment(prob, variables, c, data)
    else:
        _per_entity_bound(prob, variables, c, "<=")


def build_compatibility_allocation(prob, variables, c: Constraint, data):
    if variables["mode"] == "dispatch":
        build_compatibility_assignment(prob, variables, c, data)
    # continuous mode has a single entity type — compatibility doesn't apply
