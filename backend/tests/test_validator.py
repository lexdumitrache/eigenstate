"""Unit tests for the validation layer (spec §1, phase 11 §27).

Tests the ValidationReport returned by validate_spec directly, without
invoking the full pipeline or any LLM adapter.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from spec.enums import ConstraintType, ObjectiveSense, ProblemType
from spec.schema import (
    Ambiguity, Constraint, DecisionVariable, Entity, Objective, OptimizationSpec,
)
from validation.validator import validate_spec


# ── Helpers ───────────────────────────────────────────────────────────────────

def _worker(id_, **attrs):
    return Entity(id=id_, category="worker", attributes=attrs)


def _task(id_, **attrs):
    return Entity(id=id_, category="task", attributes=attrs)


def _van(id_, **attrs):
    return Entity(id=id_, category="van", attributes=attrs)


def _pkg(id_, **attrs):
    return Entity(id=id_, category="package", attributes=attrs)


def _base_spec(**kwargs) -> OptimizationSpec:
    defaults = dict(
        problem_type=ProblemType.ASSIGNMENT,
        raw_text="test",
        entities=[_worker("w1"), _task("t1")],
        decision_variables=[
            DecisionVariable(name="x", description="assign", var_type="binary",
                             indexed_by=["worker", "task"])
        ],
        objective=Objective(sense=ObjectiveSense.MINIMIZE, description="cost",
                            coefficient_field=None),
        constraints=[],
        ambiguities=[],
    )
    defaults.update(kwargs)
    return OptimizationSpec(**defaults)


# ── 1. Duplicate IDs ──────────────────────────────────────────────────────────

def test_duplicate_ids_detected():
    spec = _base_spec(entities=[_worker("w1"), _worker("w1"), _task("t1")])
    rep = validate_spec(spec)
    assert not rep.ok
    assert any("Duplicate" in e and "worker" in e for e in rep.errors)


def test_duplicate_ids_across_categories_are_allowed():
    # same ID string in different categories is fine
    spec = _base_spec(entities=[_worker("id1"), _task("id1")])
    rep = validate_spec(spec)
    assert rep.ok, rep.errors


# ── 2. Missing objective coefficient field ────────────────────────────────────

def test_missing_objective_field_error():
    spec = _base_spec(
        entities=[_worker("w1", cost=5), _task("t1")],  # task has no "roi" field
        objective=Objective(sense=ObjectiveSense.MAXIMIZE, description="ROI",
                            coefficient_field="roi"),
    )
    rep = validate_spec(spec)
    assert not rep.ok
    assert any("roi" in e for e in rep.errors)


def test_non_numeric_objective_field_error():
    # At least one numeric so validator reaches the non-numeric branch
    spec = _base_spec(
        entities=[_worker("w1", cost=4), _worker("w2", cost="high"), _task("t1")],
        objective=Objective(sense=ObjectiveSense.MINIMIZE, description="cost",
                            coefficient_field="cost"),
    )
    rep = validate_spec(spec)
    assert not rep.ok
    assert any("non-numeric" in e for e in rep.errors)


def test_objective_field_absent_on_some_entities_is_warning():
    # Some entities have the field, others don't → warning, not error
    spec = _base_spec(
        entities=[_worker("w1", cost=4), _task("t1"), _task("t2", cost=7)],
        objective=Objective(sense=ObjectiveSense.MINIMIZE, description="cost",
                            coefficient_field="cost"),
    )
    rep = validate_spec(spec)
    assert rep.ok
    assert any("absent" in w for w in rep.warnings)


def test_no_objective_field_required_when_none():
    spec = _base_spec(
        objective=Objective(sense=ObjectiveSense.MINIMIZE, description="count",
                            coefficient_field=None),
    )
    rep = validate_spec(spec)
    assert rep.ok, rep.errors


# ── 3. Unresolved ambiguities block validation ────────────────────────────────

def test_unresolved_ambiguity_blocks_validation():
    amb = Ambiguity(id="amb-1", question="Which objective?", blocking=True)
    spec = _base_spec(ambiguities=[amb])
    rep = validate_spec(spec)
    assert not rep.ok
    assert any("ambiguit" in e.lower() for e in rep.errors)


def test_resolved_ambiguity_does_not_block():
    amb = Ambiguity(id="amb-1", question="Which objective?", blocking=True,
                    resolution="minimize cost")
    spec = _base_spec(ambiguities=[amb])
    rep = validate_spec(spec)
    assert rep.ok, rep.errors


def test_non_blocking_unresolved_ambiguity_is_ok():
    amb = Ambiguity(id="amb-1", question="Preference?", blocking=False)
    spec = _base_spec(ambiguities=[amb])
    rep = validate_spec(spec)
    assert rep.ok, rep.errors


# ── 4. No entities fails fast ─────────────────────────────────────────────────

def test_no_entities_fails():
    spec = _base_spec(entities=[])
    rep = validate_spec(spec)
    assert not rep.ok
    assert any("No entities" in e for e in rep.errors)


# ── 5. Individual demand exceeds capacity ─────────────────────────────────────

def test_individual_demand_exceeds_max_capacity():
    spec = OptimizationSpec(
        problem_type=ProblemType.ASSIGNMENT,
        raw_text="test",
        entities=[
            _van("v1", capacity_kg=100),
            _pkg("p1", weight_kg=300),  # bigger than the only van
        ],
        decision_variables=[],
        objective=Objective(sense=ObjectiveSense.MINIMIZE, description="count",
                            coefficient_field=None),
        constraints=[Constraint(
            name="cap", constraint_type=ConstraintType.CAPACITY,
            description="van capacity",
            parameters={"entity_category": "van", "resource_field": "capacity_kg",
                        "demand_category": "package", "demand_field": "weight_kg"},
        )],
        ambiguities=[],
    )
    rep = validate_spec(spec)
    assert not rep.ok
    assert any("cannot fit" in e for e in rep.errors)


def test_total_demand_exceeds_total_capacity():
    spec = OptimizationSpec(
        problem_type=ProblemType.ASSIGNMENT,
        raw_text="test",
        entities=[
            _van("v1", capacity_kg=200),
            _pkg("p1", weight_kg=150),
            _pkg("p2", weight_kg=150),  # total 300 > 200
        ],
        decision_variables=[],
        objective=Objective(sense=ObjectiveSense.MINIMIZE, description="count",
                            coefficient_field=None),
        constraints=[Constraint(
            name="cap", constraint_type=ConstraintType.CAPACITY,
            description="van capacity",
            parameters={"entity_category": "van", "resource_field": "capacity_kg",
                        "demand_category": "package", "demand_field": "weight_kg"},
        )],
        ambiguities=[],
    )
    rep = validate_spec(spec)
    assert not rep.ok
    assert any("exceeds total" in e for e in rep.errors)


# ── 6. Too many tasks for workers ─────────────────────────────────────────────

def test_too_many_tasks_for_workers():
    spec = OptimizationSpec(
        problem_type=ProblemType.ASSIGNMENT,
        raw_text="test",
        entities=[_worker("w1"), _task("t1"), _task("t2"), _task("t3")],
        decision_variables=[],
        objective=Objective(sense=ObjectiveSense.MINIMIZE, description="cost",
                            coefficient_field=None),
        constraints=[Constraint(
            name="one_each", constraint_type=ConstraintType.ONE_PER_ENTITY,
            description="each worker one task",
            parameters={"entity_category": "worker", "count": 1, "sense": "<="},
        )],
        ambiguities=[],
    )
    rep = validate_spec(spec)
    assert not rep.ok
    assert any("Not enough" in e or "capacity" in e.lower() for e in rep.errors)


def test_exact_task_worker_match_is_ok():
    spec = OptimizationSpec(
        problem_type=ProblemType.ASSIGNMENT,
        raw_text="test",
        entities=[_worker("w1"), _worker("w2"), _task("t1"), _task("t2")],
        decision_variables=[],
        objective=Objective(sense=ObjectiveSense.MINIMIZE, description="cost",
                            coefficient_field=None),
        constraints=[Constraint(
            name="one_each", constraint_type=ConstraintType.ONE_PER_ENTITY,
            description="each worker one task",
            parameters={"entity_category": "worker", "count": 1, "sense": "<="},
        )],
        ambiguities=[],
    )
    rep = validate_spec(spec)
    assert rep.ok, rep.errors


# ── 7. Decision variable category reference ───────────────────────────────────

def test_unknown_indexed_by_category_is_error():
    spec = _base_spec(
        decision_variables=[
            DecisionVariable(name="x", description="assign", var_type="binary",
                             indexed_by=["worker", "nonexistent_category"])
        ]
    )
    rep = validate_spec(spec)
    assert not rep.ok
    assert any("nonexistent_category" in e for e in rep.errors)


# ── 8. Negative capacity warning ─────────────────────────────────────────────

def test_negative_capacity_triggers_warning():
    spec = OptimizationSpec(
        problem_type=ProblemType.ASSIGNMENT,
        raw_text="test",
        entities=[
            _van("v1", capacity_kg=-10),
            _van("v2", capacity_kg=500),
            _pkg("p1", weight_kg=50),
        ],
        decision_variables=[],
        objective=Objective(sense=ObjectiveSense.MINIMIZE, description="count",
                            coefficient_field=None),
        constraints=[Constraint(
            name="cap", constraint_type=ConstraintType.CAPACITY,
            description="van capacity",
            parameters={"entity_category": "van", "resource_field": "capacity_kg",
                        "demand_category": "package", "demand_field": "weight_kg"},
        )],
        ambiguities=[],
    )
    rep = validate_spec(spec)
    assert any("negative" in w.lower() for w in rep.warnings)
