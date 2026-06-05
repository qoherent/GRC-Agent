"""Tests for the `grc-agent init` subcommand."""

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from grc_agent.cli import _run_init_command
from grc_agent.config import default_app_config


def _parse_args(argv):
    """Helper: parse argv into a Namespace as `_run_init_command` would see it."""
    import argparse
    parser = argparse.ArgumentParser(prog="grc-agent init")
    parser.add_argument("--model")
    parser.add_argument("--hf-model")
    parser.add_argument("--model-path")
    parser.add_argument("--server-url")
    parser.add_argument("--device")
    parser.add_argument("--config-path")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--print-target", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


class InitCommandTests(unittest.TestCase):
    """The `init` subcommand must write a starter config and refuse to overwrite."""

    def setUp(self) -> None:
        self._tmp_home = self._make_temp_home()
        self.addCleanup(self._cleanup_tmp_home)

    def _make_temp_home(self) -> Path:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="grc_agent_init_test_"))
        self._old_home = os.environ.get("HOME", "")
        os.environ["HOME"] = str(tmp)
        return tmp

    def _cleanup_tmp_home(self) -> None:
        import shutil
        os.environ["HOME"] = self._old_home
        shutil.rmtree(self._tmp_home, ignore_errors=True)

    def test_init_writes_starter_config_to_user_path(self) -> None:
        args = _parse_args([
            "--model-path", "/tmp/qwen.gguf",
            "--device", "CPU",
            "--server-url", "http://127.0.0.1:8080",
        ])
        rc = _run_init_command(args)
        self.assertEqual(rc, 0)
        target = self._tmp_home / ".config" / "grc_agent" / "config.toml"
        self.assertTrue(target.is_file())
        body = target.read_text(encoding="utf-8")
        self.assertIn("model_path = \"/tmp/qwen.gguf\"", body)
        self.assertIn("device = \"CPU\"", body)
        self.assertIn("[llama]", body)

    def test_init_refuses_to_overwrite_without_force(self) -> None:
        target = self._tmp_home / ".config" / "grc_agent" / "config.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# pre-existing\n", encoding="utf-8")
        args = _parse_args(["--model-path", "/tmp/other.gguf"])
        rc = _run_init_command(args)
        self.assertEqual(rc, 1)
        # Original content is preserved.
        self.assertEqual(target.read_text(encoding="utf-8"), "# pre-existing\n")

    def test_init_force_overwrites_existing_config(self) -> None:
        target = self._tmp_home / ".config" / "grc_agent" / "config.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# pre-existing\n", encoding="utf-8")
        args = _parse_args([
            "--model-path", "/tmp/other.gguf",
            "--force",
        ])
        rc = _run_init_command(args)
        self.assertEqual(rc, 0)
        self.assertIn("model_path = \"/tmp/other.gguf\"", target.read_text(encoding="utf-8"))

    def test_init_respects_explicit_config_path(self) -> None:
        explicit = self._tmp_home / "my-config.toml"
        args = _parse_args([
            "--config-path", str(explicit),
            "--model-path", "/tmp/explicit.gguf",
        ])
        rc = _run_init_command(args)
        self.assertEqual(rc, 0)
        self.assertTrue(explicit.is_file())

    def test_init_print_target_does_not_write(self) -> None:
        args = _parse_args(["--print-target"])
        with mock.patch("builtins.print") as mock_print:
            rc = _run_init_command(args)
        self.assertEqual(rc, 0)
        target = self._tmp_home / ".config" / "grc_agent" / "config.toml"
        self.assertFalse(target.exists())
        # print was called exactly once with the resolved target path.
        self.assertEqual(mock_print.call_count, 1)

    def test_init_json_output_round_trip(self) -> None:
        args = _parse_args([
            "--model-path", "/tmp/json.gguf",
            "--json",
        ])
        with mock.patch("builtins.print") as mock_print:
            rc = _run_init_command(args)
        self.assertEqual(rc, 0)
        # First print call is the JSON payload.
        printed = mock_print.call_args_list[0].args[0]
        payload = json.loads(printed)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["model_path"], "/tmp/json.gguf")
        self.assertIn("target", payload)

    def test_init_written_config_loads_through_loader(self) -> None:
        """The starter file must round-trip through load_app_config without error."""
        from grc_agent.config import load_app_config

        args = _parse_args([
            "--model-path", "/tmp/roundtrip.gguf",
            "--device", "CPU",
        ])
        rc = _run_init_command(args)
        self.assertEqual(rc, 0)
        target = self._tmp_home / ".config" / "grc_agent" / "config.toml"
        loaded = load_app_config(target)
        self.assertEqual(loaded.llama.model_path, "/tmp/roundtrip.gguf")
        self.assertEqual(loaded.llama.device, "CPU")

    def test_init_writes_built_in_defaults_when_no_flags_passed_non_interactive(self) -> None:
        """In a non-TTY, non-interactive run, init writes a config with built-in defaults."""
        args = _parse_args([])
        # stdin/stdout are not TTYs under unittest, so the function takes the
        # non-interactive branch and uses the built-in defaults.
        rc = _run_init_command(args)
        self.assertEqual(rc, 0)
        target = self._tmp_home / ".config" / "grc_agent" / "config.toml"
        self.assertTrue(target.is_file())
        body = target.read_text(encoding="utf-8")
        defaults = default_app_config()
        self.assertIn(f'model = "{defaults.llama.model}"', body)
        self.assertIn(f'hf_model = "{defaults.llama.hf_model}"', body)


if __name__ == "__main__":
    unittest.main()
