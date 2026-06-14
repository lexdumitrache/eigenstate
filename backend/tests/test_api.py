"""API endpoint tests (spec §27: API endpoint tests).

Uses FastAPI's TestClient (synchronous HTTPX wrapper) so no real server
is needed.  LLM calls are patched via monkeypatch so no API keys are required.
"""
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from api.main import app
from parser.llm_adapter import DeterministicStub


# ── Shared spec fixtures ──────────────────────────────────────────────────────

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

AMBIGUOUS_SPEC = {
    **ASSIGNMENT_SPEC,
    "ambiguities": [
        {"id": "amb-1", "question": "At most or exactly one?", "context": "",
         "options": ["at most", "exactly"], "blocking": True,
         "target_constraint": None, "resolution_map": None}
    ],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_session(client: TestClient) -> tuple[str, str]:
    resp = client.post("/api/sessions")
    assert resp.status_code == 200
    data = resp.json()
    return data["session_id"], data["session_token"]


def _headers(token: str) -> dict[str, str]:
    return {"X-Session-Token": token}


def _patch_adapter(monkeypatch, responses: list[dict]):
    stub = DeterministicStub(responses)
    monkeypatch.setattr("api.main._adapter", lambda: stub)


# ── Session lifecycle ─────────────────────────────────────────────────────────

def test_create_session_returns_id_and_token():
    with TestClient(app) as client:
        resp = client.post("/api/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert "session_id" in body
    assert "session_token" in body
    assert body["stage"] == "created"


def test_get_session_requires_valid_token():
    with TestClient(app) as client:
        sid, tok = _create_session(client)
        # no token → 403
        resp = client.get(f"/api/sessions/{sid}")
        assert resp.status_code == 403
        # wrong token → 403
        resp = client.get(f"/api/sessions/{sid}",
                          headers={"X-Session-Token": "badtoken"})
        assert resp.status_code == 403
        # correct token → 200
        resp = client.get(f"/api/sessions/{sid}", headers=_headers(tok))
        assert resp.status_code == 200


def test_get_nonexistent_session_returns_404():
    with TestClient(app) as client:
        resp = client.get("/api/sessions/doesnotexist000",
                          headers={"X-Session-Token": "any"})
    assert resp.status_code == 404


# ── File upload ───────────────────────────────────────────────────────────────

def test_upload_file_csv_accepted():
    with TestClient(app) as client:
        sid, tok = _create_session(client)
        resp = client.post(
            f"/api/sessions/{sid}/files",
            files={"file": ("workers.csv", b"id,name\nw1,Ana\n", "text/csv")},
            headers=_headers(tok),
        )
    assert resp.status_code == 200
    assert "workers.csv" in resp.json()["files"]


def test_upload_file_rejects_bad_extension():
    with TestClient(app) as client:
        sid, tok = _create_session(client)
        resp = client.post(
            f"/api/sessions/{sid}/files",
            files={"file": ("data.txt", b"hello", "text/plain")},
            headers=_headers(tok),
        )
    assert resp.status_code == 415


def test_upload_file_rejects_oversized():
    big = b"x" * (21 * 1024 * 1024)  # 21 MB — over the 20 MB limit
    with TestClient(app) as client:
        sid, tok = _create_session(client)
        resp = client.post(
            f"/api/sessions/{sid}/files",
            files={"file": ("data.csv", big, "text/csv")},
            headers=_headers(tok),
        )
    assert resp.status_code == 413


# ── Parse ─────────────────────────────────────────────────────────────────────

def test_parse_advances_to_ready(monkeypatch):
    _patch_adapter(monkeypatch, [ASSIGNMENT_SPEC])
    with TestClient(app) as client:
        sid, tok = _create_session(client)
        resp = client.post(
            f"/api/sessions/{sid}/parse",
            json={"text": "Assign 2 workers to 2 tasks"},
            headers=_headers(tok),
        )
    assert resp.status_code == 200
    assert resp.json()["stage"] == "ready"


def test_parse_with_ambiguity_advances_to_awaiting_clarification(monkeypatch):
    _patch_adapter(monkeypatch, [AMBIGUOUS_SPEC])
    with TestClient(app) as client:
        sid, tok = _create_session(client)
        resp = client.post(
            f"/api/sessions/{sid}/parse",
            json={"text": "Assign workers to tasks"},
            headers=_headers(tok),
        )
    assert resp.status_code == 200
    assert resp.json()["stage"] == "awaiting_clarification"


# ── Solve gate ────────────────────────────────────────────────────────────────

def test_solve_before_ready_returns_409(monkeypatch):
    _patch_adapter(monkeypatch, [AMBIGUOUS_SPEC])
    with TestClient(app) as client:
        sid, tok = _create_session(client)
        client.post(f"/api/sessions/{sid}/parse",
                    json={"text": "Assign workers to tasks"},
                    headers=_headers(tok))
        # Stage is AWAITING_CLARIFICATION — solve must fail
        resp = client.post(f"/api/sessions/{sid}/solve", headers=_headers(tok))
    assert resp.status_code == 409


def test_solve_on_nonexistent_session_returns_404():
    with TestClient(app) as client:
        resp = client.post("/api/sessions/nosession/solve",
                           headers={"X-Session-Token": "tok"})
    assert resp.status_code == 404


# ── Clarify then solve flow ───────────────────────────────────────────────────

def test_clarify_unblocks_solve_and_job_completes(monkeypatch):
    _patch_adapter(monkeypatch, [AMBIGUOUS_SPEC])
    with TestClient(app) as client:
        sid, tok = _create_session(client)
        client.post(f"/api/sessions/{sid}/parse",
                    json={"text": "Assign workers to tasks"},
                    headers=_headers(tok))
        # Resolve the ambiguity
        resp = client.post(f"/api/sessions/{sid}/clarify",
                           json={"answers": {"amb-1": "at most one — proceed"}},
                           headers=_headers(tok))
        assert resp.status_code == 200
        assert resp.json()["stage"] == "ready"

        # Now solve should succeed (enqueue job)
        resp = client.post(f"/api/sessions/{sid}/solve", headers=_headers(tok))
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        # Poll until done (max 10s)
        for _ in range(40):
            r = client.get(f"/api/jobs/{job_id}")
            if r.json()["status"] in ("done", "failed", "cancelled"):
                break
            time.sleep(0.25)

        assert r.json()["status"] == "done"
        assert r.json()["session"]["stage"] == "explained"


# ── Job cancel ────────────────────────────────────────────────────────────────

def test_cancel_nonexistent_job_returns_404():
    with TestClient(app) as client:
        resp = client.post("/api/jobs/fakejobid/cancel")
    assert resp.status_code == 404


def test_get_nonexistent_job_returns_404():
    with TestClient(app) as client:
        resp = client.get("/api/jobs/fakejobid")
    assert resp.status_code == 404


def test_cancel_job_sets_flag(monkeypatch):
    """Cancelling a job returns ok=True; we can't easily assert thread state
    in unit tests so we just verify the endpoint responds correctly."""
    _patch_adapter(monkeypatch, [ASSIGNMENT_SPEC])
    with TestClient(app) as client:
        sid, tok = _create_session(client)
        client.post(f"/api/sessions/{sid}/parse",
                    json={"text": "Assign workers"},
                    headers=_headers(tok))
        resp = client.post(f"/api/sessions/{sid}/solve", headers=_headers(tok))
        job_id = resp.json()["job_id"]

        cancel_resp = client.post(f"/api/jobs/{job_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["ok"] is True


# ── Column mapping 409 on bad file ───────────────────────────────────────────

def test_column_mapping_unknown_file_returns_409(monkeypatch):
    _patch_adapter(monkeypatch, [ASSIGNMENT_SPEC])
    with TestClient(app) as client:
        sid, tok = _create_session(client)
        client.post(f"/api/sessions/{sid}/parse",
                    json={"text": "Assign workers"},
                    headers=_headers(tok))
        resp = client.post(f"/api/sessions/{sid}/column-mapping",
                           json={"file_name": "ghost.csv"},
                           headers=_headers(tok))
    assert resp.status_code == 409


# ── Preferences endpoint ──────────────────────────────────────────────────────

def test_get_preferences_returns_200():
    with TestClient(app) as client:
        resp = client.get("/api/preferences")
    assert resp.status_code == 200
