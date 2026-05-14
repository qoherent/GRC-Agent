"""Production-readiness evidence harness skeletons."""

from __future__ import annotations

from pathlib import Path
import unittest


def load_tests(
    loader: unittest.TestLoader,
    standard_tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    """Make `python -m unittest tests.production` discover this package."""
    package_dir = Path(__file__).resolve().parent
    standard_tests.addTests(
        loader.discover(
            start_dir=str(package_dir),
            pattern=pattern or "test*.py",
            top_level_dir=str(package_dir.parents[1]),
        )
    )
    return standard_tests
