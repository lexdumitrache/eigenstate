#!/usr/bin/env bash
# Run from the repo root before creating a zip for handoff.
# Removes all generated, cached, and local-state files that must not be shipped.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "Removing build artifacts and caches..."
rm -rf frontend/node_modules frontend/dist
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
rm -rf .pytest_cache .mypy_cache .ruff_cache

echo "Removing local runtime state..."
rm -f backend/eigenstate.db
rm -f feedback_log.json

echo "Removing editor/tool state..."
rm -rf .claude

echo "Done. Safe to zip (exclude .git unless you want to share history):"
echo "  zip -r ../eigenstate.zip . --exclude '*.git*'"
