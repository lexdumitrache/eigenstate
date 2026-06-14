"""Constraint-type-driven model builder (spec §3).

One hardcoded builder function per (problem_type, constraint_type) pair —
no expression interpreter. Unsupported pairs raise UnsupportedConstraintError
(which the parser should already have converted to an ambiguity upstream;
this is the defensive backstop).
"""
from __future__ import annotations

from spec.enums import ConstraintType, ProblemType
from spec.schema import Constraint, OptimizationSpec
from .allocation_model import (AllocationModelBuilder, build_budget_limit,
                               build_capacity_allocation,
                               build_compatibility_allocation,
                               build_demand_coverage_allocation,
                               build_max_allocation, build_min_allocation,
                               build_one_per_entity_allocation,
                               build_time_budget_allocation)
from .assignment_model import (AssignmentModelBuilder,
                               build_capacity_assignment,
                               build_compatibility_assignment,
                               build_demand_coverage_assignment,
                               build_one_per_entity_assignment,
                               build_time_budget_assignment)
from .scheduling_model import (SchedulingModelBuilder, build_capacity_cpsat,
                               build_demand_coverage_cpsat,
                               build_no_overlap_cpsat, build_precedence_cpsat,
                               build_time_budget_cpsat)


class UnsupportedConstraintError(Exception):
    def __init__(self, constraint: Constraint):
        self.constraint = constraint
        super().__init__(
            f"No builder for ({constraint.constraint_type.value}) — "
            f"constraint '{constraint.name}' is out of v1 scope.")


MODEL_BUILDERS = {
    ProblemType.ASSIGNMENT: AssignmentModelBuilder(),
    ProblemType.ALLOCATION: AllocationModelBuilder(),
    ProblemType.SCHEDULING: SchedulingModelBuilder(),
}

CONSTRAINT_BUILDERS = {
    (ProblemType.ASSIGNMENT, ConstraintType.ONE_PER_ENTITY): build_one_per_entity_assignment,
    (ProblemType.ASSIGNMENT, ConstraintType.DEMAND_COVERAGE): build_demand_coverage_assignment,
    (ProblemType.ASSIGNMENT, ConstraintType.CAPACITY): build_capacity_assignment,
    (ProblemType.ASSIGNMENT, ConstraintType.TIME_BUDGET): build_time_budget_assignment,
    (ProblemType.ASSIGNMENT, ConstraintType.COMPATIBILITY): build_compatibility_assignment,

    (ProblemType.ALLOCATION, ConstraintType.CAPACITY): build_capacity_allocation,
    (ProblemType.ALLOCATION, ConstraintType.BUDGET_LIMIT): build_budget_limit,
    (ProblemType.ALLOCATION, ConstraintType.MIN_ALLOCATION): build_min_allocation,
    (ProblemType.ALLOCATION, ConstraintType.MAX_ALLOCATION): build_max_allocation,
    (ProblemType.ALLOCATION, ConstraintType.DEMAND_COVERAGE): build_demand_coverage_allocation,
    (ProblemType.ALLOCATION, ConstraintType.ONE_PER_ENTITY): build_one_per_entity_allocation,
    (ProblemType.ALLOCATION, ConstraintType.TIME_BUDGET): build_time_budget_allocation,
    (ProblemType.ALLOCATION, ConstraintType.COMPATIBILITY): build_compatibility_allocation,

    (ProblemType.SCHEDULING, ConstraintType.NO_OVERLAP): build_no_overlap_cpsat,
    (ProblemType.SCHEDULING, ConstraintType.PRECEDENCE): build_precedence_cpsat,
    (ProblemType.SCHEDULING, ConstraintType.TIME_BUDGET): build_time_budget_cpsat,
    (ProblemType.SCHEDULING, ConstraintType.DEMAND_COVERAGE): build_demand_coverage_cpsat,
    (ProblemType.SCHEDULING, ConstraintType.CAPACITY): build_capacity_cpsat,
}


def build_model(spec: OptimizationSpec, entities_data: dict | None = None):
    entities_data = entities_data or {}
    builder = MODEL_BUILDERS[spec.problem_type]
    prob, variables = builder.init_model(spec, entities_data)
    for constraint in spec.constraints:
        fn = CONSTRAINT_BUILDERS.get((spec.problem_type, constraint.constraint_type))
        if fn is None:
            raise UnsupportedConstraintError(constraint)
        fn(prob, variables, constraint, entities_data)
    builder.set_objective(prob, variables, spec.objective, entities_data)
    return prob, variables
