"""Lightweight smoke tests for daily-use prototype validation.

No model backend required. All tests run in under a few seconds.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"
MESSAGE_GRAPH = Path("/usr/share/gnuradio/examples/digital/packet/tx_stage0.grc")


class SmokeDoctor(unittest.TestCase):

    def test_doctor_returns_zero_and_reports(self):
        proc = subprocess.run(
            ["uv", "run", "grc-agent", "doctor"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Python version", proc.stdout)
        self.assertIn("grcc on PATH", proc.stdout)
        self.assertIn("Environment OK", proc.stdout)

    def test_doctor_json_mode(self):
        proc = subprocess.run(
            ["uv", "run", "grc-agent", "doctor", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        import json
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertIn("checks", report)


class SmokeToolSummarize(unittest.TestCase):

    def test_direct_tool_summarize(self):
        proc = subprocess.run(
            ["uv", "run", "grc-agent", "tool", "summarize_graph",
             "--file", str(FIXTURE)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("block_count", proc.stdout)
        self.assertIn("connection_count", proc.stdout)


class SmokeToolValidate(unittest.TestCase):

    def test_direct_tool_validate(self):
        proc = subprocess.run(
            ["uv", "run", "grc-agent", "tool", "validate_graph",
             "--file", str(FIXTURE)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("valid", proc.stdout)


class SmokeNewGraphSave(unittest.TestCase):

    def test_save_new_graph_no_path_returns_error(self):
        from grc_agent.agent import GrcAgent
        from grc_agent.flowgraph_session import FlowgraphSession

        session = FlowgraphSession.create()
        agent = GrcAgent(session, catalog_root="/usr/share/gnuradio/grc/blocks")
        agent._tools["new_grc"] = agent._new_grc
        agent.execute_tool("new_grc", {"profile": "minimal"})
        result = agent.execute_tool("save_graph", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "SAVE_PATH_REQUIRED")


class SmokeRawYamlRefusal(unittest.TestCase):

    def test_raw_yaml_request_refused(self):
        from grc_agent.agent import GrcAgent
        agent = GrcAgent(catalog_root="/usr/share/gnuradio/grc/blocks")
        result = agent.check_unsupported_request(
            "Edit the raw .grc YAML directly to remove a block."
        )
        self.assertIsNotNone(result)
        self.assertIn("unsupported", result["assistant_text"].lower())

    def test_normal_edit_not_refused(self):
        from grc_agent.agent import GrcAgent
        agent = GrcAgent(catalog_root="/usr/share/gnuradio/grc/blocks")
        result = agent.check_unsupported_request(
            "Change the frequency to 1000 and validate."
        )
        self.assertIsNone(result)


class SmokeMessagePortGraph(unittest.TestCase):

    def test_message_graph_loads_and_validates(self):
        if not MESSAGE_GRAPH.exists():
            self.skipTest("Message graph fixture not installed")
        from grc_agent.agent import GrcAgent
        from grc_agent.flowgraph_session import FlowgraphSession

        session = FlowgraphSession()
        session.load(str(MESSAGE_GRAPH))
        agent = GrcAgent(session, catalog_root="/usr/share/gnuradio/grc/blocks")
        agent._tools["validate_graph"] = agent._validate_graph
        result = agent.execute_tool("validate_graph", {})
        self.assertTrue(result["ok"])


class SmokeChatLoadsGraph(unittest.TestCase):

    def test_chat_command_loads_existing_grc(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import shutil
            dst = Path(tmpdir) / FIXTURE.name
            shutil.copy2(FIXTURE, dst)
            proc = subprocess.run(
                ["uv", "run", "grc-agent", "chat", str(dst),
                 "--message", "Summarize the graph."],
                capture_output=True, text=True, timeout=60,
            )
            self.assertIn("--- Active Session ---", proc.stdout)


if __name__ == "__main__":
    unittest.main()
