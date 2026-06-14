"""Safety checks for raw YAML refusal and internal save path handling."""

import shutil
import tempfile
import unittest
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession

FIXTURE = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"


class RawYamlAndSavePathTests(unittest.TestCase):
    def test_raw_yaml_edit_is_refused_without_exposing_internal_tools(self) -> None:
        agent = GrcAgent(catalog_root="/usr/share/gnuradio/grc/blocks")

        result = agent.check_unsupported_request("Edit the raw .grc YAML directly.")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "guard")
        self.assertEqual(result["tool_calls_executed"], 0)
        self.assertIn("YAML editing is not supported", result["assistant_text"])
        # Guard messages state facts only (AGENTS.md) — no internal tool names leaked.
        self.assertNotIn("apply_edit", result["assistant_text"])
        self.assertNotIn("propose_edit", result["assistant_text"])

    def test_normal_graph_requests_are_not_refused_by_yaml_guard(self) -> None:
        agent = GrcAgent(catalog_root="/usr/share/gnuradio/grc/blocks")

        self.assertIsNone(agent.check_unsupported_request("Set samp_rate to 48000."))
        self.assertIsNone(agent.check_unsupported_request("Summarize this graph."))

    def test_new_graph_save_requires_explicit_path(self) -> None:
        session = FlowgraphSession()
        agent = GrcAgent(session, catalog_root="/usr/share/gnuradio/grc/blocks")
        agent._tools["new_grc"] = agent._new_grc

        new_result = agent.execute_tool("new_grc", {"profile": "minimal"})
        save_result = agent.execute_tool("save_graph", {})

        self.assertTrue(new_result["ok"], new_result)
        self.assertFalse(save_result["ok"], save_result)
        self.assertEqual(save_result["error_type"], "SAVE_PATH_REQUIRED")

    def test_loaded_temp_graph_can_save_to_its_own_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dst = Path(tmpdir) / FIXTURE.name
            shutil.copy2(FIXTURE, dst)
            session = FlowgraphSession()
            session.load(dst)
            agent = GrcAgent(session, catalog_root="/usr/share/gnuradio/grc/blocks")
            agent._tools["save_graph"] = agent._save_graph

            save_result = agent.execute_tool("save_graph", {})

        self.assertTrue(save_result["ok"], save_result)

    def test_internal_save_refuses_repo_fixture_path(self) -> None:
        session = FlowgraphSession()
        session.load(FIXTURE)
        agent = GrcAgent(session, catalog_root="/usr/share/gnuradio/grc/blocks")
        agent._tools["save_graph"] = agent._save_graph

        save_result = agent.execute_tool("save_graph", {})

        self.assertFalse(save_result["ok"], save_result)
        self.assertEqual(save_result["error_type"], "save_refused")


if __name__ == "__main__":
    unittest.main()
