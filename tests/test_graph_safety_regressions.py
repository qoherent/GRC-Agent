"""Focused safety regressions restored from the deleted-test audit."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.clarification import (
    ClarificationOption,
    ClarificationRequest,
    pending_clarification_reminder,
)
from grc_agent.session_ops import connection_id

DATA_DIR = Path(__file__).resolve().parent / "data"
RANDOM_FIXTURE = DATA_DIR / "random_bit_generator.grc"
MESSAGE_FIXTURE = DATA_DIR / "rewire_message_ambiguous.grc"


def _load_agent(path: Path) -> GrcAgent:
    session = FlowgraphSession()
    session.load(path)
    return GrcAgent(session)


def _connection_ids(session: FlowgraphSession) -> list[str]:
    assert session.flowgraph is not None
    return sorted(
        connection_id(c.src_block, c.src_port, c.dst_block, c.dst_port)
        for c in session.flowgraph.connections
    )


class GraphSafetyRegressionTests(unittest.TestCase):
    def _load_temp_agent(self, source: Path) -> GrcAgent:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / source.name
        shutil.copy2(source, path)
        session = FlowgraphSession()
        session.load(path)
        return GrcAgent(session)

    def test_message_ports_parse_disconnect_autosave_and_reload(self) -> None:
        agent = self._load_temp_agent(MESSAGE_FIXTURE)
        assert agent.session.path is not None
        before_ids = _connection_ids(agent.session)
        self.assertIn("strobe_0:strobe->debug_0:print", before_ids)
        assert agent.session.flowgraph is not None
        target = next(
            c for c in agent.session.flowgraph.connections if c.dst_block == "debug_0"
        )
        self.assertIsInstance(target.src_port, str)
        self.assertIsInstance(target.dst_port, str)

        result = agent.execute_tool(
            "change_graph",
            {"remove_connections": ["strobe_0:strobe->debug_0:print"]},
        )

        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result.get("committed"), result)
        self.assertEqual(result.get("autosave", {}).get("ok"), True)
        self.assertNotIn(
            "strobe_0:strobe->debug_0:print",
            _connection_ids(agent.session),
        )

        reloaded = FlowgraphSession()
        reloaded.load(agent.session.path)
        self.assertNotIn("strobe_0:strobe->debug_0:print", _connection_ids(reloaded))
        self.assertIn("strobe_0:strobe->pdu_0:generate", _connection_ids(reloaded))

    def test_insert_on_connection_using_primitives_succeeds(self) -> None:
        """Block insertion on a stream connection using add_blocks + remove_connections + add_connections."""
        agent = self._load_temp_agent(RANDOM_FIXTURE)
        assert agent.session.path is not None

        result = agent.execute_tool(
            "change_graph",
            {
                "remove_connections": [
                    "analog_random_source_x_0:0->blocks_throttle2_0:0"
                ],
                "add_blocks": [
                    {
                        "block_id": "blocks_throttle2",
                        "instance_name": "blocks_throttle2_inserted",
                        "params": {
                            "samples_per_second": "samp_rate",
                            "type": "byte",
                        },
                    }
                ],
                "add_connections": [
                    {"src": {"block": "analog_random_source_x_0", "port": 0},
                     "dst": {"block": "blocks_throttle2_inserted", "port": 0}},
                    {"src": {"block": "blocks_throttle2_inserted", "port": 0},
                     "dst": {"block": "blocks_throttle2_0", "port": 0}},
                ],
            },
        )

        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result.get("committed"), result)
        self.assertEqual(result.get("validation_result", {}).get("status"), "valid")
        self.assertEqual(result.get("autosave", {}).get("ok"), True)
        graph_delta = result.get("graph_delta") or {}
        self.assertEqual(graph_delta.get("added_blocks"), ["blocks_throttle2_inserted"])
        self.assertEqual(
            graph_delta.get("removed_connections"),
            ["analog_random_source_x_0:0->blocks_throttle2_0:0"],
        )
        self.assertIn(
            "analog_random_source_x_0:0->blocks_throttle2_inserted:0",
            graph_delta.get("added_connections", []),
        )

        reloaded = FlowgraphSession()
        reloaded.load(agent.session.path)
        self.assertIn(
            "blocks_throttle2_inserted:0->blocks_throttle2_0:0",
            _connection_ids(reloaded),
        )



    def test_pending_clarification_invalid_and_custom_replies_do_not_mutate(self) -> None:
        agent = _load_agent(MESSAGE_FIXTURE)
        before_revision = agent.session.state_revision
        before_ids = _connection_ids(agent.session)
        payload = ClarificationRequest(
            kind="ambiguous_connection",
            question="Which connection should be changed?",
            state_revision=agent.session.state_revision,
            options=[
                ClarificationOption(
                    label="A",
                    title="strobe_0:strobe->debug_0:print",
                    description="disconnect debug_0",
                    tool_name="change_graph",
                    tool_args={
                        "remove_connections": ["strobe_0:strobe->debug_0:print"]
                    },
                )
            ],
        ).to_dict()
        agent._store_pending_clarification(payload)

        invalid = agent.resolve_pending_clarification("B")
        self.assertEqual(invalid.get("mode"), "reminder", invalid)
        self.assertIn("not a valid option", invalid.get("text", ""))
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(_connection_ids(agent.session), before_ids)

        custom = agent.resolve_pending_clarification("D")
        self.assertEqual(custom.get("mode"), "custom", custom)
        self.assertEqual(agent.session.state_revision, before_revision)
        self.assertEqual(_connection_ids(agent.session), before_ids)

    def test_pending_clarification_expires_after_revision_change(self) -> None:
        agent = _load_agent(RANDOM_FIXTURE)
        payload = ClarificationRequest(
            kind="ambiguous_target",
            question="Which target?",
            state_revision=agent.session.state_revision,
            options=[
                ClarificationOption(
                    label="A",
                    title="samp_rate",
                    description="set sample rate",
                    tool_name="change_graph",
                    tool_args={
                        "update_params": [
                            {"instance_name": "samp_rate", "params": {"value": "48000"}}
                        ],
                    },
                )
            ],
        ).to_dict()
        agent._store_pending_clarification(payload)
        agent.session.set_param("samp_rate", "value", "48000")

        expired = agent.resolve_pending_clarification("A")

        self.assertEqual(expired.get("mode"), "expired", expired)
        self.assertIn("no longer valid", expired.get("text", ""))
        self.assertEqual(agent.resolve_pending_clarification("A").get("mode"), "none")

    def test_pending_clarification_reminder_is_concise_not_raw_json(self) -> None:
        payload = ClarificationRequest(
            kind="ambiguous_connection",
            question="Which connection?",
            options=[
                ClarificationOption(
                    label="A",
                    title="strobe_0:strobe->debug_0:print",
                    description="debug message edge",
                    tool_name="change_graph",
                    tool_args={"remove_connections": ["strobe_0:strobe->debug_0:print"]},
                )
            ],
        ).to_dict()

        reminder = pending_clarification_reminder(payload)

        self.assertIn("A) strobe_0:strobe->debug_0:print", reminder)
        self.assertIn("D) Other / custom", reminder)
        self.assertNotIn("{", reminder)
        self.assertNotIn("tool_args", reminder)


if __name__ == "__main__":
    unittest.main()
