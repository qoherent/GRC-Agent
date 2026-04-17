"""Env-gated live llama.cpp integration checks."""

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import socket
import unittest
from urllib.parse import urlparse

from grc_agent.agent import GrcAgent
from grc_agent.cli import main as cli_main
from grc_agent.config import load_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import LlamaServerClient, run_bounded_llama_turn

from tests.llama_launcher_support import terminate_pid


LIVE_SERVER_URL = os.environ.get("GRC_AGENT_LIVE_LLAMA_URL")
LIVE_MODEL = os.environ.get("GRC_AGENT_LIVE_LLAMA_MODEL")
_PARSED_LIVE_URL = urlparse(LIVE_SERVER_URL) if LIVE_SERVER_URL else None
_LIVE_LOCAL_HOST = (
    _PARSED_LIVE_URL is not None
    and _PARSED_LIVE_URL.scheme == "http"
    and _PARSED_LIVE_URL.hostname in {"127.0.0.1", "localhost"}
    and _PARSED_LIVE_URL.path in {"", "/"}
)


def _launcher_state_path() -> Path:
    return Path.home() / ".cache" / "grc_agent" / "llama_launcher_state.json"


def _cleanup_live_launcher_state() -> None:
    if LIVE_SERVER_URL is None or LIVE_MODEL is None:
        return
    state_path = _launcher_state_path()
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return
    if (
        payload.get("base_url") == LIVE_SERVER_URL.rstrip("/")
        and payload.get("model_alias") == LIVE_MODEL
        and isinstance(payload.get("pid"), int)
    ):
        terminate_pid(int(payload["pid"]))
        try:
            state_path.unlink()
        except FileNotFoundError:
            return


def tearDownModule() -> None:
    _cleanup_live_launcher_state()


@unittest.skipUnless(
    LIVE_SERVER_URL and LIVE_MODEL and _LIVE_LOCAL_HOST,
    "Set local GRC_AGENT_LIVE_LLAMA_URL and GRC_AGENT_LIVE_LLAMA_MODEL to run live CLI tests.",
)
class LiveCliLlamaServerTests(unittest.TestCase):
    """Exercise the real CLI chat path, including launcher cold start and reuse."""

    def _fixture_path(self) -> Path:
        test_directory = Path(__file__).resolve().parent
        return test_directory / "data" / "random_bit_generator.grc"

    def _run_cli(self, *args: str) -> tuple[int, str]:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = cli_main(list(args))
        return exit_code, output.getvalue()

    def _port_is_open(self) -> bool:
        assert _PARSED_LIVE_URL is not None
        host = _PARSED_LIVE_URL.hostname
        port = _PARSED_LIVE_URL.port or 80
        assert host is not None
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            return False

    def test_live_cli_cold_start_and_reuse(self) -> None:
        if self._port_is_open():
            self.skipTest(
                "Live CLI cold-start proof requires the configured backend port to start closed."
            )

        assert LIVE_SERVER_URL is not None
        assert LIVE_MODEL is not None

        first_exit_code, first_output = self._run_cli(
            "chat",
            str(self._fixture_path()),
            "--message",
            "Summarize the graph.",
            "--llama-server-url",
            LIVE_SERVER_URL,
            "--model",
            LIVE_MODEL,
        )
        second_exit_code, second_output = self._run_cli(
            "chat",
            str(self._fixture_path()),
            "--message",
            "Summarize the graph.",
            "--llama-server-url",
            LIVE_SERVER_URL,
            "--model",
            LIVE_MODEL,
        )

        self.assertEqual(first_exit_code, 0)
        self.assertEqual(second_exit_code, 0)
        self.assertIn("--- Active Session ---", first_output)
        self.assertIn("Started llama.cpp server", first_output)
        self.assertIn("random_bit_generator.grc: 5 blocks, 3 connections", first_output)
        self.assertIn("Reusing llama.cpp server", second_output)
        self.assertIn(
            "random_bit_generator.grc: 5 blocks, 3 connections", second_output
        )

    def test_live_cli_edit_flow(self) -> None:
        assert LIVE_SERVER_URL is not None
        assert LIVE_MODEL is not None

        exit_code, output = self._run_cli(
            "chat",
            str(self._fixture_path()),
            "--message",
            "Change samp_rate to 48000 using the supported edit tools and validate the graph. Do not save it.",
            "--llama-server-url",
            LIVE_SERVER_URL,
            "--model",
            LIVE_MODEL,
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("'name': 'apply_edit'", output)
        self.assertIn('"status": "valid"', output)
        self.assertIn('"active_session"', output)


@unittest.skipUnless(
    LIVE_SERVER_URL and LIVE_MODEL,
    "Set GRC_AGENT_LIVE_LLAMA_URL and GRC_AGENT_LIVE_LLAMA_MODEL to run live llama tests.",
)
class LiveLlamaServerTests(unittest.TestCase):
    """Exercise the real llama.cpp path against the canonical fixture."""

    def _fixture_path(self) -> Path:
        test_directory = Path(__file__).resolve().parent
        return test_directory / "data" / "random_bit_generator.grc"

    def _load_agent(self) -> tuple[GrcAgent, FlowgraphSession]:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return GrcAgent(session), session

    def _client(self) -> LlamaServerClient:
        llama_config = load_app_config().llama
        assert LIVE_SERVER_URL is not None
        return LlamaServerClient(
            LIVE_SERVER_URL,
            timeout_seconds=llama_config.request_timeout_seconds,
            max_tokens=llama_config.max_tokens,
            temperature=llama_config.temperature,
            enable_thinking=llama_config.enable_thinking,
        )

    def test_live_summarize_graph(self) -> None:
        assert LIVE_MODEL is not None
        agent, _session = self._load_agent()
        client = self._client()
        client.require_ready()

        result = run_bounded_llama_turn(
            agent,
            client,
            "Summarize the graph.",
            model=LIVE_MODEL,
        )

        self.assertTrue(result["ok"])
        tool_entries = [turn for turn in agent.history if turn.get("role") == "tool"]
        self.assertEqual([turn["name"] for turn in tool_entries], ["summarize_graph"])
        self.assertEqual(
            result["assistant_text"], tool_entries[0]["content"]["summary"]
        )

    def test_live_apply_edit_flow(self) -> None:
        assert LIVE_MODEL is not None
        agent, session = self._load_agent()
        client = self._client()
        client.require_ready()

        result = run_bounded_llama_turn(
            agent,
            client,
            "Change samp_rate to 48000 using the supported edit tools.",
            model=LIVE_MODEL,
        )

        self.assertTrue(result["ok"])
        tool_entries = [turn for turn in agent.history if turn.get("role") == "tool"]
        tool_names = [entry["name"] for entry in tool_entries]
        self.assertIn("apply_edit", tool_names)
        apply_result = next(
            entry["content"] for entry in tool_entries if entry["name"] == "apply_edit"
        )
        self.assertTrue(apply_result["ok"])
        self.assertEqual(apply_result["validation"]["status"], "valid")

        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None
        variable_block = next(
            block for block in flowgraph.blocks if block.instance_name == "samp_rate"
        )
        self.assertIn(
            str(variable_block.params["parameters"]["value"]), {"48000", "48000.0"}
        )

    def test_live_apply_edit_failure_stays_structured(self) -> None:
        assert LIVE_MODEL is not None
        agent, session = self._load_agent()
        client = self._client()
        client.require_ready()

        result = run_bounded_llama_turn(
            agent,
            client,
            "Using the supported edit tools, try transaction "
            '{"op_type": "update_params", "instance_name": "does_not_exist", '
            '"params": {"value": "123"}} and report the structured result.',
            model=LIVE_MODEL,
        )

        self.assertTrue(result["ok"])
        tool_entries = [turn for turn in agent.history if turn.get("role") == "tool"]
        self.assertTrue(tool_entries)
        self.assertTrue(any(not entry["content"]["ok"] for entry in tool_entries))
        self.assertFalse(session.is_dirty)


if __name__ == "__main__":
    unittest.main()
