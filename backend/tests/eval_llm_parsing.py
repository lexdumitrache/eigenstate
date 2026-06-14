"""LLM parsing evaluation: measures how reliably the real LLM converts messy
natural language into correct OptimizationSpecs.

Complements the deterministic E2E suite (test_e2e.py), which only tests
given-a-good-spec → can-the-pipeline-solve-it. This suite tests
given-messy-language → does-the-LLM-produce-a-correct-spec.

Usage:
    cd backend
    ANTHROPIC_API_KEY=sk-... python -m tests.eval_llm_parsing          # Anthropic
    OPENAI_API_KEY=sk-...    python -m tests.eval_llm_parsing --openai  # OpenAI

Metrics reported per category and overall:
    - problem_type accuracy
    - constraint type recall  (expected types ⊆ extracted types)
    - constraint type precision (no spurious types beyond expected)
    - ambiguity detection rate (at least one ambiguity when expected)
    - routing flag detection rate (routing ambiguity injected when expected)
    - end-to-end solver success rate (spec valid + solver reaches optimal/feasible)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser.llm_adapter import AnthropicAdapter, OpenAIAdapter, LLMError
from parser.parser import parse_problem, ParseError
from api.pipeline import run_solve
from api.session_store import SessionStore
from spec.enums import ProblemType, ConstraintType, ROUTING_SIGNALS


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

@dataclass
class EvalCase:
    id: str
    category: str          # assignment | allocation | scheduling | ambiguous | routing
    prompt: str
    expected_problem_type: Optional[str]        # None = any acceptable
    expected_constraint_types: list[str] = field(default_factory=list)  # must be present
    expect_ambiguity: bool = False              # at least one ambiguity expected
    expect_routing_flag: bool = False           # routing scope-guard ambiguity expected
    solvable: bool = True                       # should reach optimal/feasible


DATASET: list[EvalCase] = [
    # ---------------------------------------------------------------- assignment (10)
    EvalCase(
        id="asgn-01",
        category="assignment",
        prompt="I have 3 employees — Alice, Bob, Carol — and 3 projects. Assign each person to exactly one project to minimize total hours.",
        expected_problem_type="assignment",
        expected_constraint_types=["one_per_entity", "demand_coverage"],
    ),
    EvalCase(
        id="asgn-02",
        category="assignment",
        prompt="Match nurses to hospital shifts. Each nurse works at most one shift; every shift must be covered.",
        expected_problem_type="assignment",
        expected_constraint_types=["one_per_entity", "demand_coverage"],
    ),
    EvalCase(
        id="asgn-03",
        category="assignment",
        prompt="Pair 4 junior engineers with 4 mentors. Minimize mismatch cost. Each mentor takes one engineer.",
        expected_problem_type="assignment",
        expected_constraint_types=["one_per_entity", "demand_coverage"],
    ),
    EvalCase(
        id="asgn-04",
        category="assignment",
        prompt="Teachers and classrooms: assign each teacher to a classroom. Teachers have preferences rated 1-5. Maximize total preference score.",
        expected_problem_type="assignment",
        expected_constraint_types=["one_per_entity", "demand_coverage"],
    ),
    EvalCase(
        id="asgn-05",
        category="assignment",
        prompt="We have drivers and delivery zones. Assign drivers to zones to minimize total drive time. Each zone must have exactly one driver.",
        expected_problem_type="assignment",
        expected_constraint_types=["demand_coverage"],
    ),
    EvalCase(
        id="asgn-06",
        category="assignment",
        prompt="Allocate 5 customer service reps to 5 call queues. Each queue needs someone and each rep handles one queue. Minimize average wait time.",
        expected_problem_type="assignment",
        expected_constraint_types=["one_per_entity", "demand_coverage"],
    ),
    EvalCase(
        id="asgn-07",
        category="assignment",
        prompt="We have 4 machines and 4 jobs. Each machine runs one job. Minimize makespan.",
        expected_problem_type="assignment",
        expected_constraint_types=["one_per_entity", "demand_coverage"],
    ),
    EvalCase(
        id="asgn-08",
        category="assignment",
        prompt="Assign 6 sales reps to 6 territories. Each rep covers one territory. Maximize expected revenue.",
        expected_problem_type="assignment",
        expected_constraint_types=["one_per_entity", "demand_coverage"],
    ),
    EvalCase(
        id="asgn-09",
        category="assignment",
        prompt="Match volunteers to community events. Each volunteer has time for at most 2 events. Cover every event.",
        expected_problem_type="assignment",
        expected_constraint_types=["demand_coverage"],
    ),
    EvalCase(
        id="asgn-10",
        category="assignment",
        prompt="Pilots and flight routes. Pair each pilot to a route. Pilots are rated per route — maximize total suitability.",
        expected_problem_type="assignment",
        expected_constraint_types=["one_per_entity", "demand_coverage"],
    ),

    # ---------------------------------------------------------------- allocation (10)
    EvalCase(
        id="alloc-01",
        category="allocation",
        prompt="Split $500k marketing budget across 4 channels: TV, digital, print, radio. Maximize expected ROI. Digital capped at $200k.",
        expected_problem_type="allocation",
        expected_constraint_types=["budget_limit", "max_allocation"],
    ),
    EvalCase(
        id="alloc-02",
        category="allocation",
        prompt="Distribute 1000 hours of engineering time among 3 products. Product A needs at least 200 hours; Product B at least 150. Maximize total value delivered.",
        expected_problem_type="allocation",
        expected_constraint_types=["budget_limit", "min_allocation"],
    ),
    EvalCase(
        id="alloc-03",
        category="allocation",
        prompt="Allocate 200 server CPUs across 5 microservices. Each service needs at least 10 CPUs. Maximize throughput.",
        expected_problem_type="allocation",
        expected_constraint_types=["budget_limit", "min_allocation"],
    ),
    EvalCase(
        id="alloc-04",
        category="allocation",
        prompt="We have 3 vans (capacity 800kg, 600kg, 500kg) and 5 packages (120kg, 300kg, 200kg, 180kg, 250kg). Assign packages to vans, respecting capacity. Minimize number of vans used.",
        expected_problem_type="allocation",
        expected_constraint_types=["capacity"],
    ),
    EvalCase(
        id="alloc-05",
        category="allocation",
        prompt="Divide $2M R&D budget between hardware and software teams. Hardware needs at least $800k. Software caps at $1.2M. Maximize innovation score.",
        expected_problem_type="allocation",
        expected_constraint_types=["budget_limit", "min_allocation", "max_allocation"],
    ),
    EvalCase(
        id="alloc-06",
        category="allocation",
        prompt="Assign 10 support staff across 3 shifts to cover expected call volume. Each shift needs at least 2 staff. Minimize overtime cost.",
        expected_problem_type="allocation",
        expected_constraint_types=["budget_limit", "min_allocation"],
    ),
    EvalCase(
        id="alloc-07",
        category="allocation",
        prompt="Spread 500 training slots across 6 departments based on headcount. No department gets more than 120 slots. Maximize coverage.",
        expected_problem_type="allocation",
        expected_constraint_types=["budget_limit", "max_allocation"],
    ),
    EvalCase(
        id="alloc-08",
        category="allocation",
        prompt="Allocate ad impressions across 4 campaigns. Total budget is 10M impressions. Each campaign must get at least 500k. Maximize total clicks.",
        expected_problem_type="allocation",
        expected_constraint_types=["budget_limit", "min_allocation"],
    ),
    EvalCase(
        id="alloc-09",
        category="allocation",
        prompt="We have 2 trucks (capacity 1 ton each) and 4 shipments (300kg, 400kg, 250kg, 350kg). Which truck takes which shipment?",
        expected_problem_type="allocation",
        expected_constraint_types=["capacity"],
    ),
    EvalCase(
        id="alloc-10",
        category="allocation",
        prompt="Distribute $300k across 5 departments. Each must receive something; Marketing can't exceed $80k. Maximize weighted impact.",
        expected_problem_type="allocation",
        expected_constraint_types=["budget_limit", "min_allocation", "max_allocation"],
    ),

    # ---------------------------------------------------------------- scheduling (10)
    EvalCase(
        id="sched-01",
        category="scheduling",
        prompt="Schedule 4 jobs on 2 machines. Cutting takes 3h on machine A. Welding takes 2h on machine A. Painting takes 4h on machine B. Cutting must finish before welding starts. Minimize makespan.",
        expected_problem_type="scheduling",
        expected_constraint_types=["no_overlap", "precedence"],
    ),
    EvalCase(
        id="sched-02",
        category="scheduling",
        prompt="Three tasks must run on one server. Task X takes 5 minutes, Task Y takes 3 minutes, Task Z takes 7 minutes. Y must follow X. Minimize total completion time.",
        expected_problem_type="scheduling",
        expected_constraint_types=["no_overlap", "precedence"],
    ),
    EvalCase(
        id="sched-03",
        category="scheduling",
        prompt="We have 5 construction phases. Foundation (10 days), Framing (8 days), Electrical (5 days), Plumbing (5 days), Finishing (6 days). Foundation before everything; Electrical and Plumbing before Finishing. Minimize project duration.",
        expected_problem_type="scheduling",
        expected_constraint_types=["no_overlap", "precedence"],
    ),
    EvalCase(
        id="sched-04",
        category="scheduling",
        prompt="Schedule 3 surgeries on 2 operating rooms. Surgery A: 2h; Surgery B: 3h; Surgery C: 1.5h. No room can have two surgeries at once. Minimize time until all surgeries done.",
        expected_problem_type="scheduling",
        expected_constraint_types=["no_overlap"],
    ),
    EvalCase(
        id="sched-05",
        category="scheduling",
        prompt="Four print jobs on one printer. Job 1: 10min, Job 2: 15min, Job 3: 8min, Job 4: 20min. Job 2 must come after Job 1. Minimize total time.",
        expected_problem_type="scheduling",
        expected_constraint_types=["no_overlap", "precedence"],
    ),
    EvalCase(
        id="sched-06",
        category="scheduling",
        prompt="Schedule 6 maintenance tasks on 3 machines. Each task has a fixed duration. Tasks on the same machine can't overlap. Minimize makespan.",
        expected_problem_type="scheduling",
        expected_constraint_types=["no_overlap"],
    ),
    EvalCase(
        id="sched-07",
        category="scheduling",
        prompt="Plan a film shoot: 5 scenes across 2 sets. Scene A (2h), Scene B (3h), Scene C (1h) on Set 1. Scene D (4h), Scene E (2h) on Set 2. Scene B must follow Scene A. Minimize total shoot time.",
        expected_problem_type="scheduling",
        expected_constraint_types=["no_overlap", "precedence"],
    ),
    EvalCase(
        id="sched-08",
        category="scheduling",
        prompt="Sequence 4 data processing jobs on one compute cluster. Job durations: 20min, 45min, 30min, 15min. Job 3 depends on Job 1. Minimize completion time.",
        expected_problem_type="scheduling",
        expected_constraint_types=["no_overlap", "precedence"],
    ),
    EvalCase(
        id="sched-09",
        category="scheduling",
        prompt="Three baking tasks in one oven: bread (45min), cake (60min), cookies (25min). Only one fits at a time. Minimize time to finish all.",
        expected_problem_type="scheduling",
        expected_constraint_types=["no_overlap"],
    ),
    EvalCase(
        id="sched-10",
        category="scheduling",
        prompt="Schedule 4 lab experiments on 2 benches. Each experiment has a known duration. Benches can't share experiments. Experiment B must run after A. Minimize total lab time.",
        expected_problem_type="scheduling",
        expected_constraint_types=["no_overlap", "precedence"],
    ),

    # ---------------------------------------------------------------- ambiguous (10)
    EvalCase(
        id="amb-01",
        category="ambiguous",
        prompt="Assign workers to tasks to be efficient.",
        expected_problem_type="assignment",
        expect_ambiguity=True,
        solvable=False,
    ),
    EvalCase(
        id="amb-02",
        category="ambiguous",
        prompt="Distribute budget. Maximize returns.",
        expected_problem_type="allocation",
        expect_ambiguity=True,
        solvable=False,
    ),
    EvalCase(
        id="amb-03",
        category="ambiguous",
        prompt="Schedule my team for next week.",
        expected_problem_type="scheduling",
        expect_ambiguity=True,
        solvable=False,
    ),
    EvalCase(
        id="amb-04",
        category="ambiguous",
        prompt="We need to assign employees to projects. Some projects need more than one person. Should each employee do at most one project or can they do more?",
        expected_problem_type="assignment",
        expect_ambiguity=True,
        solvable=False,
    ),
    EvalCase(
        id="amb-05",
        category="ambiguous",
        prompt="Allocate hours between teams. Some tasks are critical and should take priority. Not sure how to weight them.",
        expected_problem_type="allocation",
        expect_ambiguity=True,
        solvable=False,
    ),
    EvalCase(
        id="amb-06",
        category="ambiguous",
        prompt="Sort out which machine handles which job. We care about cost but also speed — not sure which matters more.",
        expected_problem_type="assignment",
        expect_ambiguity=True,
        solvable=False,
    ),
    EvalCase(
        id="amb-07",
        category="ambiguous",
        prompt="Split the marketing budget. We have three channels but I'm not sure what the ROI is for each yet.",
        expected_problem_type="allocation",
        expect_ambiguity=True,
        solvable=False,
    ),
    EvalCase(
        id="amb-08",
        category="ambiguous",
        prompt="Schedule construction tasks. Some tasks depend on others but I haven't mapped all dependencies.",
        expected_problem_type="scheduling",
        expect_ambiguity=True,
        solvable=False,
    ),
    EvalCase(
        id="amb-09",
        category="ambiguous",
        prompt="Assign drivers to jobs. Each driver can handle multiple jobs but I'm not sure how many.",
        expected_problem_type="assignment",
        expect_ambiguity=True,
        solvable=False,
    ),
    EvalCase(
        id="amb-10",
        category="ambiguous",
        prompt="Allocate server resources across services. Some services are more important — prioritize them somehow.",
        expected_problem_type="allocation",
        expect_ambiguity=True,
        solvable=False,
    ),

    # ---------------------------------------------------------------- routing / out-of-scope (10)
    EvalCase(
        id="route-01",
        category="routing",
        prompt="In what order should my delivery van visit 5 stops to minimize total travel time?",
        expected_problem_type=None,
        expect_routing_flag=True,
        solvable=False,
    ),
    EvalCase(
        id="route-02",
        category="routing",
        prompt="Find the shortest route for our truck to visit all 8 warehouses and return to the depot.",
        expected_problem_type=None,
        expect_routing_flag=True,
        solvable=False,
    ),
    EvalCase(
        id="route-03",
        category="routing",
        prompt="Optimize the visiting order of customer locations to minimize total travel distance.",
        expected_problem_type=None,
        expect_routing_flag=True,
        solvable=False,
    ),
    EvalCase(
        id="route-04",
        category="routing",
        prompt="What is the optimal sequence of stops for my field service technician?",
        expected_problem_type=None,
        expect_routing_flag=True,
        solvable=False,
    ),
    EvalCase(
        id="route-05",
        category="routing",
        prompt="Plan a multi-stop route for our sales rep visiting 10 clients. Minimize total driving.",
        expected_problem_type=None,
        expect_routing_flag=True,
        solvable=False,
    ),
    EvalCase(
        id="route-06",
        category="routing",
        prompt="Solve a TSP for our courier: 12 delivery points, return to start, minimize distance.",
        expected_problem_type=None,
        expect_routing_flag=True,
        solvable=False,
    ),
    EvalCase(
        id="route-07",
        category="routing",
        prompt="Vehicle routing: 3 trucks, 20 customer locations, each truck starts and ends at the depot. Minimize total distance.",
        expected_problem_type=None,
        expect_routing_flag=True,
        solvable=False,
    ),
    EvalCase(
        id="route-08",
        category="routing",
        prompt="Given travel times between 6 cities, find the route sequence that minimizes total travel time.",
        expected_problem_type=None,
        expect_routing_flag=True,
        solvable=False,
    ),
    EvalCase(
        id="route-09",
        category="routing",
        prompt="My driver needs to pick up packages from 4 locations and drop them at 4 destinations. What order minimizes driving?",
        expected_problem_type=None,
        expect_routing_flag=True,
        solvable=False,
    ),
    EvalCase(
        id="route-10",
        category="routing",
        prompt="Optimize the sequence of stops for a school bus picking up students at 8 locations.",
        expected_problem_type=None,
        expect_routing_flag=True,
        solvable=False,
    ),
]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    case_id: str
    category: str
    prompt_type_correct: bool
    constraint_recall: float      # fraction of expected constraints found
    constraint_precision: float   # fraction of found constraints that were expected
    ambiguity_detected: bool
    routing_detected: bool
    solver_success: bool
    error: Optional[str] = None


def score_case(case: EvalCase, adapter) -> CaseResult:
    store = SessionStore()
    session = store.create()

    try:
        from api.pipeline import run_parse
        run_parse(session, case.prompt, adapter)
    except (ParseError, Exception) as e:
        return CaseResult(
            case_id=case.id, category=case.category,
            prompt_type_correct=False, constraint_recall=0.0,
            constraint_precision=1.0, ambiguity_detected=False,
            routing_detected=False, solver_success=False,
            error=str(e),
        )

    spec = session.spec

    # problem_type accuracy
    if case.expected_problem_type is None:
        type_correct = True  # routing — any type is fine; flag matters
    else:
        type_correct = (spec.problem_type.value == case.expected_problem_type)

    # constraint recall / precision
    extracted = {c.constraint_type.value for c in spec.constraints}
    expected_set = set(case.expected_constraint_types)
    if expected_set:
        recall = len(extracted & expected_set) / len(expected_set)
        precision = len(extracted & expected_set) / len(extracted) if extracted else 1.0
    else:
        recall = 1.0
        precision = 1.0

    # ambiguity detection
    has_ambiguity = bool(spec.ambiguities)

    # routing flag: the routing scope-guard ambiguity mentions "v2"
    routing_flagged = any(
        "v2" in a.question or any(sig in a.question.lower() for sig in ROUTING_SIGNALS)
        for a in spec.ambiguities
    )

    # solver success
    solver_ok = False
    if case.solvable and session.stage.value in ("ready",):
        try:
            from api.pipeline import run_solve
            run_solve(session)
            solver_ok = session.result is not None and session.result.status in ("optimal", "feasible")
        except Exception:
            pass

    return CaseResult(
        case_id=case.id,
        category=case.category,
        prompt_type_correct=type_correct,
        constraint_recall=recall,
        constraint_precision=precision,
        ambiguity_detected=has_ambiguity,
        routing_detected=routing_flagged,
        solver_success=solver_ok,
        error=None,
    )


# ---------------------------------------------------------------------------
# Runner + report
# ---------------------------------------------------------------------------

def run_eval(adapter, cases: list[EvalCase] | None = None, delay: float = 1.0):
    if cases is None:
        cases = DATASET
    results: list[CaseResult] = []
    print(f"\nRunning {len(cases)} eval cases ...\n")
    for i, case in enumerate(cases, 1):
        print(f"  [{i:02d}/{len(cases)}] {case.id:12s} {case.category:12s} ", end="", flush=True)
        r = score_case(case, adapter)
        results.append(r)
        status = "OK" if not r.error else f"ERR: {r.error[:60]}"
        print(status)
        if delay:
            time.sleep(delay)
    return results


def _pct(num, den):
    return f"{100*num/den:.1f}%" if den else "n/a"


def print_report(results: list[CaseResult], cases: list[EvalCase] | None = None):
    if cases is None:
        cases = DATASET
    solvable_ids = {c.id for c in cases if c.solvable}
    categories = ["assignment", "allocation", "scheduling", "ambiguous", "routing"]
    print("\n" + "=" * 72)
    print(f"{'Category':<14} {'N':>3}  {'Type%':>6}  {'Recall':>7}  {'Precis':>7}  {'Ambig%':>7}  {'Route%':>7}  {'Solve%':>7}")
    print("-" * 72)

    totals = dict(n=0, type=0, recall=0.0, prec=0.0, ambig=0, route=0, solve=0)

    for cat in categories:
        rs = [r for r in results if r.category == cat]
        if not rs:
            continue
        n = len(rs)
        type_ok = sum(r.prompt_type_correct for r in rs)
        recall_sum = sum(r.constraint_recall for r in rs)
        prec_sum = sum(r.constraint_precision for r in rs)

        # ambiguity: relevant only for ambiguous category
        if cat == "ambiguous":
            ambig_ok = sum(r.ambiguity_detected for r in rs)
            ambig_str = _pct(ambig_ok, n)
        else:
            ambig_str = "—"

        # routing: relevant only for routing category
        if cat == "routing":
            route_ok = sum(r.routing_detected for r in rs)
            route_str = _pct(route_ok, n)
        else:
            route_str = "—"

        # solver: relevant only for solvable categories
        solvable_rs = [r for r in rs if r.case_id in solvable_ids]
        if solvable_rs:
            solve_ok = sum(r.solver_success for r in solvable_rs)
            solve_str = _pct(solve_ok, len(solvable_rs))
        else:
            solve_str = "—"

        print(
            f"{cat:<14} {n:>3}  "
            f"{_pct(type_ok, n):>6}  "
            f"{_pct(recall_sum, n):>7}  "
            f"{_pct(prec_sum, n):>7}  "
            f"{ambig_str:>7}  "
            f"{route_str:>7}  "
            f"{solve_str:>7}"
        )

        totals["n"] += n
        totals["type"] += type_ok
        totals["recall"] += recall_sum
        totals["prec"] += prec_sum

    print("-" * 72)
    n = totals["n"]
    print(
        f"{'TOTAL':<14} {n:>3}  "
        f"{_pct(totals['type'], n):>6}  "
        f"{_pct(totals['recall'], n):>7}  "
        f"{_pct(totals['prec'], n):>7}"
    )
    print("=" * 72)

    errors = [r for r in results if r.error]
    if errors:
        print(f"\n{len(errors)} case(s) errored:")
        for r in errors:
            print(f"  {r.case_id}: {r.error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Eigenstate LLM parsing eval")
    parser.add_argument("--openai", action="store_true", help="Use OpenAI instead of Anthropic")
    parser.add_argument("--model", default=None, help="Override model name")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between API calls (rate-limit buffer)")
    parser.add_argument("--subset", default=None, help="Comma-separated case IDs to run (e.g. asgn-01,alloc-02)")
    args = parser.parse_args()

    if args.openai:
        adapter = OpenAIAdapter(model=args.model or "gpt-4o")
        print(f"Provider: OpenAI  model: {adapter.model}")
    else:
        adapter = AnthropicAdapter(model=args.model or "claude-sonnet-4-6")
        print(f"Provider: Anthropic  model: {adapter.model}")

    active = DATASET
    if args.subset:
        ids = {s.strip() for s in args.subset.split(",")}
        active = [c for c in DATASET if c.id in ids]
        if not active:
            print(f"No cases matched subset filter: {args.subset}")
            sys.exit(1)

    results = run_eval(adapter, active, delay=args.delay)
    print_report(results, active)
