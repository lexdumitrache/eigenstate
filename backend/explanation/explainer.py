"""Natural-language explanation of results (spec §1, final stage).

Two layers:
1. A deterministic digest (always correct, never hallucinated) that turns
   variable values back into human decisions using the spec's entities.
2. An optional LLM pass that narrates the digest — the LLM only rephrases
   verified facts, it never recomputes numbers.
"""
from __future__ import annotations

import re

from spec.enums import ConstraintType, ProblemType
from spec.schema import AssignmentGroup, Explanation, OptimizationSpec, SolveResult
from parser.llm_adapter import LLMAdapter, LLMError

NARRATE_PROMPT = """You are explaining an optimization result to a non-technical
operations manager. You are given verified facts and any learned user preferences
from past runs. Rephrase them as a short, clear summary. Where relevant, note
whether this result aligns with or conflicts with past preferences.
Do NOT invent numbers or decisions not in the facts.
Output ONLY JSON: {"summary": str, "caveats": [str, ...]}"""


def _entity_name(spec: OptimizationSpec, eid: str) -> str:
    for e in spec.entities:
        if e.id == eid:
            label = e.attributes.get("name")
            return f"{label} ({eid})" if label else eid
    return eid


def _short_label(spec: OptimizationSpec, eid: str) -> str:
    """Return the entity's name attribute if present, else its id."""
    for e in spec.entities:
        if e.id == eid:
            name = e.attributes.get("name")
            return str(name) if name else eid
    return eid


def build_digest(spec: OptimizationSpec, result: SolveResult) -> list[str]:
    decisions: list[str] = []
    if result.status not in ("optimal", "feasible"):
        return decisions

    if spec.problem_type in (ProblemType.ASSIGNMENT, ProblemType.ALLOCATION):
        pair = re.compile(r"x\[(.+?),(.+?)\]")
        single = re.compile(r"x\[([^,\]]+)\]$")
        for name, val in sorted(result.variable_values.items()):
            m = pair.match(name)
            if m and val > 0.5:
                a, t = m.group(1), m.group(2)
                decisions.append(f"Assign {_entity_name(spec, t)} → "
                                 f"{_entity_name(spec, a)}")
                continue
            m = single.match(name)
            if m and val > 1e-9:
                decisions.append(f"Allocate {val:g} to "
                                 f"{_entity_name(spec, m.group(1))}")
    else:  # scheduling
        starts = {k[6:-1]: v for k, v in result.variable_values.items()
                  if k.startswith("start[")}
        ends = {k[4:-1]: v for k, v in result.variable_values.items()
                if k.startswith("end[")}
        for tid in sorted(starts, key=lambda t: starts[t]):
            decisions.append(f"{_entity_name(spec, tid)}: start at t={starts[tid]:g}, "
                             f"finish at t={ends.get(tid, 0):g}")
        if "makespan" in result.variable_values:
            decisions.append(f"Everything completes by t="
                             f"{result.variable_values['makespan']:g}")
    return decisions


def build_groups(
    spec: OptimizationSpec, result: SolveResult
) -> tuple[list[AssignmentGroup], list[str]]:
    """Build rich per-agent groupings with resource utilisation.

    Returns (groups, unassigned_labels).  Empty lists if the problem type
    doesn't produce pair assignments (e.g. continuous allocation, scheduling).
    """
    if result.status not in ("optimal", "feasible"):
        return [], []

    # Only makes sense for binary x[agent, task] assignments
    if spec.problem_type not in (ProblemType.ASSIGNMENT, ProblemType.ALLOCATION):
        return [], []

    try:
        from modeling.assignment_model import split_categories
        agents, tasks = split_categories(spec)
    except (ValueError, ImportError, StopIteration):
        return [], []

    # Find capacity constraint for utilisation tracking
    cap_c = next(
        (c for c in spec.constraints if c.constraint_type == ConstraintType.CAPACITY),
        None,
    )
    resource_field: str | None = None
    demand_field: str | None = None
    unit = ""
    if cap_c:
        resource_field = str(cap_c.parameters.get("resource_field", "capacity"))
        demand_field = str(cap_c.parameters.get("demand_field", "demand"))
        # Derive display unit from the field suffix: "capacity_kg" → "kg"
        if "_" in resource_field:
            unit = resource_field.rsplit("_", 1)[-1]

    entity_by_id = {e.id: e for e in spec.entities}

    # Parse assignments: x[agent, task] = 1
    pair = re.compile(r"x\[(.+?),(.+?)\]")
    assignments: dict[str, list[str]] = {}
    for name, val in result.variable_values.items():
        m = pair.match(name)
        if m and val > 0.5:
            a_id, t_id = m.group(1), m.group(2)
            assignments.setdefault(a_id, []).append(t_id)

    groups: list[AssignmentGroup] = []
    for agent in agents:
        task_list = assignments.get(agent.id, [])
        task_labels = [_short_label(spec, t) for t in task_list]

        used: float | None = None
        capacity: float | None = None
        if resource_field and demand_field:
            cap_val = agent.attributes.get(resource_field)
            if cap_val is not None:
                capacity = float(cap_val)
                used = sum(
                    float(entity_by_id[t].attributes.get(demand_field, 0))
                    for t in task_list
                    if t in entity_by_id
                )

        agent_label = _short_label(spec, agent.id)
        groups.append(AssignmentGroup(
            agent_id=agent.id,
            agent_label=agent_label,
            task_ids=task_list,
            task_labels=task_labels,
            used=used,
            capacity=capacity,
            unit=unit,
        ))

    assigned_tasks = {t for ts in assignments.values() for t in ts}
    unassigned = [_short_label(spec, t.id) for t in tasks if t.id not in assigned_tasks]

    return groups, unassigned


def _binding_constraint_names(result: SolveResult) -> list[str]:
    """Return constraint names whose slack is at or near zero (binding at limit)."""
    return [
        name for name, slack in result.constraint_slacks.items()
        if abs(slack) < 1e-6
    ]


def explain(spec: OptimizationSpec, result: SolveResult,
            adapter: LLMAdapter | None = None,
            past_preferences: list[str] | None = None) -> Explanation:
    decisions = build_digest(spec, result)
    groups, unassigned = build_groups(spec, result)
    past_preferences = past_preferences or []

    if result.status == "infeasible":
        summary = ("No solution satisfies all constraints simultaneously. "
                   "The most common causes: total demand exceeds total capacity, "
                   "or conflicting limits. Review the constraints below.")
        return Explanation(summary=summary,
                           binding_constraints=[c.name for c in spec.constraints],
                           caveats=["Try relaxing one constraint and re-solving."])

    binding = _binding_constraint_names(result)

    obj = (f"{result.objective_value:g}" if result.objective_value is not None
           else "n/a")
    base_summary = (f"Solved as a {spec.problem_type.value} problem "
                    f"({result.solver_name}, {result.solve_time_ms:g} ms). "
                    f"Status: {result.status}. Objective "
                    f"({spec.objective.sense.value} {spec.objective.description}): "
                    f"{obj}. {len(decisions)} decisions made.")

    caveats = [a.question for a in spec.ambiguities
               if a.resolution and "proceed" in a.resolution.lower()]

    preference_caveats = [f"Past preference: {p}" for p in past_preferences]

    if adapter is not None:
        facts = {
            "summary": base_summary,
            "decisions": decisions[:50],
            "objective": spec.objective.description,
            "past_preferences": past_preferences,
        }
        try:
            raw = adapter.complete_json(NARRATE_PROMPT, str(facts))
            return Explanation(
                summary=raw.get("summary", base_summary),
                decisions=decisions,
                binding_constraints=binding,
                groups=groups,
                unassigned=unassigned,
                caveats=caveats + preference_caveats + raw.get("caveats", []),
            )
        except LLMError:
            pass  # fall back to deterministic summary

    return Explanation(
        summary=base_summary,
        decisions=decisions,
        binding_constraints=binding,
        groups=groups,
        unassigned=unassigned,
        caveats=caveats + preference_caveats,
    )
