"""Edge-case tests for the pipeline layer (spec §27 priority list).

All offline via DeterministicStub or custom adapters — no real LLM calls.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from parser.llm_adapter import DeterministicStub, LLMAdapter, LLMError
from api.pipeline import (
    GateError, confirm_column_mapping, resolve_ambiguities, run_parse, run_solve,
)
from api.session_store import SessionStore
from spec.enums import SessionStage


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_session():
    return SessionStore().create()


ASSIGNMENT_SPEC = {
    "problem_type": "assignment",
    "entities": [
        {"id": "w1", "category": "worker", "attributes": {"name": "Ana", "cost": 4}},
        {"id": "w2", "category": "worker", "attributes": {"name": "Bo", "cost": 6}},
        {"id": "t1", "category": "task", "attributes": {"cost_w1": 4, "cost_w2": 8}},
        {"id": "t2", "category": "task", "attributes": {"cost_w1": 6, "cost_w2": 3}},
    ],
    "decision_variables": [{"name": "x", "description": "worker does task",
                            "var_type": "binary", "indexed_by": ["worker", "task"]}],
    "objective": {"sense": "minimize", "description": "total cost",
                  "coefficient_field": "cost"},
    "constraints": [
        {"name": "each_task_covered", "constraint_type": "demand_coverage",
         "description": "every task done once", "parameters": {"sense": "=="}},
        {"name": "one_task_per_worker", "constraint_type": "one_per_entity",
         "description": "each worker at most one task",
         "parameters": {"entity_category": "worker", "count": 1, "sense": "<="}},
    ],
    "ambiguities": [],
    "confidence": 0.9,
}

AMBIGUOUS_SPEC = {
    **ASSIGNMENT_SPEC,
    "ambiguities": [
        {
            "id": "amb-1",
            "question": "At most one task per worker, or exactly one?",
            "context": "",
            "options": ["at most one", "exactly one"],
            "blocking": True,
            "target_constraint": "one_task_per_worker",
            "resolution_map": {
                "exactly one": {"sense": "==", "count": 1},
                "at most one": {"sense": "<=", "count": 1},
            },
        }
    ],
}


# ── 1. Cancel clarification stops pipeline ───────────────────────────────────

def test_cancel_stops_pipeline():
    """Answering 'cancel' to an ambiguity sets session to CANCELLED and
    blocks all further solving."""
    s = make_session()
    run_parse(s, "Assign workers to tasks", DeterministicStub([AMBIGUOUS_SPEC]))
    assert s.stage == SessionStage.AWAITING_CLARIFICATION

    resolve_ambiguities(s, {"amb-1": "cancel"})
    assert s.stage == SessionStage.CANCELLED

    with pytest.raises(GateError, match="cancelled"):
        run_solve(s)


def test_cancel_resolve_is_case_insensitive():
    s = make_session()
    run_parse(s, "Assign workers to tasks", DeterministicStub([AMBIGUOUS_SPEC]))
    resolve_ambiguities(s, {"amb-1": "  CANCEL  "})
    assert s.stage == SessionStage.CANCELLED


# ── 2. Clarification with resolution_map patches constraint ──────────────────

def test_clarification_exactly_one_updates_constraint():
    """When the user picks an option that has a resolution_map entry, the
    linked constraint's parameters are updated before solving."""
    s = make_session()
    run_parse(s, "Assign workers to tasks", DeterministicStub([AMBIGUOUS_SPEC]))
    assert s.stage == SessionStage.AWAITING_CLARIFICATION

    resolve_ambiguities(s, {"amb-1": "exactly one"})
    assert s.stage == SessionStage.READY

    # The constraint should now have sense == "=="
    c = next(c for c in s.spec.constraints if c.name == "one_task_per_worker")
    assert c.parameters["sense"] == "=="


def test_clarification_at_most_one_updates_constraint():
    s = make_session()
    run_parse(s, "Assign workers to tasks", DeterministicStub([AMBIGUOUS_SPEC]))
    resolve_ambiguities(s, {"amb-1": "at most one"})
    c = next(c for c in s.spec.constraints if c.name == "one_task_per_worker")
    assert c.parameters["sense"] == "<="


def test_clarification_no_resolution_map_still_resolves():
    """Ambiguities without a resolution_map just get their resolution stored."""
    spec = {
        **ASSIGNMENT_SPEC,
        "ambiguities": [
            {"id": "amb-x", "question": "Free text?", "context": "",
             "options": [], "blocking": True,
             "target_constraint": None, "resolution_map": None}
        ],
    }
    s = make_session()
    run_parse(s, "Assign workers", DeterministicStub([spec]))
    assert s.stage == SessionStage.AWAITING_CLARIFICATION
    resolve_ambiguities(s, {"amb-x": "proceed with my assumptions"})
    assert s.stage == SessionStage.READY


# ── 3. Gate enforcement ───────────────────────────────────────────────────────

def test_solve_before_parse_raises_gate_error():
    s = make_session()
    with pytest.raises(GateError):
        run_solve(s)


def test_solve_during_clarification_raises_gate_error():
    s = make_session()
    run_parse(s, "Assign workers to tasks", DeterministicStub([AMBIGUOUS_SPEC]))
    assert s.stage == SessionStage.AWAITING_CLARIFICATION
    with pytest.raises(GateError):
        run_solve(s)


def test_cancelled_session_blocks_solve():
    s = make_session()
    run_parse(s, "Assign workers to tasks", DeterministicStub([AMBIGUOUS_SPEC]))
    resolve_ambiguities(s, {"amb-1": "cancel"})
    with pytest.raises(GateError, match="cancelled"):
        run_solve(s)


# ── 4. Unsupported constraint becomes ambiguity ───────────────────────────────

def test_unsupported_constraint_becomes_ambiguity():
    """A constraint type not in SUPPORTED_CONSTRAINTS for that problem type
    is silently removed and replaced with a blocking ambiguity."""
    spec = {
        **ASSIGNMENT_SPEC,
        "constraints": [
            # NO_OVERLAP is not valid for assignment problems
            {"name": "no_dup", "constraint_type": "no_overlap",
             "description": "no double booking", "parameters": {}},
        ],
    }
    s = make_session()
    run_parse(s, "Assign workers", DeterministicStub([spec]))
    assert s.stage == SessionStage.AWAITING_CLARIFICATION
    # The unsupported constraint should have been converted to an ambiguity
    assert any("no_overlap" in a.question or "no double booking" in a.question
               for a in s.spec.ambiguities)
    # And removed from the constraint list
    assert not any(c.name == "no_dup" for c in s.spec.constraints)


# ── 5. Malformed LLM output ───────────────────────────────────────────────────

class _AlwaysErrorAdapter(LLMAdapter):
    def complete_json(self, system: str, user: str) -> dict:
        raise LLMError("Simulated network/parse failure")


def test_malformed_llm_json_fails_gracefully():
    """LLMError from the adapter is caught and the session moves to FAILED."""
    s = make_session()
    run_parse(s, "Assign workers to tasks", _AlwaysErrorAdapter())
    assert s.stage == SessionStage.FAILED
    assert s.error_code == "parse_error"
    assert s.error  # some message present


class _InvalidSchemaStub(LLMAdapter):
    """Returns JSON dicts that fail pydantic validation on every attempt."""
    def __init__(self):
        self._calls = 0

    def complete_json(self, system: str, user: str) -> dict:
        self._calls += 1
        # Missing required 'objective' and has an invalid problem_type
        return {"problem_type": "totally_wrong", "entities": []}


def test_llm_repair_loop_exhausted():
    """After 3 failed validation attempts the parser raises ParseError and
    the session ends in FAILED stage."""
    stub = _InvalidSchemaStub()
    s = make_session()
    run_parse(s, "Assign workers to tasks", stub)
    assert s.stage == SessionStage.FAILED
    assert s.error_code == "parse_error"
    assert stub._calls == 3  # parser tried 3 times


# ── 6. Bad / empty CSV ───────────────────────────────────────────────────────

def test_bad_csv_fails_gracefully():
    """Passing a file with non-CSV binary content → FAILED with ingestion error."""
    s = make_session()
    s.files["data.csv"] = b"\x00\x01\x02 not valid utf-8 \xff\xfe"
    # The file read should fail or produce nonsense; we just need no crash
    spec_stub = {**ASSIGNMENT_SPEC}
    run_parse(s, "Assign workers", DeterministicStub([spec_stub]))
    # May succeed with empty rows OR fail — either is fine, no exception leak
    assert s.stage in (SessionStage.FAILED, SessionStage.READY,
                       SessionStage.AWAITING_COLUMN_MAPPING,
                       SessionStage.AWAITING_CLARIFICATION)


def test_empty_csv_content():
    """A zero-byte CSV file is handled without crashing."""
    s = make_session()
    s.files["empty.csv"] = b""
    spec_stub = {**ASSIGNMENT_SPEC}
    run_parse(s, "Assign workers", DeterministicStub([spec_stub,
        {"table_role": "entity", "entity_category": "worker",
         "id_column": None, "column_to_field": {}}]))
    assert s.stage in (SessionStage.FAILED, SessionStage.READY,
                       SessionStage.AWAITING_COLUMN_MAPPING,
                       SessionStage.AWAITING_CLARIFICATION)


def test_csv_with_only_header_row():
    """A CSV with a header row but no data rows is handled gracefully."""
    s = make_session()
    s.files["workers.csv"] = b"id,name,cost\n"
    run_parse(s, "Assign workers", DeterministicStub([
        ASSIGNMENT_SPEC,
        {"table_role": "entity", "entity_category": "worker",
         "id_column": "id", "column_to_field": {"id": "id", "name": "name", "cost": "cost"}},
    ]))
    # Should not crash; may end up in any non-exception state
    assert s.stage in (SessionStage.FAILED, SessionStage.READY,
                       SessionStage.AWAITING_COLUMN_MAPPING,
                       SessionStage.AWAITING_CLARIFICATION)


# ── 7. Column mapping override ────────────────────────────────────────────────

PACKAGES_CSV = b"pkg,weight\np1,300\np2,500\n"
VANS_CSV = b"van,cap\nv1,600\nv2,500\n"

DISPATCH_SPEC = {
    "problem_type": "allocation",
    "entities": [],
    "decision_variables": [{"name": "x", "description": "van takes package",
                            "var_type": "binary", "indexed_by": ["van", "package"]}],
    "objective": {"sense": "minimize", "description": "assignments",
                  "coefficient_field": None},
    "constraints": [
        {"name": "cover", "constraint_type": "demand_coverage",
         "description": "every package delivered", "parameters": {"sense": "=="}},
        {"name": "van_capacity", "constraint_type": "capacity",
         "description": "weight within van capacity",
         "parameters": {"entity_category": "van", "resource_field": "capacity_kg",
                        "demand_category": "package", "demand_field": "weight_kg"}},
    ],
    "ambiguities": [],
    "confidence": 0.85,
}

_PKG_MAP = {"entity_category": "package", "id_column": "pkg",
            "column_to_field": {"pkg": "pkg", "weight": "weight_kg"}}
_VAN_MAP = {"entity_category": "van", "id_column": "van",
            "column_to_field": {"van": "van", "cap": "capacity_kg"}}


def test_wrong_column_mapping_override():
    """User can override the suggested field name during confirm_column_mapping."""
    s = make_session()
    s.files["packages.csv"] = PACKAGES_CSV
    s.files["vans.csv"] = VANS_CSV
    run_parse(s, "Assign packages to vans",
              DeterministicStub([DISPATCH_SPEC, _PKG_MAP, _VAN_MAP]))
    assert s.stage == SessionStage.AWAITING_COLUMN_MAPPING

    # Override: remap 'weight' column to a different field name
    confirm_column_mapping(
        s, "packages.csv",
        column_to_field={"pkg": "package_id", "weight": "mass_kg"},
        entity_category="package",
        id_column="pkg",
    )
    # Check the override stuck
    pkg_mapping = next(m for m in s.spec.column_mappings if m.file_name == "packages.csv")
    assert pkg_mapping.column_to_field["weight"] == "mass_kg"
    assert pkg_mapping.confirmed


def test_confirm_unknown_file_raises_gate_error():
    s = make_session()
    s.files["packages.csv"] = PACKAGES_CSV
    s.files["vans.csv"] = VANS_CSV
    run_parse(s, "Assign packages to vans",
              DeterministicStub([DISPATCH_SPEC, _PKG_MAP, _VAN_MAP]))
    with pytest.raises(GateError, match="No mapping for file"):
        confirm_column_mapping(s, "nonexistent.csv", None, None, None)


# ── 8. Cancel event during solve ─────────────────────────────────────────────

def test_cancel_event_before_solve_raises():
    """If the cancel_event is already set before run_solve enters validation,
    the function raises SolveCancelledError."""
    from api.errors import SolveCancelledError

    s = make_session()
    run_parse(s, "Assign workers to tasks",
              DeterministicStub([{**ASSIGNMENT_SPEC, "ambiguities": []}]))
    # Force READY (the spec has no ambiguities so should already be READY)
    assert s.stage == SessionStage.READY

    cancel = threading.Event()
    cancel.set()

    with pytest.raises(SolveCancelledError):
        run_solve(s, cancel_event=cancel)


# ── 9. Parse failure stage ───────────────────────────────────────────────────

def test_parse_error_sets_failed_stage_and_code():
    s = make_session()
    run_parse(s, "anything", _AlwaysErrorAdapter())
    assert s.stage == SessionStage.FAILED
    assert s.error_code == "parse_error"
    # Session should still be usable for inspection
    assert s.spec is None


# ── 10. resolve_ambiguities without prior parse ──────────────────────────────

def test_resolve_ambiguities_without_spec_raises_gate_error():
    s = make_session()
    with pytest.raises(GateError, match="Parse first"):
        resolve_ambiguities(s, {"amb-1": "yes"})
