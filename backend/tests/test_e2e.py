"""E2E tests (spec §7, phase 11): one per problem family, one with file
upload + column mapping, one exercising the clarification gate, plus the
routing-scope guard. All offline via DeterministicStub."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser.llm_adapter import DeterministicStub
from api.pipeline import (GateError, confirm_column_mapping, resolve_ambiguities,
                          run_parse, run_solve)
from api.session_store import SessionStore
from spec.enums import SessionStage
import pytest


def make_session():
    return SessionStore().create()


# ---------------------------------------------------------------- assignment

ASSIGNMENT_SPEC = {
    "problem_type": "assignment",
    "entities": [
        {"id": "w1", "category": "worker", "attributes": {"name": "Ana"}},
        {"id": "w2", "category": "worker", "attributes": {"name": "Bo"}},
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


def test_assignment_e2e():
    s = make_session()
    run_parse(s, "Assign 2 workers to 2 tasks at minimum cost",
              DeterministicStub([ASSIGNMENT_SPEC]))
    assert s.stage == SessionStage.READY
    run_solve(s)  # no LLM → deterministic explanation
    assert s.stage == SessionStage.EXPLAINED
    assert s.result.status == "optimal"
    assert s.result.objective_value == 7  # w1→t1 (4) + w2→t2 (3)
    assigned = [k for k, v in s.result.variable_values.items() if v > 0.5]
    assert len(assigned) == 2
    assert len(s.explanation.decisions) == 2


# ---------------------------------------------------------------- allocation

ALLOCATION_SPEC = {
    "problem_type": "allocation",
    "entities": [
        {"id": "mkt", "category": "department", "attributes": {"roi": 3.0, "max_budget": 50}},
        {"id": "rnd", "category": "department", "attributes": {"roi": 5.0, "max_budget": 40}},
        {"id": "ops", "category": "department", "attributes": {"roi": 1.5, "max_budget": 80}},
    ],
    "decision_variables": [{"name": "x", "description": "budget given",
                            "var_type": "continuous", "indexed_by": ["department"]}],
    "objective": {"sense": "maximize", "description": "total ROI",
                  "coefficient_field": "roi"},
    "constraints": [
        {"name": "total_budget", "constraint_type": "budget_limit",
         "description": "spend at most 100", "parameters": {"limit": 100}},
        {"name": "dept_caps", "constraint_type": "max_allocation",
         "description": "department maximums",
         "parameters": {"resource_field": "max_budget"}},
    ],
    "ambiguities": [],
    "confidence": 0.9,
}


def test_allocation_e2e():
    s = make_session()
    run_parse(s, "Split 100k across departments to maximize ROI",
              DeterministicStub([ALLOCATION_SPEC]))
    run_solve(s)
    assert s.result.status == "optimal"
    # rnd gets 40 (roi 5), mkt gets 50 (roi 3), ops gets 10 (roi 1.5) = 365
    assert abs(s.result.objective_value - 365.0) < 1e-6


# ---------------------------------------------------------------- scheduling

SCHEDULING_SPEC = {
    "problem_type": "scheduling",
    "entities": [
        {"id": "cut", "category": "job", "attributes": {"duration": 3, "machine": "m1"}},
        {"id": "weld", "category": "job", "attributes": {"duration": 2, "machine": "m1"}},
        {"id": "paint", "category": "job", "attributes": {"duration": 4, "machine": "m2"}},
    ],
    "decision_variables": [],
    "objective": {"sense": "minimize", "description": "makespan",
                  "coefficient_field": None},
    "constraints": [
        {"name": "machine_no_overlap", "constraint_type": "no_overlap",
         "description": "one job per machine at a time",
         "parameters": {"resource_field": "machine"}},
        {"name": "cut_before_weld", "constraint_type": "precedence",
         "description": "cut before weld",
         "parameters": {"before": "cut", "after": "weld"}},
    ],
    "ambiguities": [],
    "confidence": 0.9,
}


def test_scheduling_e2e():
    s = make_session()
    run_parse(s, "Schedule 3 jobs on 2 machines minimizing makespan",
              DeterministicStub([SCHEDULING_SPEC]))
    run_solve(s)
    assert s.result.status == "optimal"
    assert s.result.objective_value == 5  # m1: cut(3)+weld(2); m2: paint(4)
    assert s.result.variable_values["start[weld]"] >= s.result.variable_values["end[cut]"]


# ------------------------------------------------- file upload + column map

DISPATCH_SPEC = {
    "problem_type": "allocation",
    "entities": [],  # comes from the file + inline vans
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

MAPPING_SUGGESTION = {"entity_category": "package", "id_column": "pkg",
                      "column_to_field": {"pkg": "pkg", "weight": "weight_kg"}}

VAN_MAPPING_SUGGESTION = {"entity_category": "van", "id_column": "van",
                          "column_to_field": {"van": "van", "cap": "capacity_kg"}}

PACKAGES_CSV = b"pkg,weight\np1,300\np2,500\np3,200\n"
VANS_CSV = b"van,cap\nv1,600\nv2,500\n"


def test_file_upload_and_column_mapping_e2e():
    s = make_session()
    s.files["packages.csv"] = PACKAGES_CSV
    s.files["vans.csv"] = VANS_CSV
    stub = DeterministicStub([DISPATCH_SPEC, MAPPING_SUGGESTION,
                              VAN_MAPPING_SUGGESTION])
    run_parse(s, "Assign packages to vans, respect capacity", stub)
    assert s.stage == SessionStage.AWAITING_COLUMN_MAPPING

    # solving now must be refused (gate enforcement)
    with pytest.raises(GateError):
        run_solve(s)

    confirm_column_mapping(s, "packages.csv", None, None, None)
    confirm_column_mapping(s, "vans.csv", None, None, None)
    assert s.stage == SessionStage.READY
    assert len(s.spec.entities) == 5

    run_solve(s)
    assert s.result.status == "optimal"
    # check capacity respected in the digest
    assert len([k for k, v in s.result.variable_values.items() if v > 0.5]) == 3


# ------------------------------------------------- clarification gate

AMBIGUOUS_SPEC = {**ASSIGNMENT_SPEC,
                  "ambiguities": [{"id": "amb-1",
                                   "question": "At most one task per worker, or exactly one?",
                                   "context": "", "options": ["at most", "exactly"],
                                   "blocking": True}]}


def test_clarification_gate_blocks_solving():
    s = make_session()
    run_parse(s, "Assign workers to tasks", DeterministicStub([AMBIGUOUS_SPEC]))
    assert s.stage == SessionStage.AWAITING_CLARIFICATION
    with pytest.raises(GateError):
        run_solve(s)
    resolve_ambiguities(s, {"amb-1": "at most one — proceed with my assumptions"})
    assert s.stage == SessionStage.READY
    run_solve(s)
    assert s.stage == SessionStage.EXPLAINED
    # dismissed ambiguity shows up as a caveat
    assert s.explanation.caveats


# ------------------------------------------------- routing scope guard

def test_routing_detection_flags_v2_scope():
    spec = {**DISPATCH_SPEC, "entities": [
        {"id": "v1", "category": "van", "attributes": {"capacity_kg": 600}},
        {"id": "p1", "category": "package", "attributes": {"weight_kg": 100}},
    ]}
    s = make_session()
    run_parse(s, "In what order should the van visit stops to deliver packages?",
              DeterministicStub([spec]))
    assert s.stage == SessionStage.AWAITING_CLARIFICATION
    assert any("v2" in a.question for a in s.spec.ambiguities)


# ------------------------------------------------- infeasibility pre-check

def test_feasibility_precheck_catches_overload():
    spec = {**DISPATCH_SPEC, "entities": [
        {"id": "v1", "category": "van", "attributes": {"capacity_kg": 100}},
        {"id": "p1", "category": "package", "attributes": {"weight_kg": 300}},
        {"id": "p2", "category": "package", "attributes": {"weight_kg": 300}},
    ]}
    s = make_session()
    run_parse(s, "Assign packages to vans", DeterministicStub([spec]))
    run_solve(s)
    assert s.stage == SessionStage.FAILED
    assert "exceeds total" in s.error
