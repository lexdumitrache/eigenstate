"""The OptimizationSpec — the single validated contract between the AI parser
and the deterministic modeling layer. No free-form expression fields."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .enums import ConstraintType, ObjectiveSense, ProblemType, TableRole


class Entity(BaseModel):
    """One row of problem data: a van, a worker, a task, a package..."""
    id: str
    category: str                                  # e.g. "van", "package", "employee"
    attributes: dict[str, str | float | int] = Field(default_factory=dict)
    source_file: str = ""                          # which uploaded file this row came from


class DecisionVariable(BaseModel):
    """Describes a variable family, e.g. x[van, package] ∈ {0,1}."""
    name: str
    description: str
    var_type: str = Field(pattern="^(binary|integer|continuous)$")
    indexed_by: list[str]  # entity categories this variable is indexed over


class Objective(BaseModel):
    sense: ObjectiveSense
    description: str
    # Field on the *pair* or *entity* that carries the coefficient,
    # e.g. "cost", "distance_km", "priority". Resolved against entity attributes.
    coefficient_field: str | None = None


class Constraint(BaseModel):
    name: str
    constraint_type: ConstraintType
    description: str
    # References to entity fields / categories, never expressions.
    # e.g. {"resource_field": "capacity_kg", "entity_category": "van",
    #       "demand_field": "weight_kg", "demand_category": "package"}
    parameters: dict[str, str | float | int] = Field(default_factory=dict)


class Ambiguity(BaseModel):
    id: str
    question: str
    context: str = ""
    options: list[str] = Field(default_factory=list)
    resolution: str | None = None    # filled by the clarification gate
    blocking: bool = True
    # Optional: links this ambiguity to a constraint and maps each option to the
    # parameter changes that should be applied when that option is chosen.
    # e.g. {"Exactly one": {"sense": "=="}, "At most one": {"sense": "<="}}
    target_constraint: str | None = None
    resolution_map: dict[str, dict[str, str | float | int]] | None = None


class ColumnMapping(BaseModel):
    file_name: str
    column_to_field: dict[str, str]  # csv column -> Entity attribute name
    entity_category: str             # what these rows represent
    id_column: str | None = None
    table_role: TableRole = TableRole.ENTITY
    confirmed: bool = False


class PairwiseCostTable(BaseModel):
    """A CSV that maps (agent_id, task_id) -> cost, used as the objective matrix."""
    file_name: str
    agent_column: str      # column holding agent/van/worker IDs
    task_column: str       # column holding task/package/job IDs
    cost_column: str       # column holding the numeric cost/time/distance
    agent_category: str    # entity category the agent column refers to
    task_category: str     # entity category the task column refers to
    confirmed: bool = False


class RelationshipTable(BaseModel):
    """A CSV whose rows link two entity categories (e.g. driver→route, employee→shift)."""
    file_name: str
    from_column: str        # column with the "from" entity IDs
    to_column: str          # column with the "to" entity IDs
    from_category: str      # entity category from_column refers to
    to_category: str        # entity category to_column refers to
    value_column: str | None = None   # optional numeric weight on the edge
    column_to_field: dict[str, str] = Field(default_factory=dict)
    confirmed: bool = False


class CalendarTable(BaseModel):
    """A CSV that encodes time-based availability or scheduling windows for entities."""
    file_name: str
    entity_column: str       # column with entity IDs
    entity_category: str     # entity category the entity_column refers to
    start_column: str | None = None   # availability/window start
    end_column: str | None = None     # availability/window end
    column_to_field: dict[str, str] = Field(default_factory=dict)
    confirmed: bool = False


class Roles(BaseModel):
    """Explicit category roles for assignment problems.
    Avoids heuristic guessing when there are more than two entity categories.
    """
    agent_category: str   # the category that "does" things (e.g. "van", "worker")
    task_category: str    # the category that "gets done" (e.g. "package", "job")


class OptimizationSpec(BaseModel):
    problem_type: ProblemType
    raw_text: str
    entities: list[Entity] = Field(default_factory=list)
    decision_variables: list[DecisionVariable] = Field(default_factory=list)
    objective: Objective
    constraints: list[Constraint] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    column_mappings: list[ColumnMapping] = Field(default_factory=list)
    pairwise_tables: list[PairwiseCostTable] = Field(default_factory=list)
    relationship_tables: list[RelationshipTable] = Field(default_factory=list)
    calendar_tables: list[CalendarTable] = Field(default_factory=list)
    global_params: dict[str, str | float | int] = Field(default_factory=dict)
    roles: Roles | None = None          # explicit agent/task split for assignment
    confidence: float = Field(ge=0, le=1, default=0.5)
    ready_to_solve: bool = False

    def unresolved_ambiguities(self) -> list[Ambiguity]:
        return [a for a in self.ambiguities if a.blocking and a.resolution is None]


class SolveResult(BaseModel):
    status: str
    objective_value: float | None = None
    variable_values: dict[str, float] = Field(default_factory=dict)
    constraint_slacks: dict[str, float] = Field(default_factory=dict)
    solver_name: str = ""
    solve_time_ms: float = 0.0
    message: str = ""


class AssignmentGroup(BaseModel):
    """One agent with its assigned tasks and optional resource utilisation."""
    agent_id: str
    agent_label: str
    task_ids: list[str] = Field(default_factory=list)
    task_labels: list[str] = Field(default_factory=list)
    used: float | None = None      # total demand consumed
    capacity: float | None = None  # agent's capacity limit
    unit: str = ""                 # e.g. "kg", "hrs"


class Explanation(BaseModel):
    summary: str
    decisions: list[str] = Field(default_factory=list)
    binding_constraints: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    groups: list[AssignmentGroup] = Field(default_factory=list)
    unassigned: list[str] = Field(default_factory=list)


class FeedbackChange(BaseModel):
    original_decision: str   # the decision string as shown in the explanation
    user_change: str         # what the user actually did instead
    reason: str              # user-provided reason


class FeedbackEntry(BaseModel):
    session_id: str
    timestamp: str
    problem_type: str
    accepted: bool           # was the overall plan accepted without changes?
    changes: list[FeedbackChange] = Field(default_factory=list)
    inferred_preferences: list[str] = Field(default_factory=list)
