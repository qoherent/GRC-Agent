"""Tests for raw-YAML refusal guard and save-path-required error."""

import unittest
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession

FIXTURE = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"


class TestRawYamlRefusalGuard(unittest.TestCase):

    def _make_agent(self):
        return GrcAgent(catalog_root="/usr/share/gnuradio/grc/blocks")

    def test_edit_raw_grc_yaml_directly(self):
        agent = self._make_agent()
        result = agent.check_unsupported_request(
            "Edit the raw .grc YAML directly to remove a block."
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        self.assertIn("Raw .grc YAML editing is unsupported", result["assistant_text"])

    def test_patch_yaml_manually(self):
        agent = self._make_agent()
        result = agent.check_unsupported_request(
            "Patch the YAML manually and fix the connection."
        )
        self.assertIsNotNone(result)
        self.assertIn("unsupported", result["assistant_text"].lower())

    def test_modify_yaml_text(self):
        agent = self._make_agent()
        result = agent.check_unsupported_request(
            "Modify the YAML text in the file."
        )
        self.assertIsNotNone(result)

    def test_remove_block_by_editing_yaml(self):
        agent = self._make_agent()
        result = agent.check_unsupported_request(
            "Remove a block by editing the YAML."
        )
        self.assertIsNotNone(result)

    def test_raw_grc_file(self):
        agent = self._make_agent()
        result = agent.check_unsupported_request(
            "Edit the raw .grc file directly."
        )
        self.assertIsNotNone(result)

    def test_raw_yaml_edit(self):
        agent = self._make_agent()
        result = agent.check_unsupported_request(
            "Can you do a raw YAML edit on this graph?"
        )
        self.assertIsNotNone(result)

    def test_normal_edit_not_refused(self):
        agent = self._make_agent()
        result = agent.check_unsupported_request(
            "Change the frequency parameter to 1000 and validate the graph."
        )
        self.assertIsNone(result)

    def test_summarize_not_refused(self):
        agent = self._make_agent()
        result = agent.check_unsupported_request(
            "Summarize this flowgraph and show me the connections."
        )
        self.assertIsNone(result)

    def test_save_not_refused(self):
        agent = self._make_agent()
        result = agent.check_unsupported_request(
            "Save the graph to /tmp/output.grc."
        )
        self.assertIsNone(result)

    def test_load_grc_not_refused(self):
        agent = self._make_agent()
        result = agent.check_unsupported_request(
            "Load the .grc file and show me what blocks are in it."
        )
        self.assertIsNone(result)

    def test_add_block_not_refused(self):
        agent = self._make_agent()
        result = agent.check_unsupported_request(
            "Add a throttle block and connect it to the signal source."
        )
        self.assertIsNone(result)

    def test_refusal_response_structure(self):
        agent = self._make_agent()
        result = agent.check_unsupported_request(
            "Edit the raw .grc YAML directly."
        )
        self.assertIn("ok", result)
        self.assertIn("assistant_text", result)
        self.assertIn("steps", result)
        self.assertEqual(result["steps"], 0)
        self.assertEqual(result["tool_calls_executed"], 0)
        self.assertEqual(result["model"], "guard")

    def test_refusal_mentions_validated_tools(self):
        agent = self._make_agent()
        result = agent.check_unsupported_request(
            "Edit the raw .grc YAML directly to remove a block."
        )
        self.assertIn("change_graph", result["assistant_text"])
        self.assertIn("save_graph_explicit", result["assistant_text"])
        self.assertNotIn("apply_edit", result["assistant_text"])
        self.assertNotIn("propose_edit", result["assistant_text"])


class TestSavePathRequired(unittest.TestCase):

    def test_save_new_graph_no_path_returns_error(self):
        session = FlowgraphSession()
        agent = GrcAgent(session, catalog_root="/usr/share/gnuradio/grc/blocks")
        agent._tools["new_grc"] = agent._new_grc
        new_result = agent.execute_tool("new_grc", {"profile": "minimal"})
        self.assertTrue(new_result["ok"])
        save_result = agent.execute_tool("save_graph", {})
        self.assertFalse(save_result["ok"])
        self.assertEqual(save_result["error_type"], "SAVE_PATH_REQUIRED")
        self.assertIn("save_graph(path=", save_result["message"])

    def test_save_new_graph_with_explicit_path_works(self):
        import tempfile
        session = FlowgraphSession()
        agent = GrcAgent(session, catalog_root="/usr/share/gnuradio/grc/blocks")
        agent._tools["new_grc"] = agent._new_grc
        agent._tools["validate_graph"] = agent._validate_graph
        new_result = agent.execute_tool("new_grc", {"profile": "minimal"})
        self.assertTrue(new_result["ok"])
        validate_result = agent.execute_tool("validate_graph", {})
        self.assertTrue(validate_result["ok"])
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = str(Path(tmpdir) / "test_save.grc")
            save_result = agent.execute_tool("save_graph", {"path": save_path})
            self.assertTrue(save_result["ok"])

    def test_save_loaded_graph_no_path_works(self):
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            dst = Path(tmpdir) / FIXTURE.name
            shutil.copy2(FIXTURE, dst)
            session = FlowgraphSession()
            session.load(str(dst))
            agent = GrcAgent(session, catalog_root="/usr/share/gnuradio/grc/blocks")
            agent._tools["save_graph"] = agent._save_graph
            save_result = agent.execute_tool("save_graph", {})
            self.assertTrue(save_result["ok"])

    def test_internal_save_graph_refuses_unsafe_canonical_path(self):
        session = FlowgraphSession()
        session.load(str(FIXTURE))
        agent = GrcAgent(session, catalog_root="/usr/share/gnuradio/grc/blocks")
        agent._tools["save_graph"] = agent._save_graph

        save_result = agent.execute_tool("save_graph", {})

        self.assertFalse(save_result["ok"])
        self.assertEqual(save_result["error_type"], "save_refused")


if __name__ == "__main__":
    unittest.main()
