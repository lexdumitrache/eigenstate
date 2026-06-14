"""Shared pytest configuration and fixtures."""
import subprocess
import sys

import pytest


def _ortools_works() -> bool:
    """Check via subprocess so a C-level abort cannot kill the test runner."""
    result = subprocess.run(
        [sys.executable, "-c", "from ortools.sat.python import cp_model"],
        capture_output=True,
    )
    return result.returncode == 0


_ORTOOLS_OK = _ortools_works()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "ortools: mark test as requiring a working ortools installation",
    )


def pytest_collection_modifyitems(items):
    skip = pytest.mark.skip(
        reason="ortools unavailable or incompatible with this Python environment"
    )
    for item in items:
        if item.get_closest_marker("ortools") and not _ORTOOLS_OK:
            item.add_marker(skip)
