"""Live two-turn inspect_graph routing against a real installed GRC file.

This test is intentionally opt-in because it calls a real llama.cpp server.
Run it with:

    GRC_AGENT_RUN_LIVE_INSPECT_TEST=1 \
        uv run python -m unittest tests.integration.test_live_inspect_graph_two_turn
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.config import load_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import LlamaServerClient, run_bounded_llama_turn
from tests.llama_eval.harness import (
    executed_tool_calls_since,
    requested_tool_calls_since,
)


LIVE_FLAG = "GRC_AGENT_RUN_LIVE_INSPECT_TEST"
SOURCE_GRAPH = Path("/usr/share/gnuradio/examples/audio/dial_tone.grc")


class LiveInspectGraphTwoTurnTests(unittest.TestCase):
    """Exercise natural read-only chat turns without scripted tool calls."""

    def setUp(self) -> None:
        if os.environ.get(LIVE_FLAG) != "1":
            self.skipTest(f"set {LIVE_FLAG}=1 to run live llama.cpp integration")
        if not SOURCE_GRAPH.is_file():
            self.skipTest(f"installed GNU Radio example missing: {SOURCE_GRAPH}")

        config = load_app_config()
        self.server_url = os.environ.get(
            "GRC_AGENT_LIVE_LLAMA_URL",
            config.llama.server_url,
        )
        self.model = os.environ.get("GRC_AGENT_LIVE_LLAMA_MODEL", config.llama.model)
        self.client = LlamaServerClient(
            self.server_url,
            timeout_seconds=config.llama.request_timeout_seconds,
            max_tokens=int(os.environ.get("GRC_AGENT_LIVE_MAX_TOKENS", "2048")),
            temperature=0.0,
        )
        self.client.require_ready()
        self.client.require_model_alias(self.model)

    def test_summary_then_specific_block_parameter_value(self) -> None:
        """Natural two-turn chat should route to overview, then targeted details."""
        source_hash = _sha256(SOURCE_GRAPH)
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = Path(tmpdir) / "dial_tone_live_inspect.grc"
            shutil.copy2(SOURCE_GRAPH, graph_path)

            session = FlowgraphSession()
            session.load(graph_path)
            agent = GrcAgent(session)

            turn1 = self._run_turn(
                agent,
                "Can you summarize this flowgraph in plain English?",
            )
            self.assertTrue(turn1["result"].get("ok"), turn1)
            self.assertTrue(
                _has_inspect_call(
                    turn1["requested"],
                    view="overview",
                    targets=[],
                    params=["all"],
                ),
                _failure_context("turn1 requested calls", turn1),
            )
            self.assertEqual(
                {"inspect_graph"},
                {call.get("name") for call in turn1["requested"]},
                _failure_context("turn1 used unexpected tool", turn1),
            )

            turn2 = self._run_turn(
                agent,
                "What is the freq value on block analog_sig_source_x_1?",
            )
            self.assertTrue(turn2["result"].get("ok"), turn2)
            self.assertTrue(
                _has_inspect_call(
                    turn2["requested"],
                    view="details",
                    targets=["analog_sig_source_x_1"],
                    params=None,
                ),
                _failure_context("turn2 requested calls", turn2),
            )
            self.assertEqual(
                {"inspect_graph"},
                {call.get("name") for call in turn2["requested"]},
                _failure_context("turn2 used unexpected tool", turn2),
            )
            combined_turn2_text = json.dumps(turn2, sort_keys=True)
            self.assertIn("440", combined_turn2_text)

        self.assertEqual(source_hash, _sha256(SOURCE_GRAPH))

    def _run_turn(self, agent: GrcAgent, prompt: str) -> dict[str, object]:
        history_start = len(agent.history)
        result = run_bounded_llama_turn(
            agent=agent,
            client=self.client,
            model=self.model,
            user_message=prompt,
            advisor_shadow_telemetry=False,
            mvp_tool_profile=True,
        )
        requested = requested_tool_calls_since(agent.history, history_start)
        executed = executed_tool_calls_since(agent.history, history_start)
        turn = {
            "prompt": prompt,
            "assistant_text": result.get("assistant_text", ""),
            "requested": requested,
            "executed": executed,
            "result": result,
        }
        print("\n[live inspect turn]", prompt)
        print("[assistant]", turn["assistant_text"])
        print("[requested]", json.dumps(requested, sort_keys=True))
        print("[executed tools]", [call.get("name") for call in executed])
        return turn


def _has_inspect_call(
    calls: list[dict[str, object]],
    *,
    view: str,
    targets: list[str],
    params: list[str] | None,
) -> bool:
    for call in calls:
        if call.get("name") != "inspect_graph":
            continue
        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            continue
        if arguments.get("view") != view:
            continue
        if arguments.get("targets") != targets:
            continue
        if params is not None and arguments.get("params") != params:
            continue
        return True
    return False


def _failure_context(label: str, turn: dict[str, object]) -> str:
    return label + ":\n" + json.dumps(turn, indent=2, sort_keys=True, default=str)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
