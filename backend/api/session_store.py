"""Session store + pipeline state machine (spec §5).

PARSED → (files) AWAITING_COLUMN_MAPPING → (ambiguities) AWAITING_CLARIFICATION
  → READY → VALIDATED → MODELED → SOLVED → EXPLAINED

SQLite-backed store: sessions survive restarts, support multi-session history,
and provide a foundation for analytics and audit trails. All sessions are also
kept in a memory cache so repeated GET /sessions/{id} calls are fast.

Set EIGENSTATE_DB to override the database path (default: eigenstate.db next to
the backend package).
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import uuid
from dataclasses import dataclass, field

from spec.enums import SessionStage
from spec.schema import Explanation, OptimizationSpec, SolveResult


@dataclass
class Session:
    id: str
    token: str = field(default_factory=lambda: secrets.token_hex(24))
    stage: SessionStage = SessionStage.CREATED
    problem_text: str = ""
    spec: OptimizationSpec | None = None
    files: dict[str, bytes] = field(default_factory=dict)        # name -> raw bytes
    file_rows: dict[str, list[dict]] = field(default_factory=dict)
    validation: dict | None = None
    result: SolveResult | None = None
    explanation: Explanation | None = None
    error: str | None = None
    error_code: str | None = None

    def next_action(self) -> str:
        return {
            SessionStage.CREATED: "POST /parse with the problem text",
            SessionStage.PARSED: "internal",
            SessionStage.AWAITING_COLUMN_MAPPING: "POST /column-mapping to confirm mappings",
            SessionStage.AWAITING_CLARIFICATION: "POST /clarify to resolve ambiguities",
            SessionStage.READY: "POST /solve",
            SessionStage.SOLVED: "done",
            SessionStage.EXPLAINED: "done",
            SessionStage.FAILED: "fix input and re-parse",
            SessionStage.CANCELLED: "user cancelled — start a new session",
        }.get(self.stage, "internal")

    def recompute_stage_after_parse(self) -> None:
        assert self.spec is not None
        if any(not m.confirmed for m in self.spec.column_mappings):
            self.stage = SessionStage.AWAITING_COLUMN_MAPPING
        elif any(not t.confirmed for t in self.spec.pairwise_tables):
            self.stage = SessionStage.AWAITING_COLUMN_MAPPING
        elif self.spec.unresolved_ambiguities():
            self.stage = SessionStage.AWAITING_CLARIFICATION
        else:
            self.spec.ready_to_solve = True
            self.stage = SessionStage.READY


_DB_PATH = os.environ.get(
    "EIGENSTATE_DB",
    os.path.join(os.path.dirname(__file__), "..", "eigenstate.db"),
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id               TEXT PRIMARY KEY,
    token            TEXT NOT NULL DEFAULT '',
    stage            TEXT NOT NULL DEFAULT 'created',
    problem_text     TEXT NOT NULL DEFAULT '',
    spec_json        TEXT,
    file_rows_json   TEXT NOT NULL DEFAULT '{}',
    validation_json  TEXT,
    result_json      TEXT,
    explanation_json TEXT,
    error            TEXT,
    error_code       TEXT
);

CREATE TABLE IF NOT EXISTS session_files (
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    file_name   TEXT NOT NULL,
    content     BLOB NOT NULL,
    PRIMARY KEY (session_id, file_name)
);
"""


class SQLiteSessionStore:
    """Session store backed by SQLite with an in-process memory cache."""

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self._db_path = db_path
        self._cache: dict[str, Session] = {}
        self._init_db()
        self._preload()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)

    def _preload(self) -> None:
        """Load all persisted sessions into the memory cache on startup."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, token, stage, problem_text, spec_json, file_rows_json, "
                "validation_json, result_json, explanation_json, error, error_code "
                "FROM sessions"
            ).fetchall()
            for row in rows:
                session = self._deserialize(conn, row)
                self._cache[session.id] = session

    def _deserialize(self, conn: sqlite3.Connection, row: tuple) -> Session:
        (id_, token, stage, problem_text, spec_json, file_rows_json,
         validation_json, result_json, explanation_json, error, error_code) = row

        files: dict[str, bytes] = {}
        for file_name, content in conn.execute(
            "SELECT file_name, content FROM session_files WHERE session_id = ?",
            (id_,),
        ):
            files[file_name] = bytes(content)

        return Session(
            id=id_,
            token=token or secrets.token_hex(24),
            stage=SessionStage(stage),
            problem_text=problem_text or "",
            spec=OptimizationSpec.model_validate_json(spec_json) if spec_json else None,
            files=files,
            file_rows=json.loads(file_rows_json) if file_rows_json else {},
            validation=json.loads(validation_json) if validation_json else None,
            result=SolveResult.model_validate_json(result_json) if result_json else None,
            explanation=Explanation.model_validate_json(explanation_json) if explanation_json else None,
            error=error,
            error_code=error_code,
        )

    # ── public interface ──────────────────────────────────────────────────────

    def create(self) -> Session:
        s = Session(id=uuid.uuid4().hex[:12])
        self._cache[s.id] = s
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, token, stage) VALUES (?, ?, ?)",
                (s.id, s.token, s.stage.value),
            )
        return s

    def get(self, session_id: str) -> Session:
        if session_id not in self._cache:
            raise KeyError(session_id)
        return self._cache[session_id]

    def save(self, session: Session) -> None:
        """Persist the session to SQLite. Call after any mutation."""
        self._cache[session.id] = session
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO sessions
                   (id, token, stage, problem_text, spec_json, file_rows_json,
                    validation_json, result_json, explanation_json, error, error_code)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.id,
                    session.token,
                    session.stage.value,
                    session.problem_text,
                    session.spec.model_dump_json() if session.spec else None,
                    json.dumps(session.file_rows),
                    json.dumps(session.validation) if session.validation else None,
                    session.result.model_dump_json() if session.result else None,
                    session.explanation.model_dump_json() if session.explanation else None,
                    session.error,
                    session.error_code,
                ),
            )
            for name, content in session.files.items():
                conn.execute(
                    """INSERT OR REPLACE INTO session_files
                       (session_id, file_name, content) VALUES (?, ?, ?)""",
                    (session.id, name, content),
                )


SessionStore = SQLiteSessionStore

store = SQLiteSessionStore()
