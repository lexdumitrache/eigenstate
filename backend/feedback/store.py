"""Persistent feedback log stored as a JSON file.

Each entry captures whether the user accepted the solver's plan and, if not,
what they changed and why. These records drive the "learned preferences" that
surface on future solves.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import List

from spec.schema import FeedbackChange, FeedbackEntry

_FEEDBACK_FILE = os.path.join(
    os.path.dirname(__file__), "../../feedback_log.json"
)


def _load_raw() -> list[dict]:
    if not os.path.exists(_FEEDBACK_FILE):
        return []
    try:
        with open(_FEEDBACK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_raw(entries: list[dict]) -> None:
    with open(_FEEDBACK_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def _infer_preferences(changes: list[FeedbackChange]) -> list[str]:
    """Derive plain-English preference summaries from the user's changes."""
    prefs: list[str] = []
    for ch in changes:
        reason = ch.reason.strip()
        original = ch.original_decision.strip()
        changed = ch.user_change.strip()
        if reason:
            prefs.append(
                f'Prefers "{changed}" over "{original}" — reason: {reason}'
            )
        else:
            prefs.append(f'Changed "{original}" to "{changed}"')
    return prefs


def save_feedback(entry: FeedbackEntry) -> FeedbackEntry:
    """Infer preferences, persist the entry, and return the enriched entry."""
    entry.inferred_preferences = _infer_preferences(entry.changes)
    raw = _load_raw()
    raw.append(entry.model_dump())
    _save_raw(raw)
    return entry


def load_all() -> List[FeedbackEntry]:
    return [FeedbackEntry(**r) for r in _load_raw()]


def get_relevant_preferences(problem_type: str, limit: int = 5) -> list[str]:
    """Return the most recent inferred preferences for this problem type."""
    all_entries = load_all()
    matching = [
        e for e in reversed(all_entries)
        if e.problem_type == problem_type
    ]
    prefs: list[str] = []
    for entry in matching:
        prefs.extend(entry.inferred_preferences)
        if len(prefs) >= limit:
            break
    return prefs[:limit]


def preference_summary() -> dict:
    """Aggregate all stored preferences grouped by problem type."""
    all_entries = load_all()
    by_type: dict[str, list[str]] = {}
    for entry in all_entries:
        prefs = entry.inferred_preferences
        if prefs:
            by_type.setdefault(entry.problem_type, []).extend(prefs)
    return {
        "total_sessions": len(all_entries),
        "accepted_count": sum(1 for e in all_entries if e.accepted),
        "preferences_by_type": by_type,
    }
