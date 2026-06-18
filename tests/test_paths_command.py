"""Tests for the library-level ``collect_package_paths`` helper.

The ``grc-agent paths`` CLI subcommand was removed; only the underlying
``config.collect_package_paths`` library helper remains (used by the GUI
and other tooling). These tests exercise the helper directly.
"""

import unittest
from pathlib import Path

from grc_agent.config import collect_package_paths


class PackagePathsTests(unittest.TestCase):
    """``collect_package_paths`` lists every filesystem location the package uses."""

    def test_collect_package_paths_includes_expected_keys(self) -> None:
        paths = collect_package_paths()
        expected_keys = {
            "config_repo",
            "config_user",
            "history",
            "history_env_var",
            "grc_agent_state",
            "grc_agent_cache",
            "preferences",
            "sessions_db",
        }
        self.assertTrue(expected_keys.issubset(paths.keys()))

    def test_history_env_var_is_stable(self) -> None:
        """The env-var name must remain `GRC_AGENT_HISTORY_PATH` (a public contract)."""
        paths = collect_package_paths()
        self.assertEqual(paths["history_env_var"], "GRC_AGENT_HISTORY_PATH")

    def test_paths_are_absolute(self) -> None:
        """All paths should be absolute."""
        paths = collect_package_paths()
        for key, value in paths.items():
            if key == "history_env_var":
                continue
            self.assertTrue(
                Path(value).is_absolute(),
                f"{key} = {value!r} is not absolute",
            )


if __name__ == "__main__":
    unittest.main()
