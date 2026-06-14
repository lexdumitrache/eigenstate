"""Fixed vocabularies for the Eigenstate pipeline.

The LLM parser is constrained to these enums — there is no free-form
expression interpretation anywhere in the system (see spec section 3).
"""
from enum import Enum


class ProblemType(str, Enum):
    ASSIGNMENT = "assignment"
    ALLOCATION = "allocation"
    SCHEDULING = "scheduling"


class ConstraintType(str, Enum):
    CAPACITY = "capacity"                # resource/vehicle/worker capacity limit
    DEMAND_COVERAGE = "demand_coverage"  # each task/package/shift must be covered
    ONE_PER_ENTITY = "one_per_entity"    # each worker/van does at most/exactly N tasks
    TIME_BUDGET = "time_budget"          # max hours/time per entity
    BUDGET_LIMIT = "budget_limit"        # total spend <= budget
    MIN_ALLOCATION = "min_allocation"    # entity must receive >= X
    MAX_ALLOCATION = "max_allocation"    # entity must receive <= X
    NO_OVERLAP = "no_overlap"            # scheduling: no double-booking
    PRECEDENCE = "precedence"            # task A before task B
    COMPATIBILITY = "compatibility"      # x[i,j]=0 if agent/task fields don't match


class ObjectiveSense(str, Enum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


class SessionStage(str, Enum):
    CREATED = "created"
    PARSED = "parsed"
    AWAITING_COLUMN_MAPPING = "awaiting_column_mapping"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    READY = "ready"
    VALIDATED = "validated"
    MODELED = "modeled"
    SOLVED = "solved"
    EXPLAINED = "explained"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TableRole(str, Enum):
    ENTITY = "entity"           # rows are standalone objects: vans, packages, employees
    RELATIONSHIP = "relationship"  # rows link two entity types: driver→route
    COST_MATRIX = "cost_matrix"    # pairwise numeric cost/time/distance
    CALENDAR = "calendar"          # time-based availability or scheduling windows
    PARAMETER = "parameter"        # global configuration values


class SolverStatus(str, Enum):
    OPTIMAL = "optimal"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    UNBOUNDED = "unbounded"
    ERROR = "error"


# Which constraint types each problem family supports in v1.
# Anything outside this map becomes an Ambiguity surfaced to the user.
SUPPORTED_CONSTRAINTS: dict[ProblemType, set[ConstraintType]] = {
    ProblemType.ASSIGNMENT: {
        ConstraintType.ONE_PER_ENTITY,
        ConstraintType.DEMAND_COVERAGE,
        ConstraintType.CAPACITY,
        ConstraintType.TIME_BUDGET,
        ConstraintType.COMPATIBILITY,
    },
    ProblemType.ALLOCATION: {
        ConstraintType.CAPACITY,
        ConstraintType.BUDGET_LIMIT,
        ConstraintType.MIN_ALLOCATION,
        ConstraintType.MAX_ALLOCATION,
        ConstraintType.DEMAND_COVERAGE,
        ConstraintType.ONE_PER_ENTITY,
        ConstraintType.TIME_BUDGET,
        ConstraintType.COMPATIBILITY,
    },
    ProblemType.SCHEDULING: {
        ConstraintType.NO_OVERLAP,
        ConstraintType.PRECEDENCE,
        ConstraintType.DEMAND_COVERAGE,
        ConstraintType.TIME_BUDGET,
        ConstraintType.CAPACITY,
    },
}

# Keywords that suggest the user actually needs route sequencing (v2 scope).
ROUTING_SIGNALS = [
    "in what order", "visiting order", "route order", "sequence of stops",
    "shortest route", "travel time between", "tsp", "vehicle routing",
    "multi-stop route", "optimal route",
]
