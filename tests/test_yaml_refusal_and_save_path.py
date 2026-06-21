"""Safety checks for raw YAML refusal and internal save path handling."""

import shutil
import tempfile
import unittest
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession

FIXTURE = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"


class RawYamlAndSavePathTests(unittest.TestCase):
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
