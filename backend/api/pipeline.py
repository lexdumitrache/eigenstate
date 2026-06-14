"""Pipeline orchestration: each function advances a Session one stage.
The clarification gate is enforced here — solve() refuses to run unless
the spec passed the gate (spec §1: mandatory, not UX polish)."""
from __future__ import annotations

import threading

from parser.column_mapper import (read_table, rows_to_entities, rows_to_global_params, classify_table)
from spec.schema import CalendarTable, ColumnMapping, PairwiseCostTable, RelationshipTable
from parser.llm_adapter import LLMAdapter, LLMError
from parser.parser import ParseError as _LLMParseError, parse_problem
from spec.enums import SessionStage, ProblemType, ObjectiveSense, TableRole
from validation.validator import validate_spec
from modeling.builder import build_model, UnsupportedConstraintError
from solvers.solver_router import route_and_solve
from explanation.explainer import explain
from feedback.store import get_relevant_preferences
from .session_store import Session
from .errors import (
    ParseError, DataIngestionError, ModelValidationError,
    ModelBuildError, SolverError, SolveCancelledError,
)


class GateError(RuntimeError):
    """Raised when a stage is invoked out of order or a gate is not passed."""


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event and cancel_event.is_set():
        raise SolveCancelledError("Solve was cancelled by the user.")


def run_parse(session: Session, text: str, adapter: LLMAdapter) -> None:
    session.problem_text = text
    previews = []
    for name, content in session.files.items():
        try:
            header, rows = read_table(name, content)
        except Exception as e:
            session.stage = SessionStage.FAILED
            session.error = f"Could not read file '{name}': {e}"
            session.error_code = "data_ingestion_error"
            return
        session.file_rows[name] = rows
        previews.append({"file": name, "header": header, "sample": rows[:5],
                         "row_count": len(rows)})
    try:
        spec = parse_problem(text, adapter, previews or None)
    except (_LLMParseError, LLMError) as e:
        session.stage = SessionStage.FAILED
        session.error = str(e)
        session.error_code = "parse_error"
        return
    except Exception as e:
        session.stage = SessionStage.FAILED
        session.error = f"Unexpected parse error: {e}"
        session.error_code = "parse_error"
        return
    for name in session.files:
        rows = session.file_rows[name]
        header = list(rows[0].keys()) if rows else []
        try:
            table = classify_table(name, header, rows, adapter)
        except Exception as e:
            session.stage = SessionStage.FAILED
            session.error = f"Column mapping failed for '{name}': {e}"
            session.error_code = "mapping_error"
            return
        if isinstance(table, PairwiseCostTable):
            spec.pairwise_tables.append(table)
        elif isinstance(table, RelationshipTable):
            table.confirmed = True
            spec.relationship_tables.append(table)
        elif isinstance(table, CalendarTable):
            table.confirmed = True
            spec.calendar_tables.append(table)
        else:  # ColumnMapping (entity or parameter)
            spec.column_mappings.append(table)
    session.spec = spec
    session.error = None
    session.error_code = None
    session.recompute_stage_after_parse()


def confirm_column_mapping(session: Session, file_name: str,
                           column_to_field: dict[str, str] | None,
                           entity_category: str | None,
                           id_column: str | None) -> None:
    if session.spec is None:
        raise GateError("Parse first.")
    mapping = next((m for m in session.spec.column_mappings
                    if m.file_name == file_name), None)
    if mapping is None:
        raise GateError(f"No mapping for file '{file_name}'.")
    if column_to_field:
        mapping.column_to_field = column_to_field
    if entity_category:
        mapping.entity_category = entity_category
    if id_column is not None:
        mapping.id_column = id_column
    mapping.confirmed = True
    rows = session.file_rows[file_name]
    if mapping.table_role == TableRole.PARAMETER:
        session.spec.global_params.update(rows_to_global_params(mapping, rows))
    else:
        session.spec.entities = [e for e in session.spec.entities
                                 if not (e.category == mapping.entity_category
                                         and e.source_file == file_name)]
        session.spec.entities.extend(rows_to_entities(mapping, rows))
    session.recompute_stage_after_parse()


def confirm_pairwise_table(session: Session, file_name: str,
                           agent_column: str | None = None,
                           task_column: str | None = None,
                           cost_column: str | None = None,
                           agent_category: str | None = None,
                           task_category: str | None = None) -> None:
    """Confirm (and optionally correct) a suggested pairwise cost-table mapping."""
    if session.spec is None:
        raise GateError("Parse first.")
    table = next((t for t in session.spec.pairwise_tables
                  if t.file_name == file_name), None)
    if table is None:
        raise GateError(f"No pairwise table suggestion for file '{file_name}'.")
    if agent_column:
        table.agent_column = agent_column
    if task_column:
        table.task_column = task_column
    if cost_column:
        table.cost_column = cost_column
    if agent_category:
        table.agent_category = agent_category
    if task_category:
        table.task_category = task_category
    table.confirmed = True
    session.recompute_stage_after_parse()


def resolve_ambiguities(session: Session, answers: dict[str, str]) -> None:
    """answers: ambiguity id -> resolution text (or 'proceed with my assumptions').

    Applies two side-effects beyond storing the resolution:
    1. If the resolution is 'cancel', the session is immediately failed/cancelled.
    2. If the ambiguity carries a resolution_map, the matching parameter changes
       are written back onto the linked constraint (target_constraint by name).
    """
    if session.spec is None:
        raise GateError("Parse first.")
    for amb in session.spec.ambiguities:
        if amb.id not in answers:
            continue
        resolution = answers[amb.id]
        amb.resolution = resolution

        if resolution.strip().lower() == "cancel":
            session.stage = SessionStage.CANCELLED
            session.error = "User cancelled: the problem cannot be modelled safely in v1."
            session.error_code = "cancelled"
            return

        if amb.target_constraint and amb.resolution_map:
            patch = amb.resolution_map.get(resolution)
            if patch:
                _apply_constraint_patch(session.spec, amb.target_constraint, patch)

    session.recompute_stage_after_parse()


def _apply_constraint_patch(spec, constraint_name: str,
                            patch: dict[str, str | float | int]) -> None:
    for c in spec.constraints:
        if c.name == constraint_name:
            c.parameters.update(patch)
            return


def edit_spec(session: Session, edits) -> None:
    """Apply user edits to the extracted spec (problem_type, objective, constraint params)."""
    spec = session.spec
    if spec is None:
        raise GateError("No spec to edit yet.")
    if edits.problem_type:
        spec.problem_type = ProblemType(edits.problem_type)
    if edits.objective_sense:
        spec.objective.sense = ObjectiveSense(edits.objective_sense)
    if edits.objective_coefficient_field is not None:
        spec.objective.coefficient_field = edits.objective_coefficient_field or None
    for patch in edits.constraint_patches:
        name = patch.get("name")
        params = patch.get("parameters", {})
        if name and params:
            _apply_constraint_patch(spec, name, params)
    session.recompute_stage_after_parse()


def run_solve(session: Session, adapter: LLMAdapter | None = None,
              cancel_event: threading.Event | None = None) -> None:
    if session.stage == SessionStage.CANCELLED:
        raise GateError("Session was cancelled by the user.")
    if session.spec is None or session.stage != SessionStage.READY:
        raise GateError("Session is not READY — the clarification gate must be "
                        "passed before solving.")
    spec = session.spec

    _check_cancel(cancel_event)

    report = validate_spec(spec)
    session.validation = {"errors": report.errors, "warnings": report.warnings}
    if not report.ok:
        session.stage = SessionStage.FAILED
        session.error = "; ".join(report.errors)
        session.error_code = "validation_error"
        raise ModelValidationError(session.error)
    session.stage = SessionStage.VALIDATED

    _check_cancel(cancel_event)

    cost_table: dict[tuple[str, str], float] = {}
    for pt in spec.pairwise_tables:
        if not pt.confirmed:
            continue
        for row in session.file_rows.get(pt.file_name, []):
            agent_id = str(row.get(pt.agent_column, ""))
            task_id = str(row.get(pt.task_column, ""))
            try:
                cost = float(row[pt.cost_column])
            except (KeyError, ValueError, TypeError):
                continue
            if agent_id and task_id:
                cost_table[(agent_id, task_id)] = cost

    calendar_windows: dict[str, tuple[int, int]] = {}
    for cal in spec.calendar_tables:
        if not cal.confirmed:
            continue
        for row in session.file_rows.get(cal.file_name, []):
            entity_id = str(row.get(cal.entity_column, ""))
            if not entity_id:
                continue
            try:
                start = int(float(row[cal.start_column])) if cal.start_column and cal.start_column in row else None
                end = int(float(row[cal.end_column])) if cal.end_column and cal.end_column in row else None
            except (TypeError, ValueError):
                continue
            if start is not None or end is not None:
                calendar_windows[entity_id] = (start or 0, end or 0)

    allowed_pairs: set[tuple[str, str]] = set()
    for rt in spec.relationship_tables:
        if not rt.confirmed:
            continue
        for row in session.file_rows.get(rt.file_name, []):
            from_id = str(row.get(rt.from_column, ""))
            to_id = str(row.get(rt.to_column, ""))
            if from_id and to_id:
                allowed_pairs.add((from_id, to_id))

    entities_data: dict = {}
    if cost_table:
        entities_data["cost_tables"] = cost_table
    if spec.global_params:
        entities_data["global_params"] = spec.global_params
    if calendar_windows:
        entities_data["calendar_windows"] = calendar_windows
    if allowed_pairs:
        entities_data["allowed_pairs"] = allowed_pairs

    try:
        prob, variables = build_model(spec, entities_data)
    except (UnsupportedConstraintError, ValueError) as e:
        session.stage = SessionStage.FAILED
        session.error = str(e)
        session.error_code = "model_build_error"
        raise ModelBuildError(str(e)) from e
    except Exception as e:
        session.stage = SessionStage.FAILED
        session.error = f"Model build failed: {e}"
        session.error_code = "model_build_error"
        raise ModelBuildError(session.error) from e
    session.stage = SessionStage.MODELED

    _check_cancel(cancel_event)

    try:
        session.result = route_and_solve(spec, prob, variables)
    except Exception as e:
        session.stage = SessionStage.FAILED
        session.error = f"Solver error: {e}"
        session.error_code = "solver_error"
        raise SolverError(session.error) from e
    session.stage = SessionStage.SOLVED

    _check_cancel(cancel_event)

    try:
        past_prefs = get_relevant_preferences(spec.problem_type.value)
        session.explanation = explain(spec, session.result, adapter, past_prefs)
    except Exception as e:
        # Explanation failure is non-fatal — the result is still usable.
        session.explanation = None
        session.error = f"Explanation unavailable: {e}"
        session.error_code = None
    session.stage = SessionStage.EXPLAINED
