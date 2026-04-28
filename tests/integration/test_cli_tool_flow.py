"""Integration tests for the explicit phase 6 CLI surface."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from grc_agent.cli import main
from grc_agent.config import ConfigError

from tests.llama_launcher_support import reserve_free_port, terminate_pid, write_stub_llama_server


class CliToolFlowIntegrationTests(unittest.TestCase):
    """Exercise the explicit CLI modes and direct tool execution path."""

    def _fixture_path(self) -> Path:
        test_directory = Path(__file__).resolve().parents[1]
        return test_directory / "data" / "random_bit_generator.grc"

    def _run_cli(self, *args: str) -> tuple[int, str]:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(list(args))
        return exit_code, output.getvalue()

    def test_tool_subcommand_runs_summary(self) -> None:
        exit_code, output = self._run_cli(
            "tool",
            "summarize_graph",
            "--file",
            str(self._fixture_path()),
        )

        payload = json.loads(output)
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tool"], "summarize_graph")
        self.assertIn("random_bit_generator.grc", payload["summary"])

    def test_tool_subcommand_runs_session_search(self) -> None:
        exit_code, output = self._run_cli(
            "tool",
            "search_grc",
            "--file",
            str(self._fixture_path()),
            "--args",
            json.dumps({"query": "samp_rate", "scope": "session", "k": 5}),
        )

        payload = json.loads(output)
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tool"], "search_grc")
        self.assertEqual(payload["scope"], "session")
        self.assertGreaterEqual(len(payload["results"]), 1)

    def test_tool_subcommand_runs_apply_edit(self) -> None:
        exit_code, output = self._run_cli(
            "tool",
            "apply_edit",
            "--file",
            str(self._fixture_path()),
            "--args",
            json.dumps(
                {
                    "transaction": {
                        "op_type": "update_params",
                        "instance_name": "samp_rate",
                        "params": {"value": "48000"},
                    }
                }
            ),
        )

        payload = json.loads(output)
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tool"], "apply_edit")
        self.assertEqual(payload["validation"]["status"], "valid")
        self.assertIn("samp_rate", payload["affected_blocks"])

    def test_tool_subcommand_rejects_invalid_args_before_execution(self) -> None:
        exit_code, output = self._run_cli(
            "tool",
            "search_grc",
            "--file",
            str(self._fixture_path()),
            "--args",
            json.dumps({"query": "samp_rate", "scope": "session", "unexpected": True}),
        )

        payload = json.loads(output)
        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "tool_call_invalid")
        self.assertEqual(payload["validation_errors"][0]["code"], "unexpected_argument")
        self.assertEqual(payload["validation_errors"][0]["field"], "unexpected")

    def test_chat_subcommand_starts_server_and_completes_real_turn(self) -> None:
        port = reserve_free_port()
        model_alias = "stub-live-model"

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            write_stub_llama_server(temp_path)
            env = {
                "HOME": str(temp_path),
                "PATH": f"{temp_path}:{os.environ['PATH']}",
                "GRC_AGENT_TEST_LAUNCH_DELAY": "0.2",
            }

            with mock.patch.dict(os.environ, env, clear=False):
                exit_code, output = self._run_cli(
                    "chat",
                    str(self._fixture_path()),
                    "--message",
                    "Summarize the graph.",
                    "--llama-server-url",
                    f"http://127.0.0.1:{port}",
                    "--model",
                    model_alias,
                )

            state_path = temp_path / ".cache" / "grc_agent" / "llama_launcher_state.json"
            if state_path.exists():
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                terminate_pid(int(payload["pid"]))

        self.assertEqual(exit_code, 0)
        self.assertIn("--- Active Session ---", output)
        self.assertIn(str(self._fixture_path()), output)
        self.assertIn(f"Started llama.cpp server for {model_alias} at http://127.0.0.1:{port}", output)
        self.assertIn("summarize_graph: ok", output)

    def test_chat_subcommand_reuses_healthy_server_on_second_run(self) -> None:
        port = reserve_free_port()
        model_alias = "stub-reuse-model"

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            write_stub_llama_server(temp_path)
            env = {
                "HOME": str(temp_path),
                "PATH": f"{temp_path}:{os.environ['PATH']}",
            }

            with mock.patch.dict(os.environ, env, clear=False):
                first_exit_code, first_output = self._run_cli(
                    "chat",
                    str(self._fixture_path()),
                    "--message",
                    "Summarize the graph.",
                    "--llama-server-url",
                    f"http://127.0.0.1:{port}",
                    "--model",
                    model_alias,
                )
                second_exit_code, second_output = self._run_cli(
                    "chat",
                    str(self._fixture_path()),
                    "--message",
                    "Summarize the graph.",
                    "--llama-server-url",
                    f"http://127.0.0.1:{port}",
                    "--model",
                    model_alias,
                )

            state_path = temp_path / ".cache" / "grc_agent" / "llama_launcher_state.json"
            if state_path.exists():
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                terminate_pid(int(payload["pid"]))

        self.assertEqual(first_exit_code, 0)
        self.assertEqual(second_exit_code, 0)
        self.assertIn(f"Started llama.cpp server for {model_alias} at http://127.0.0.1:{port}", first_output)
        self.assertIn(f"Reusing llama.cpp server for {model_alias} at http://127.0.0.1:{port}", second_output)

    def test_cli_rejects_unknown_tool_name_with_parser_error(self) -> None:
        output = StringIO()
        errors = StringIO()

        with redirect_stdout(output), redirect_stderr(errors):
            with self.assertRaises(SystemExit) as raised:
                main(["tool", "definitely_not_a_tool"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid choice", errors.getvalue())

    def test_help_does_not_require_loading_config(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            with mock.patch(
                "grc_agent.cli.load_app_config",
                side_effect=ConfigError("broken config"),
            ):
                with self.assertRaises(SystemExit) as raised:
                    main(["--help"])

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("GRC Agent CLI", output.getvalue())

    def test_doctor_subcommand_reports_json_shape(self) -> None:
        exit_code, output = self._run_cli("doctor", "--json", "--skip-retrieval")

        payload = json.loads(output)
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertIn("checks", payload)
        self.assertIn("summary", payload)
        self.assertEqual(
            [check["name"] for check in payload["checks"][:4]],
            [
                "Python version",
                "grcc on PATH",
                "GNU Radio import/version",
                "App config",
            ],
        )

    def test_doctor_does_not_start_llama_by_default(self) -> None:
        with mock.patch(
            "grc_agent.cli.run_doctor",
            return_value={"ok": True, "checks": [], "summary": "ok"},
        ) as run_doctor_mock:
            exit_code, _ = self._run_cli("doctor", "--skip-retrieval")

        self.assertEqual(exit_code, 0)
        self.assertEqual(run_doctor_mock.call_args.kwargs["check_llama"], False)

    def test_doctor_start_llama_opts_into_llama_check(self) -> None:
        with mock.patch(
            "grc_agent.cli.run_doctor",
            return_value={"ok": True, "checks": [], "summary": "ok"},
        ) as run_doctor_mock:
            exit_code, _ = self._run_cli("doctor", "--skip-retrieval", "--start-llama")

        self.assertEqual(exit_code, 0)
        self.assertEqual(run_doctor_mock.call_args.kwargs["check_llama"], True)

    def test_tool_subcommand_reports_missing_file_without_traceback(self) -> None:
        exit_code, output = self._run_cli(
            "tool",
            "summarize_graph",
            "--file",
            "/tmp/does-not-exist-for-grc-agent.grc",
        )

        payload = json.loads(output)
        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "file_load_error")
        self.assertIn("does-not-exist-for-grc-agent.grc", payload["message"])

    def test_legacy_fake_flag_still_routes_to_phase_six_fake_mode(self) -> None:
        exit_code, output = self._run_cli("--fake", str(self._fixture_path()))

        self.assertEqual(exit_code, 0)
        self.assertIn("Assistant called apply_edit", output)

    def test_health_command_returns_structured_json(self) -> None:
        exit_code, output = self._run_cli("health")

        payload = json.loads(output)
        self.assertIn("status", payload)
        self.assertIn("session_loaded", payload)
        self.assertIn("retrieval_ready", payload)
        self.assertIn("tool_count", payload)
        self.assertGreater(payload["tool_count"], 0)
        self.assertFalse(payload["session_loaded"])

    def test_health_command_exit_code_matches_status(self) -> None:
        exit_code, output = self._run_cli("health")

        payload = json.loads(output)
        expected = 0 if payload["status"] == "ok" else 1
        self.assertEqual(exit_code, expected)
