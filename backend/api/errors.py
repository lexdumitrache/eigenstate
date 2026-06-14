"""Structured error hierarchy for Eigenstate.

All pipeline errors inherit from EigenstateError and carry a machine-readable
error_code.  The FastAPI exception handler in main.py serialises them into a
consistent JSON envelope:

    {"error_code": "solver_error", "detail": "human-readable message"}
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


# ── Error codes ──────────────────────────────────────────────────────────────

class EigenstateError(Exception):
    error_code = "eigenstate_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ParseError(EigenstateError):
    """LLM failed to parse the problem description."""
    error_code = "parse_error"


class DataIngestionError(EigenstateError):
    """CSV/Excel file could not be read (bad encoding, empty file, …)."""
    error_code = "data_ingestion_error"


class MappingError(EigenstateError):
    """Column-to-field mapping is invalid or missing required columns."""
    error_code = "mapping_error"


class ModelValidationError(EigenstateError):
    """Spec failed business-rule validation before building the LP/IP."""
    error_code = "validation_error"


class ModelBuildError(EigenstateError):
    """Could not translate the spec into a solver model."""
    error_code = "model_build_error"


class SolverError(EigenstateError):
    """The solver returned an error or hit a time limit."""
    error_code = "solver_error"


class SolveCancelledError(EigenstateError):
    """User cancelled the solve job."""
    error_code = "cancelled"


# ── FastAPI exception handler ─────────────────────────────────────────────────

async def eigenstate_error_handler(request: Request, exc: EigenstateError) -> JSONResponse:
    status = 400
    if isinstance(exc, SolveCancelledError):
        status = 409
    return JSONResponse(
        status_code=status,
        content={"error_code": exc.error_code, "detail": exc.message},
    )
