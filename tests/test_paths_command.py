"""Tests for the `grc-agent paths` subcommand."""

import json
import unittest
from unittest import mock

from grc_agent.cli import _run_paths_command
from grc_agent.paths import collect_package_paths


def _parse_args(argv):
    """Helper: parse argv into a Namespace as `_run_paths_command` would see it."""
    import argparse
    parser = argparse.ArgumentParser(prog="grc-agent paths")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


class PathsCommandTests(unittest.TestCase):
    """The `paths` subcommand must list every filesystem location the package uses."""

    def test_collect_package_paths_includes_expected_keys(self) -> None:
        paths = collect_package_paths()
        expected_keys = {
            "config_repo",
            "config_user",
            "history",
            "history_env_var",
            "vector_index_default",
            "fastembed_cache",
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

    def test_json_output_is_valid_json(self) -> None:
        args = _parse_args(["--json"])
        with mock.patch("builtins.print") as mock_print:
            rc = _run_paths_command(args)
        self.assertEqual(rc, 0)
        printed = mock_print.call_args.args[0]
        payload = json.loads(printed)
        self.assertIn("config_user", payload)
        self.assertIn("sessions_db", payload)

    def test_human_output_lists_all_keys(self) -> None:
        args = _parse_args([])
        with mock.patch("builtins.print") as mock_print:
            rc = _run_paths_command(args)
        self.assertEqual(rc, 0)
        printed_text = "".join(
            str(call.args[0]) for call in mock_print.call_args_list
        )
        paths = collect_package_paths()
        for key in paths:
            self.assertIn(key, printed_text)

    def test_paths_are_absolute(self) -> None:
        """All paths should be absolute."""
        from pathlib import Path
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
