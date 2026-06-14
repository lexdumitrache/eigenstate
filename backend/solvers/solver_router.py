"""Solver layer: PuLP (CBC) for assignment/allocation, CP-SAT for scheduling,
routed by problem type (spec §1)."""
from __future__ import annotations

import time

import pulp

from spec.enums import ProblemType, SolverStatus
from spec.schema import OptimizationSpec, SolveResult

_PULP_STATUS = {
    pulp.LpStatusOptimal: SolverStatus.OPTIMAL,
    pulp.LpStatusInfeasible: SolverStatus.INFEASIBLE,
    pulp.LpStatusUnbounded: SolverStatus.UNBOUNDED,
}


def solve_pulp(prob: pulp.LpProblem, variables: dict,
               time_limit_s: int = 30) -> SolveResult:
    t0 = time.perf_counter()
    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit_s))
    elapsed = (time.perf_counter() - t0) * 1000
    status = _PULP_STATUS.get(prob.status, SolverStatus.ERROR)
    values: dict[str, float] = {}
    slacks: dict[str, float] = {}
    if status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
        # Use canonical keys from the variables dict — PuLP sanitizes
        # bracket/comma characters in its own .name attribute.
        for key, var in variables.get("x", {}).items():
            val = var.value()
            if val is not None and abs(val) > 1e-9:
                name = (f"x[{key[0]},{key[1]}]" if isinstance(key, tuple)
                        else f"x[{key}]")
                values[name] = round(val, 6)
        for cname, con in prob.constraints.items():
            sv = pulp.value(con)
            if sv is not None:
                slacks[cname] = round(sv, 6)
    return SolveResult(
        status=status.value,
        objective_value=pulp.value(prob.objective)
        if status == SolverStatus.OPTIMAL else None,
        variable_values=values,
        constraint_slacks=slacks,
        solver_name="PuLP/CBC",
        solve_time_ms=round(elapsed, 2),
        message=pulp.LpStatus[prob.status],
    )


def solve_cpsat(model, variables: dict, time_limit_s: int = 30) -> SolveResult:
    from ortools.sat.python import cp_model  # lazy: avoids crash at import time
    _CPSAT_STATUS = {
        cp_model.OPTIMAL: SolverStatus.OPTIMAL,
        cp_model.FEASIBLE: SolverStatus.FEASIBLE,
        cp_model.INFEASIBLE: SolverStatus.INFEASIBLE,
    }
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    t0 = time.perf_counter()
    status = solver.Solve(model)
    elapsed = (time.perf_counter() - t0) * 1000
    mapped = _CPSAT_STATUS.get(status, SolverStatus.ERROR)
    values: dict[str, float] = {}
    if mapped in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
        for tid, var in variables["starts"].items():
            values[f"start[{tid}]"] = float(solver.Value(var))
        for tid, var in variables["ends"].items():
            values[f"end[{tid}]"] = float(solver.Value(var))
        if "makespan" in variables:
            values["makespan"] = float(solver.Value(variables["makespan"]))
    return SolveResult(
        status=mapped.value,
        objective_value=solver.ObjectiveValue()
        if mapped in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE) else None,
        variable_values=values,
        solver_name="OR-Tools CP-SAT",
        solve_time_ms=round(elapsed, 2),
        message=solver.StatusName(status),
    )


def route_and_solve(spec: OptimizationSpec, prob, variables,
                    time_limit_s: int = 30) -> SolveResult:
    if spec.problem_type == ProblemType.SCHEDULING:
        return solve_cpsat(prob, variables, time_limit_s)
    return solve_pulp(prob, variables, time_limit_s)
