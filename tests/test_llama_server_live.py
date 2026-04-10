"""Env-gated live llama.cpp integration checks."""

import os
from pathlib import Path
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.config import load_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import LlamaServerClient, run_bounded_llama_turn


LIVE_SERVER_URL = os.environ.get("GRC_AGENT_LIVE_LLAMA_URL")
LIVE_MODEL = os.environ.get("GRC_AGENT_LIVE_LLAMA_MODEL")


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
            max_steps=load_app_config().llama.max_steps,
        )

        self.assertTrue(result["ok"])
        tool_entries = [turn for turn in agent.history if turn.get("role") == "tool"]
        self.assertEqual([turn["name"] for turn in tool_entries], ["summarize_graph"])
        self.assertEqual(result["assistant_text"], tool_entries[0]["content"]["summary"])

    def test_live_set_variable_and_validate(self) -> None:
        assert LIVE_MODEL is not None
        agent, session = self._load_agent()
        client = self._client()
        client.require_ready()

        result = run_bounded_llama_turn(
            agent,
            client,
            "Change the samp_rate variable to 48000 and validate the graph.",
            model=LIVE_MODEL,
            max_steps=load_app_config().llama.max_steps,
        )

        self.assertTrue(result["ok"])
        tool_names = [
            turn["name"]
            for turn in agent.history
            if turn.get("role") == "tool"
        ]
        self.assertEqual(tool_names, ["set_variable", "validate_graph"])
        self.assertEqual(
            result["assistant_text"],
            "Set samp_rate to 48000 and validated the graph successfully.",
        )
        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None
        variable_block = next(
            block for block in flowgraph.blocks if block.instance_name == "samp_rate"
        )
        self.assertEqual(variable_block.params["parameters"]["value"], 48000)
        self.assertTrue(
            any(
                turn.get("role") == "tool"
                and turn.get("name") == "validate_graph"
                and turn["content"]["valid"]
                for turn in agent.history
            )
        )

    def test_live_missing_variable_recovery(self) -> None:
        assert LIVE_MODEL is not None
        agent, session = self._load_agent()
        client = self._client()
        client.require_ready()

        result = run_bounded_llama_turn(
            agent,
            client,
            "Set the variable does_not_exist to 123 and validate the graph.",
            model=LIVE_MODEL,
            max_steps=load_app_config().llama.max_steps,
        )

        self.assertTrue(result["ok"])
        tool_entries = [turn for turn in agent.history if turn.get("role") == "tool"]
        self.assertEqual([entry["name"] for entry in tool_entries], ["set_variable", "validate_graph"])
        self.assertEqual(
            result["assistant_text"],
            "Could not set the requested variable: Variable block not found: does_not_exist. "
            "The graph validated successfully.",
        )
        self.assertFalse(tool_entries[0]["content"]["ok"])
        self.assertTrue(tool_entries[1]["content"]["valid"])
        self.assertFalse(session.is_dirty)


if __name__ == "__main__":
    unittest.main()
