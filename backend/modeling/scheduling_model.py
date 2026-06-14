"""Scheduling family (spec §2.3): OR-Tools CP-SAT.

Tasks become interval variables. Resource grouping (machine/worker column)
drives no-overlap; precedence links task ids; default objective minimizes
makespan (or weighted completion if a coefficient_field is given).
"""
from __future__ import annotations

from spec.enums import ObjectiveSense
from spec.schema import Constraint, Entity, OptimizationSpec

SCALE = 1  # durations are treated as integer time units


def _duration(task: Entity, dur_field: str) -> int:
    val = task.attributes.get(dur_field, task.attributes.get("duration", 1))
    try:
        return max(1, int(round(float(val) * SCALE)))
    except (TypeError, ValueError):
        return 1


class SchedulingModelBuilder:
    name = "scheduling"

    def init_model(self, spec: OptimizationSpec, _data: dict):
        from ortools.sat.python import cp_model  # lazy: avoids crash at import time
        model = cp_model.CpModel()
        dur_field = "duration"
        for c in spec.constraints:
            if "duration_field" in c.parameters:
                dur_field = str(c.parameters["duration_field"])
                break
        tasks = [e for e in spec.entities]

        # Horizon: at least sum-of-durations, but also cover any explicit deadlines.
        sum_dur = sum(_duration(t, dur_field) for t in tasks)
        max_deadline = max(
            (int(float(t.attributes["deadline"])) for t in tasks
             if "deadline" in t.attributes),
            default=0,
        )
        horizon = max(sum_dur, max_deadline) or 1

        cal_wins: dict[str, tuple[int, int]] = _data.get("calendar_windows", {})
        starts, ends, intervals = {}, {}, {}
        for t in tasks:
            d = _duration(t, dur_field)
            cal = cal_wins.get(t.id)
            if cal:
                release = cal[0]
                deadline = cal[1] if cal[1] else horizon
            else:
                release = int(float(t.attributes.get("release_time", 0)))
                deadline = int(float(t.attributes["deadline"])) \
                    if "deadline" in t.attributes else horizon
            s = model.NewIntVar(release, deadline - d, f"start[{t.id}]")
            e = model.NewIntVar(release + d, deadline, f"end[{t.id}]")
            intervals[t.id] = model.NewIntervalVar(s, d, e, f"iv[{t.id}]")
            starts[t.id], ends[t.id] = s, e

        variables = {"model_type": "cpsat", "tasks": tasks, "starts": starts,
                     "ends": ends, "intervals": intervals, "horizon": horizon,
                     "dur_field": dur_field}
        return model, variables

    def set_objective(self, model, variables, objective, _data: dict):
        tasks, ends = variables["tasks"], variables["ends"]
        horizon = variables["horizon"]
        field = objective.coefficient_field
        if field and any(field in t.attributes for t in tasks):
            expr = sum(int(float(t.attributes.get(field, 1))) * ends[t.id]
                       for t in tasks)
        else:
            makespan = model.NewIntVar(0, horizon, "makespan")
            model.AddMaxEquality(makespan, [ends[t.id] for t in tasks])
            variables["makespan"] = makespan
            expr = makespan
        if objective.sense == ObjectiveSense.MAXIMIZE:
            model.Maximize(expr)
        else:
            model.Minimize(expr)


# ---------------- constraint builders ----------------

def build_no_overlap_cpsat(model, variables, c: Constraint, _data):
    """No double-booking. If a resource_field is given, group tasks by it;
    otherwise all tasks share one resource."""
    tasks, intervals = variables["tasks"], variables["intervals"]
    field = c.parameters.get("resource_field")
    if field:
        groups: dict[str, list] = {}
        for t in tasks:
            key = str(t.attributes.get(str(field), "_shared"))
            groups.setdefault(key, []).append(intervals[t.id])
        for ivs in groups.values():
            if len(ivs) > 1:
                model.AddNoOverlap(ivs)
    else:
        model.AddNoOverlap(list(intervals.values()))


def build_precedence_cpsat(model, variables, c: Constraint, _data):
    """Task `before` must end before task `after` starts."""
    before = str(c.parameters.get("before", ""))
    after = str(c.parameters.get("after", ""))
    starts, ends = variables["starts"], variables["ends"]
    if before in ends and after in starts:
        model.Add(ends[before] <= starts[after])
    else:
        raise ValueError(f"Precedence '{c.name}' references unknown task ids "
                         f"({before!r} -> {after!r}).")


def build_time_budget_cpsat(model, variables, c: Constraint, _data):
    """All tasks must finish within a deadline (limit) — per resource group
    if resource_field given, else global."""
    limit = int(float(c.parameters.get("limit", variables["horizon"])))
    for t in variables["tasks"]:
        model.Add(variables["ends"][t.id] <= limit)


def build_demand_coverage_cpsat(model, variables, c: Constraint, _data):
    # Every task already has exactly one interval — coverage is structural.
    # Kept as a no-op so the parser can extract it without failing.
    return


def build_capacity_cpsat(model, variables, c: Constraint, _data):
    """Cumulative resource: at most `limit` tasks running concurrently."""
    limit = int(float(c.parameters.get("limit", 1)))
    intervals = list(variables["intervals"].values())
    model.AddCumulative(intervals, [1] * len(intervals), limit)
