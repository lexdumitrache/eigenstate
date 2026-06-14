"""Validation layer (spec §1): runs after the clarification gate, before modeling.

Checks (in order):
  1. Gate: unresolved ambiguities, missing entities/variables
  2. Structural: duplicate IDs, decision-variable category references
  3. Objective: coefficient field actually exists and is numeric
  4. Constraint refs: category and field names resolve against entity data
  5. Feasibility pre-checks:
       a. Total demand vs total capacity
       b. Individual item larger than the largest single resource
       c. Too many tasks for available agents under one_per_entity
       d. Negative / non-numeric capacity or demand values
"""
from __future__ import annotations

from dataclasses import dataclass, field

from spec.enums import ConstraintType
from spec.schema import Constraint, Entity, OptimizationSpec


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _by_category(entities: list[Entity]) -> dict[str, list[Entity]]:
    out: dict[str, list[Entity]] = {}
    for e in entities:
        out.setdefault(e.category, []).append(e)
    return out


def _to_float(val) -> float | None:
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val))
    except (ValueError, TypeError):
        return None


def validate_spec(spec: OptimizationSpec) -> ValidationReport:
    rep = ValidationReport()
    cats = _by_category(spec.entities)

    # ── 1. Gate checks ────────────────────────────────────────────────────────
    if spec.unresolved_ambiguities():
        rep.errors.append("Unresolved blocking ambiguities — clarification gate "
                          "not passed.")
    if not spec.entities:
        rep.errors.append("No entities present; upload data or describe entities.")
        return rep  # nothing else can run without entities
    if not spec.constraints:
        rep.warnings.append("No constraints extracted — model may be trivial.")
    if not spec.decision_variables:
        rep.warnings.append("No decision variables declared; using defaults for "
                            f"{spec.problem_type.value}.")

    # ── 2. Structural: duplicate entity IDs per category ─────────────────────
    _check_duplicate_ids(cats, rep)

    # ── 3. Decision-variable category references ──────────────────────────────
    for dv in spec.decision_variables:
        for cat in dv.indexed_by:
            if cat not in cats:
                rep.errors.append(f"Variable '{dv.name}' indexed by unknown "
                                  f"category '{cat}'. Known: {sorted(cats)}")

    # ── 4. Objective: coefficient field must exist and be numeric ─────────────
    _check_objective_field(spec, cats, rep)

    # ── 5. Constraint field references ────────────────────────────────────────
    for c in spec.constraints:
        _check_constraint_refs(c, cats, rep)

    # ── 6. Feasibility pre-checks ─────────────────────────────────────────────
    _feasibility_precheck(spec, cats, rep)
    return rep


# ── Duplicate ID check ────────────────────────────────────────────────────────

def _check_duplicate_ids(cats: dict[str, list[Entity]], rep: ValidationReport) -> None:
    for cat, ents in cats.items():
        seen: dict[str, int] = {}
        for e in ents:
            seen[e.id] = seen.get(e.id, 0) + 1
        dupes = sorted(id_ for id_, n in seen.items() if n > 1)
        if dupes:
            rep.errors.append(
                f"Duplicate entity IDs in category '{cat}': {dupes}. "
                f"Each entity must have a unique ID.")


# ── Objective field check ─────────────────────────────────────────────────────

def _check_objective_field(spec: OptimizationSpec,
                           cats: dict[str, list[Entity]],
                           rep: ValidationReport) -> None:
    coeff_field = spec.objective.coefficient_field
    if not coeff_field:
        return  # count-based objective — no field needed

    all_entities = spec.entities
    if not all_entities:
        return

    numeric_count = 0
    non_numeric: list[str] = []
    missing: list[str] = []

    for e in all_entities:
        val = e.attributes.get(coeff_field)
        if val is None:
            # Also check wide-format columns (e.g. cost_van1 on task rows)
            wide_keys = [k for k in e.attributes if k.startswith(f"{coeff_field}_")]
            if wide_keys:
                numeric_count += 1
            else:
                missing.append(e.id)
        elif _to_float(val) is not None:
            numeric_count += 1
        else:
            non_numeric.append(e.id)

    if numeric_count == 0:
        rep.errors.append(
            f"Objective field '{coeff_field}' was not found on any entity. "
            f"Upload data that includes a '{coeff_field}' column, or choose "
            f"a different objective.")
    else:
        if non_numeric:
            rep.errors.append(
                f"Objective field '{coeff_field}' contains non-numeric values "
                f"on entities: {non_numeric[:5]}. All values must be numbers.")
        if missing:
            rep.warnings.append(
                f"Objective field '{coeff_field}' is absent on "
                f"{len(missing)} entit{'y' if len(missing)==1 else 'ies'} "
                f"({missing[:3]}{'...' if len(missing)>3 else ''}). "
                f"Their coefficient will default to 1.0.")


# ── Constraint field reference check ─────────────────────────────────────────

def _check_constraint_refs(c: Constraint, cats: dict[str, list[Entity]],
                           rep: ValidationReport) -> None:
    p = c.parameters
    for cat_key in ("entity_category", "demand_category"):
        cat = p.get(cat_key)
        if cat and cat not in cats:
            rep.errors.append(f"Constraint '{c.name}': category '{cat}' not found "
                              f"in data. Known: {sorted(cats)}")
            return

    for field_key, cat_key in (("resource_field", "entity_category"),
                               ("demand_field", "demand_category"),
                               ("duration_field", "entity_category")):
        fname = p.get(field_key)
        cat = p.get(cat_key)
        if not fname or not cat or cat not in cats:
            continue
        ents = cats[cat]
        missing_field = [e.id for e in ents if fname not in e.attributes]
        if len(missing_field) == len(ents):
            sample = sorted(ents[0].attributes) if ents else []
            rep.errors.append(
                f"Constraint '{c.name}': field '{fname}' missing on all "
                f"'{cat}' entities. Available fields: {sample}")
        elif missing_field:
            rep.warnings.append(
                f"Constraint '{c.name}': field '{fname}' missing on "
                f"{len(missing_field)} '{cat}' entit"
                f"{'y' if len(missing_field)==1 else 'ies'} "
                f"({missing_field[:3]}). Missing values treated as 0.")

        # Non-numeric check for numeric fields
        bad = [e.id for e in ents
               if fname in e.attributes and _to_float(e.attributes[fname]) is None]
        if bad:
            rep.errors.append(
                f"Constraint '{c.name}': field '{fname}' has non-numeric values "
                f"on entities {bad[:5]}. Numeric values are required.")

    if c.constraint_type == ConstraintType.COMPATIBILITY:
        _check_compatibility_refs(c, cats, rep)


def _check_compatibility_refs(c: Constraint, cats: dict[str, list[Entity]],
                              rep: ValidationReport) -> None:
    """Warn when agent_field / task_field are missing from all entities."""
    p = c.parameters
    agent_field = p.get("agent_field")
    task_field = p.get("task_field")
    if not agent_field or not task_field:
        rep.warnings.append(
            f"Constraint '{c.name}' (compatibility): 'agent_field' and "
            f"'task_field' are required parameters. Constraint will have no effect.")
        return
    all_ents = [e for ents in cats.values() for e in ents]
    for fname in (agent_field, task_field):
        covered = [e for e in all_ents if fname in e.attributes]
        if not covered:
            rep.warnings.append(
                f"Constraint '{c.name}' (compatibility): field '{fname}' is "
                f"absent on all entities — constraint will have no effect.")


# ── Feasibility pre-checks ────────────────────────────────────────────────────

def _feasibility_precheck(spec: OptimizationSpec, cats: dict[str, list[Entity]],
                          rep: ValidationReport) -> None:
    for c in spec.constraints:
        if c.constraint_type == ConstraintType.CAPACITY:
            _check_capacity(c, cats, rep)
        elif c.constraint_type == ConstraintType.ONE_PER_ENTITY:
            _check_one_per_entity_coverage(c, cats, spec, rep)


def _check_capacity(c: Constraint, cats: dict[str, list[Entity]],
                    rep: ValidationReport) -> None:
    p = c.parameters
    res_cat, res_f = p.get("entity_category"), p.get("resource_field")
    dem_cat, dem_f = p.get("demand_category"), p.get("demand_field")
    if not all([res_cat, res_f, dem_cat, dem_f]):
        return
    if res_cat not in cats or dem_cat not in cats:
        return

    # Collect numeric capacities
    caps: list[tuple[str, float]] = []
    for e in cats[str(res_cat)]:
        v = _to_float(e.attributes.get(str(res_f)))
        if v is None:
            continue
        if v < 0:
            rep.warnings.append(
                f"Constraint '{c.name}': {res_cat} '{e.id}' has negative "
                f"{res_f} ({v}). Treating as 0.")
            v = 0.0
        caps.append((e.id, v))

    # Collect numeric demands
    dems: list[tuple[str, float]] = []
    for e in cats[str(dem_cat)]:
        v = _to_float(e.attributes.get(str(dem_f)))
        if v is None:
            continue
        if v < 0:
            rep.warnings.append(
                f"Constraint '{c.name}': {dem_cat} '{e.id}' has negative "
                f"{dem_f} ({v}). This is unusual.")
        dems.append((e.id, v))

    if not caps or not dems:
        return

    total_cap = sum(v for _, v in caps)
    total_dem = sum(v for _, v in dems)
    max_cap = max(v for _, v in caps)

    # Total demand vs total capacity
    if total_dem > total_cap:
        rep.errors.append(
            f"Infeasible before solving: total {dem_f} of {dem_cat}s "
            f"({total_dem:g}) exceeds total {res_f} of {res_cat}s "
            f"({total_cap:g}).")

    # Individual item bigger than every resource
    oversized = [(eid, v) for eid, v in dems if v > max_cap]
    if oversized:
        examples = ", ".join(
            f"'{eid}' ({dem_f}={v:g})" for eid, v in oversized[:3])
        suffix = f" (and {len(oversized)-3} more)" if len(oversized) > 3 else ""
        rep.errors.append(
            f"Infeasible before solving: {examples}{suffix} cannot fit in any "
            f"{res_cat} (largest {res_f}={max_cap:g}).")


def _check_one_per_entity_coverage(c: Constraint, cats: dict[str, list[Entity]],
                                   spec: OptimizationSpec,
                                   rep: ValidationReport) -> None:
    """If each agent does at most N tasks, check there are enough agents."""
    p = c.parameters
    sense = str(p.get("sense", "<="))
    if sense not in ("<=", "=="):
        return  # >= sense means agents must be busy — different constraint

    limit = _to_float(p.get("count", p.get("limit", 1)))
    if limit is None:
        limit = 1.0

    agent_cat = p.get("entity_category")
    if not agent_cat or agent_cat not in cats:
        return

    # Find the task category — the other category in the spec
    all_cats = list({e.category for e in spec.entities})
    task_cats = [cat for cat in all_cats if cat != agent_cat]
    if not task_cats:
        return

    agents = cats[agent_cat]
    tasks = cats[task_cats[0]]
    max_tasks = len(agents) * limit

    if len(tasks) > max_tasks:
        rep.errors.append(
            f"Not enough {agent_cat}s: {len(tasks)} {task_cats[0]}s need to be "
            f"covered but {len(agents)} {agent_cat}s can handle at most "
            f"{int(limit)} each (capacity {int(max_tasks)}).")
