"""Unittest wrapper that auto-discovers every fixture in
``fixtures/*.json`` and runs it through the eval harness.

Each fixture becomes a single ``unittest`` test case. Failures in
``expect`` assertions surface as test failures; harness exceptions
surface as errors. Run with ``uv run python -m unittest
tests.eval_chat.test_fixtures``.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.eval_chat.harness import (
    _FIXTURES_DIR,
    run_fixture,
)


def _make_test(fixture_path: Path) -> unittest.TestCase:
    class _FixtureTest(unittest.TestCase):
        def test_fixture(self) -> None:
            result = run_fixture(fixture_path)
            self.assertTrue(
                result.passed,
                msg=(
                    f"Fixture {fixture_path.name} failed:\n  "
                    + "\n  ".join(result.failures)
                ),
            )

    _FixtureTest.__name__ = f"Fixture_{fixture_path.stem}"
    return _FixtureTest


def _load_all() -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for path in sorted(_FIXTURES_DIR.glob("*.json")):
        suite.addTest(loader.loadTestsFromTestCase(_make_test(path)))
    return suite


def load_tests(loader: unittest.TestLoader, standard_tests: unittest.TestSuite, pattern: str | None) -> unittest.TestSuite:
    return _load_all()
