"""FastAPI app (spec §5).

POST /api/sessions                      → create session
POST /api/sessions/{id}/files           → upload CSV/Excel (before parse)
POST /api/sessions/{id}/parse           → run parser
POST /api/sessions/{id}/column-mapping  → confirm/edit column mappings
POST /api/sessions/{id}/clarify         → resolve ambiguities
POST /api/sessions/{id}/solve           → enqueue background solve job
GET  /api/jobs/{job_id}                 → poll job status / fetch result
POST /api/jobs/{job_id}/cancel          → cancel in-flight solve
POST /api/sessions/{id}/feedback        → record user's post-solve feedback
GET  /api/sessions/{id}                 → full state for UI rendering
GET  /api/preferences                   → aggregated learned preferences
"""
from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from parser.llm_adapter import get_adapter
from api import pipeline
from api.session_store import store
from api.job_store import job_store, JobStatus
from api.errors import EigenstateError, SolveCancelledError, eigenstate_error_handler
from feedback.store import save_feedback, preference_summary
from spec.schema import FeedbackEntry, FeedbackChange
from spec.enums import SessionStage

app = FastAPI(title="Eigenstate", version="1.0")

app.add_exception_handler(EigenstateError, eigenstate_error_handler)

_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:4173",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:4173",
    # nginx-served frontend (docker compose)
    "http://localhost",
    "http://localhost:80",
]
_extra = os.environ.get("CORS_ORIGINS", "")
if _extra:
    _ALLOWED_ORIGINS += [o.strip() for o in _extra.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Session-Token"],
)


def _adapter():
    return get_adapter(os.environ.get("EIGENSTATE_LLM"))


# ── Request bodies ─────────────────────────────────────────────────────────────

class ParseRequest(BaseModel):
    text: str


class MappingRequest(BaseModel):
    file_name: str
    column_to_field: dict[str, str] | None = None
    entity_category: str | None = None
    id_column: str | None = None


class ClarifyRequest(BaseModel):
    answers: dict[str, str]


class ConstraintPatchRequest(BaseModel):
    name: str
    parameters: dict[str, str | float | int] = {}


class SpecEditRequest(BaseModel):
    problem_type: str | None = None
    objective_sense: str | None = None
    objective_coefficient_field: str | None = None
    constraint_patches: list[ConstraintPatchRequest] = []


class FeedbackChangeRequest(BaseModel):
    original_decision: str
    user_change: str
    reason: str = ""


class FeedbackRequest(BaseModel):
    accepted: bool
    changes: list[FeedbackChangeRequest] = []


# ── Helpers ────────────────────────────────────────────────────────────────────

def _session_or_404(session_id: str):
    try:
        return store.get(session_id)
    except KeyError:
        raise HTTPException(404, f"Session {session_id} not found")


def _verify_token(session, token: str | None) -> None:
    if token != session.token:
        raise HTTPException(403, "Invalid or missing X-Session-Token header.")


def _state(session) -> dict:
    return {
        "session_id": session.id,
        "stage": session.stage.value,
        "next_action": session.next_action(),
        "spec": session.spec.model_dump() if session.spec else None,
        "validation": session.validation,
        "result": session.result.model_dump() if session.result else None,
        "explanation": session.explanation.model_dump() if session.explanation else None,
        "error": session.error,
        "error_code": getattr(session, "error_code", None),
        "files": list(session.files),
    }


# ── Session lifecycle ──────────────────────────────────────────────────────────

@app.post("/api/sessions")
def create_session():
    s = store.create()
    return {"session_id": s.id, "session_token": s.token, "stage": s.stage.value}


_ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
_MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB


@app.post("/api/sessions/{session_id}/files")
async def upload_file(
    session_id: str,
    file: UploadFile,
    x_session_token: str | None = Header(None),
):
    s = _session_or_404(session_id)
    _verify_token(s, x_session_token)
    filename = file.filename or "upload.csv"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            415,
            f"Unsupported file type '{ext}'. Allowed: {sorted(_ALLOWED_EXTENSIONS)}",
        )
    content = await file.read()
    if len(content) > _MAX_FILE_BYTES:
        raise HTTPException(
            413,
            f"File '{filename}' exceeds the {_MAX_FILE_BYTES // (1024 * 1024)} MB limit.",
        )
    s.files[filename] = content
    store.save(s)
    return {"ok": True, "files": list(s.files)}


@app.post("/api/sessions/{session_id}/parse")
def parse(session_id: str, body: ParseRequest, x_session_token: str | None = Header(None)):
    s = _session_or_404(session_id)
    _verify_token(s, x_session_token)
    pipeline.run_parse(s, body.text, _adapter())
    store.save(s)
    return _state(s)


@app.post("/api/sessions/{session_id}/column-mapping")
def column_mapping(
    session_id: str,
    body: MappingRequest,
    x_session_token: str | None = Header(None),
):
    s = _session_or_404(session_id)
    _verify_token(s, x_session_token)
    try:
        pipeline.confirm_column_mapping(s, body.file_name, body.column_to_field,
                                        body.entity_category, body.id_column)
    except pipeline.GateError as e:
        raise HTTPException(409, str(e))
    store.save(s)
    return _state(s)


@app.post("/api/sessions/{session_id}/spec")
def edit_spec(
    session_id: str,
    body: SpecEditRequest,
    x_session_token: str | None = Header(None),
):
    s = _session_or_404(session_id)
    _verify_token(s, x_session_token)
    if s.spec is None:
        raise HTTPException(409, "No spec to edit yet.")
    try:
        pipeline.edit_spec(s, body)
    except pipeline.GateError as e:
        raise HTTPException(409, str(e))
    store.save(s)
    return _state(s)


@app.post("/api/sessions/{session_id}/clarify")
def clarify(
    session_id: str,
    body: ClarifyRequest,
    x_session_token: str | None = Header(None),
):
    s = _session_or_404(session_id)
    _verify_token(s, x_session_token)
    try:
        pipeline.resolve_ambiguities(s, body.answers)
    except pipeline.GateError as e:
        raise HTTPException(409, str(e))
    store.save(s)
    return _state(s)


# ── Async solve job ────────────────────────────────────────────────────────────

@app.post("/api/sessions/{session_id}/solve")
def solve(session_id: str, x_session_token: str | None = Header(None)):
    s = _session_or_404(session_id)
    _verify_token(s, x_session_token)
    if s.stage == SessionStage.CANCELLED:
        raise HTTPException(409, "Session was cancelled by the user.")
    if s.spec is None or s.stage != SessionStage.READY:
        raise HTTPException(
            409,
            "Session is not READY — the clarification gate must be passed before solving.",
        )

    job = job_store.create(session_id)

    def _run():
        job.status = JobStatus.RUNNING
        try:
            pipeline.run_solve(s, _adapter(), cancel_event=job.cancel_event)
            if job.cancel_event.is_set():
                job.status = JobStatus.CANCELLED
            else:
                job.status = JobStatus.DONE
        except SolveCancelledError:
            s.stage = SessionStage.CANCELLED
            job.status = JobStatus.CANCELLED
        except Exception as e:
            code = getattr(e, "error_code", "solver_error")
            job.status = JobStatus.FAILED
            job.error = getattr(e, "message", str(e))
            job.error_code = code
        finally:
            store.save(s)

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job.id, "status": job.status}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    try:
        job = job_store.get(job_id)
    except KeyError:
        raise HTTPException(404, f"Job {job_id} not found")
    s = store.get(job.session_id)
    response: dict = {
        "job_id": job.id,
        "status": job.status,
        "error": job.error,
        "error_code": job.error_code,
    }
    if job.status == JobStatus.DONE:
        response["session"] = _state(s)
    return response


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    try:
        job = job_store.get(job_id)
    except KeyError:
        raise HTTPException(404, f"Job {job_id} not found")
    job.cancel()
    return {"ok": True, "job_id": job_id}


# ── Read-only session view ─────────────────────────────────────────────────────

@app.get("/api/sessions/{session_id}")
def get_session(session_id: str, x_session_token: str | None = Header(None)):
    s = _session_or_404(session_id)
    _verify_token(s, x_session_token)
    return _state(s)


# ── Feedback ───────────────────────────────────────────────────────────────────

@app.post("/api/sessions/{session_id}/feedback")
def submit_feedback(
    session_id: str,
    body: FeedbackRequest,
    x_session_token: str | None = Header(None),
):
    s = _session_or_404(session_id)
    _verify_token(s, x_session_token)
    if s.spec is None:
        raise HTTPException(409, "Session has no solved spec to give feedback on.")
    from datetime import datetime, timezone
    entry = FeedbackEntry(
        session_id=session_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        problem_type=s.spec.problem_type.value,
        accepted=body.accepted,
        changes=[
            FeedbackChange(
                original_decision=c.original_decision,
                user_change=c.user_change,
                reason=c.reason,
            )
            for c in body.changes
        ],
    )
    saved = save_feedback(entry)
    return {"ok": True, "inferred_preferences": saved.inferred_preferences}


@app.get("/api/preferences")
def get_preferences():
    return preference_summary()
