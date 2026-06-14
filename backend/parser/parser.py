"""AI Parser: natural language (+ optional file previews) → OptimizationSpec.

One structured-extraction call constrained to the fixed enum vocabulary.
Deterministic post-processing handles routing detection (v2 scope guard)
and unsupported-constraint downgrade to ambiguities.
"""
from __future__ import annotations

import json
import uuid

from pydantic import ValidationError

from spec.enums import (ConstraintType, ProblemType, ROUTING_SIGNALS,
                        SUPPORTED_CONSTRAINTS)
from spec.schema import Ambiguity, OptimizationSpec
from .llm_adapter import LLMAdapter, LLMError

SYSTEM_PROMPT = f"""You are the parser for an optimization-modeling system.
Convert the user's operational problem into a single JSON object that conforms
EXACTLY to the schema below. Do not invent constraint types or fields.

Output ONLY a JSON object — no prose, no markdown fences.

Schema:
{{
  "problem_type": one of {[t.value for t in ProblemType]},
  "roles": {{"agent_category": str, "task_category": str}} or null,
  "entities": [{{"id": str, "category": str, "attributes": {{str: number|str}}}}],
  "decision_variables": [{{"name": str, "description": str,
                           "var_type": "binary"|"integer"|"continuous",
                           "indexed_by": [category, ...]}}],
  "objective": {{"sense": "minimize"|"maximize", "description": str,
                 "coefficient_field": str or null}},
  "constraints": [{{"name": str,
                    "constraint_type": one of {[c.value for c in ConstraintType]},
                    "description": str,
                    "parameters": {{str: str|number}}}}],
  "ambiguities": [{{"id": str, "question": str, "context": str,
                    "options": [str, ...], "blocking": true|false,
                    "target_constraint": str or null,
                    "resolution_map": {{option_text: {{param_key: param_value}}, ...}} or null}}]
}}

Rules:
- problem_type: "assignment" = matching two sets (workers↔tasks, vans↔packages,
  no routing/ordering); "allocation" = dividing a quantity (budget, capacity,
  hours) across recipients, incl. simplified dispatch; "scheduling" = placing
  tasks/shifts in time.
- For assignment problems ALWAYS set "roles" to identify which category is the
  agent (the entity that does work: van, worker, machine) and which is the task
  (the entity that receives work: package, job, order). This is mandatory when
  there are more than two entity categories (e.g. drivers, vans, packages,
  depots) so the system can reliably build the x[agent, task] variable matrix.
  Example: {{"agent_category": "van", "task_category": "package"}}.
  For non-assignment problems, set "roles" to null.
- Extract entities from the text AND from any file previews provided. If the
  data will come from an uploaded file, you may leave entities empty for that
  category and note it in parameters as {{"entity_category": "..."}}.
- Constraint parameters reference entity attribute FIELD NAMES (e.g.
  {{"resource_field": "capacity_kg", "entity_category": "van",
  "demand_field": "weight_kg", "demand_category": "package"}}) or scalar limits
  (e.g. {{"limit": 5000}}). NEVER write mathematical expressions.
- Common parameter keys: entity_category, resource_field, demand_category,
  demand_field, limit, min, max, count, sense ("<="|">="|"=="),
  duration_field, start_after (for precedence: {{"before": id, "after": id}}).
- For compatibility constraints: agent_field (attribute on agent/worker/van, e.g.
  "skills" or "license_type"), task_field (attribute on task/package, e.g.
  "required_skill" or "vehicle_type"). Values may be comma-separated lists; an
  agent is compatible if ANY of their values matches ANY of the task's values.
- For scheduling tasks: if a task has a release time (earliest start) or
  deadline (latest finish), encode them as numeric entity attributes named
  "release_time" and "deadline" respectively. The scheduler will automatically
  add start[task] >= release_time and end[task] <= deadline constraints.
- Raise an ambiguity (blocking=true) whenever the text leaves a modeling
  decision open: unspecified objective, "at most one or exactly one?",
  missing units, conflicting hints, missing data the model needs.
- When an ambiguity would change a constraint's parameters, set
  target_constraint to that constraint's "name" field, and set resolution_map
  to a dict mapping each option string to the parameter changes it implies.
  Example — "at most one vs exactly one" for a one_per_entity constraint named
  "one_task_per_worker":
    target_constraint: "one_task_per_worker",
    resolution_map: {{"Exactly one task": {{"sense": "==", "count": 1}},
                      "At most one task": {{"sense": "<=", "count": 1}}}}
  For ambiguities that do not modify a specific constraint, leave both null.
"""


class ParseError(RuntimeError):
    pass


def detect_routing(text: str) -> bool:
    low = text.lower()
    return any(sig in low for sig in ROUTING_SIGNALS)


ROUTING_MESSAGE = (
    "This includes route sequencing, which is a planned v2 feature. "
    "For v1, I can optimize which van handles which deliveries, "
    "but not the visiting order."
)


def parse_problem(text: str, adapter: LLMAdapter,
                  file_previews: list[dict] | None = None) -> OptimizationSpec:
    """Run the parser and post-process into a validated OptimizationSpec."""
    user = text
    if file_previews:
        user += "\n\n--- Uploaded file previews ---\n" + json.dumps(file_previews, indent=2)

    spec: OptimizationSpec | None = None
    last_error: ValidationError | None = None
    for attempt in range(3):
        prompt = user
        if last_error is not None:
            prompt += f"\n\nRepair the JSON you previously returned. Validation error:\n{last_error}"
        try:
            raw = adapter.complete_json(SYSTEM_PROMPT, prompt)
        except LLMError as e:
            raise ParseError(str(e)) from e

        raw.setdefault("raw_text", text)
        raw.setdefault("objective", {"sense": "minimize", "description": "unspecified"})
        raw.pop("confidence", None)  # computed deterministically below
        try:
            spec = OptimizationSpec.model_validate(raw)
            break
        except ValidationError as e:
            last_error = e

    if spec is None:
        raise ParseError(
            f"Parser output failed schema validation after 3 attempts: {last_error}"
        ) from last_error

    _flag_routing(spec, text)
    _flag_unsupported_constraints(spec)
    spec.ready_to_solve = not spec.unresolved_ambiguities()
    spec.confidence = _compute_confidence(spec)
    return spec


def _compute_confidence(spec: OptimizationSpec) -> float:
    """Deterministic confidence based on how complete the extracted spec is."""
    checks = [
        spec.objective.description not in ("unspecified", ""),
        len(spec.entities) > 0,
        len(spec.decision_variables) > 0,
        len(spec.constraints) > 0,
        len(spec.unresolved_ambiguities()) == 0,
        spec.objective.coefficient_field is not None,
    ]
    return round(sum(checks) / len(checks), 2)


def _flag_routing(spec: OptimizationSpec, text: str) -> None:
    if detect_routing(text):
        spec.ambiguities.append(Ambiguity(
            id=f"amb-{uuid.uuid4().hex[:8]}",
            question=ROUTING_MESSAGE + " Proceed with assignment-only optimization?",
            context="Route sequencing detected in problem statement.",
            options=["Proceed without route ordering", "Cancel"],
            blocking=True,
        ))


def _flag_unsupported_constraints(spec: OptimizationSpec) -> None:
    """Constraint types outside the (problem_type, constraint_type) builder map
    become user-facing ambiguities instead of silent failures (spec §3)."""
    supported = SUPPORTED_CONSTRAINTS[spec.problem_type]
    kept = []
    for c in spec.constraints:
        if c.constraint_type in supported:
            kept.append(c)
        else:
            spec.ambiguities.append(Ambiguity(
                id=f"amb-{uuid.uuid4().hex[:8]}",
                question=(f"I detected a constraint about '{c.description}' "
                          f"({c.constraint_type.value}), but v1 doesn't support "
                          f"modeling this for {spec.problem_type.value} problems "
                          f"yet. It will be ignored — do you want to proceed?"),
                context=c.name,
                options=["Proceed without it", "Cancel"],
                blocking=True,
            ))
    spec.constraints = kept
