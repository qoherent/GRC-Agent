"""Regression guards for architecture-audit maintenance watch items."""

from __future__ import annotations

from pathlib import Path
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import LlamaServerClient
from grc_agent.runtime.transaction_normalization import TransactionNormalizer


def _fixture_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"


def _load_agent() -> tuple[GrcAgent, FlowgraphSession]:
    session = FlowgraphSession()
    session.load(_fixture_path())
    return GrcAgent(session), session


def _raw_snapshot(session: FlowgraphSession) -> tuple[int, bool, str]:
    assert session.flowgraph is not None
    return (
        session.state_revision,
        session.is_dirty,
        session._serialize_raw_data(session.flowgraph.raw_data),
    )


def _parse_fallback(content: str):
    client = LlamaServerClient("http://127.0.0.1:1")
    return client.parse_assistant_message(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": content,
                    }
                }
            ]
        },
        fallback_transaction_checker=GrcAgent.looks_like_transaction_payload,
    )


def _parse_without_fallback(content: str):
    client = LlamaServerClient("http://127.0.0.1:1")
    return client.parse_assistant_message(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": content,
                    }
                }
            ]
        },
        fallback_transaction_checker=GrcAgent.looks_like_transaction_payload,
        assistant_text_fallback_enabled=False,
    )


class AssistantTextFallbackGuardTests(unittest.TestCase):
    """Fallback parsing must never become hidden routing or direct mutation."""

    def test_fallback_parsing_only_creates_structured_tool_call_object(self) -> None:
        agent, session = _load_agent()
        before = _raw_snapshot(session)
        history_before = list(agent.history)

        content, tool_calls = _parse_fallback(
            '{"op_type": "update_params", "instance_name": "samp_rate", '
            '"params": {"value": "44100"}}'
        )

        self.assertIsNone(content)
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0].name, "apply_edit")
        self.assertEqual(
            tool_calls[0].arguments,
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "44100"},
                }
            },
        )
        self.assertEqual(_raw_snapshot(session), before)
        self.assertEqual(agent.history, history_before)

    def test_mvp_disabled_fallback_leaves_assistant_text_unparsed(self) -> None:
        content, tool_calls = _parse_without_fallback(
            '{"op_type": "update_params", "instance_name": "samp_rate", '
            '"params": {"value": "44100"}}'
        )

        self.assertIn("update_params", content)
        self.assertEqual(tool_calls, [])

    def test_fallback_transaction_is_route_rejected_for_preview_only_turn(self) -> None:
        agent, session = _load_agent()
        before = _raw_snapshot(session)
        agent.init_turn_requirements("Preview changing samp_rate to 44100; do not apply.")
        _content, tool_calls = _parse_fallback(
            '{"op_type": "update_params", "instance_name": "samp_rate", '
            '"params": {"value": "44100"}}'
        )
        tool_call = tool_calls[0]
        args = agent.normalize_tool_call_arguments(tool_call.name, tool_call.arguments)

        route_result = agent.validate_turn_route(tool_call.name, args)

        self.assertIsNotNone(route_result)
        assert route_result is not None
        self.assertFalse(route_result["ok"])
        self.assertEqual(route_result["error_type"], "route_mismatch")
        self.assertEqual(_raw_snapshot(session), before)

    def test_fallback_extra_top_level_fields_are_schema_rejected(self) -> None:
        agent, session = _load_agent()
        before = _raw_snapshot(session)
        agent.init_turn_requirements("Set samp_rate to 44100.")
        _content, tool_calls = _parse_fallback(
            'apply_edit(transaction={"op_type": "update_params", '
            '"instance_name": "samp_rate", "params": {"value": "44100"}}, '
            'repair_plan="also rewrite the graph")'
        )
        tool_call = tool_calls[0]
        args = agent.normalize_tool_call_arguments(tool_call.name, tool_call.arguments)

        route_result = agent.validate_turn_route(tool_call.name, args)
        validation_result = agent.validate_tool_call(tool_call.name, args)

        self.assertIsNone(route_result)
        self.assertIsNotNone(validation_result)
        assert validation_result is not None
        self.assertFalse(validation_result["ok"])
        self.assertEqual(validation_result["error_type"], "tool_call_invalid")
        self.assertEqual(_raw_snapshot(session), before)

    def test_fallback_valid_call_still_executes_through_grcagent_tool_path(self) -> None:
        agent, session = _load_agent()
        before = _raw_snapshot(session)
        agent.init_turn_requirements("Set samp_rate to 44100.")
        _content, tool_calls = _parse_fallback(
            '{"op_type": "update_params", "instance_name": "samp_rate", '
            '"params": {"value": "44100"}}'
        )
        tool_call = tool_calls[0]
        args = agent.normalize_tool_call_arguments(tool_call.name, tool_call.arguments)

        self.assertIsNone(agent.validate_turn_route(tool_call.name, args))
        self.assertIsNone(agent.validate_tool_call(tool_call.name, args))
        result = agent.execute_tool(tool_call.name, args)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool"], "apply_edit")
        self.assertNotEqual(_raw_snapshot(session), before)
        self.assertTrue(session.is_dirty)

    def test_yaml_like_assistant_text_is_not_fallback_parsed_as_tool_call(self) -> None:
        content, tool_calls = _parse_fallback(
            "blocks:\n"
            "  - name: samp_rate\n"
            "    parameters:\n"
            "      value: 44100\n"
            "connections: []"
        )

        self.assertEqual(content.strip().splitlines()[0], "blocks:")
        self.assertEqual(tool_calls, [])

    def test_unknown_tool_name_is_not_recovered_from_assistant_text(self) -> None:
        content, tool_calls = _parse_fallback(
            'raw_yaml_edit(path="/tmp/graph.grc", patch="blocks: []")'
        )

        self.assertIn("raw_yaml_edit", content)
        self.assertEqual(tool_calls, [])

    def test_unknown_tool_call_is_rejected_by_runtime_schema(self) -> None:
        agent, session = _load_agent()
        before = _raw_snapshot(session)

        result = agent.validate_tool_call("raw_yaml_edit", {"path": "graph.grc"})

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "unknown_tool")
        self.assertEqual(_raw_snapshot(session), before)

    def test_block_uid_mutation_wording_keeps_empty_tool_surface(self) -> None:
        agent, session = _load_agent()
        before = _raw_snapshot(session)

        plan = agent.init_turn_requirements("Use block_uid abc123 to disable that block.")

        self.assertEqual(plan.intent, "uncertain_mutation")
        self.assertTrue(plan.requires_clarification)
        self.assertEqual(plan.allowed_tools, ())
        self.assertEqual(_raw_snapshot(session), before)

    def test_block_uid_prose_is_not_fallback_parsed_as_mutation(self) -> None:
        content, tool_calls = _parse_fallback(
            "Use block_uid block:0123456789abcdef to disable that duplicate."
        )

        self.assertIn("block_uid", content)
        self.assertEqual(tool_calls, [])

    def test_vague_topology_repair_keeps_empty_tool_surface(self) -> None:
        agent, session = _load_agent()
        before = _raw_snapshot(session)

        plan = agent.init_turn_requirements("Fix this topology and rewire everything.")

        self.assertEqual(plan.intent, "uncertain_mutation")
        self.assertTrue(plan.requires_clarification)
        self.assertEqual(plan.allowed_tools, ())
        self.assertEqual(_raw_snapshot(session), before)


class TransactionNormalizerGuardTests(unittest.TestCase):
    """Normalizer must stay narrow, generic, and non-authoritative."""

    def test_partial_remove_connection_is_not_completed_when_ambiguous(self) -> None:
        _agent, session = _load_agent()
        normalizer = TransactionNormalizer(session)

        result = normalizer.normalize_transaction_instance_names(
            {"op_type": "remove_connection", "src_block": "blocks_throttle2_0"}
        )

        self.assertEqual(
            result,
            {"op_type": "remove_connection", "src_block": "blocks_throttle2_0"},
        )

    def test_single_connection_completion_is_exact_only(self) -> None:
        _agent, session = _load_agent()
        normalizer = TransactionNormalizer(session)

        result = normalizer.normalize_transaction_instance_names(
            {"op_type": "remove_connection", "src_block": "analog_random_source_x_0"}
        )

        self.assertEqual(
            result,
            {
                "op_type": "remove_connection",
                "src_block": "analog_random_source_x_0",
                "src_port": 0,
                "dst_block": "blocks_throttle2_0",
                "dst_port": 0,
            },
        )

    def test_normalizer_does_not_invent_tutorial_params_or_defaults(self) -> None:
        normalizer = TransactionNormalizer()

        result = normalizer.normalize_transaction_instance_names(
            {
                "op_type": "add_block",
                "block_type": "blocks_throttle2",
                "instance_name": "throttle_from_docs",
            }
        )

        self.assertEqual(
            result,
            {
                "op_type": "add_block",
                "block_type": "blocks_throttle2",
                "instance_name": "throttle_from_docs",
            },
        )
        self.assertNotIn("parameters", result)
        self.assertNotIn("params", result)

    def test_normalizer_does_not_remap_destructive_operation_to_safer_operation(self) -> None:
        normalizer = TransactionNormalizer()

        result = normalizer.normalize_transaction_instance_names(
            {
                "op_type": "remove_block",
                "instance_name": "blocks_throttle2_0",
                "params": {"value": "do not convert me"},
            }
        )

        self.assertEqual(result["op_type"], "remove_block")
        self.assertEqual(result["instance_name"], "blocks_throttle2_0")
        self.assertEqual(result["params"], {"value": "do not convert me"})

    def test_block_uid_never_becomes_instance_name(self) -> None:
        normalizer = TransactionNormalizer()

        result = normalizer.normalize_transaction_instance_names(
            {
                "op_type": "update_params",
                "block_uid": "block:not-a-mutation-handle",
                "params": {"value": "1"},
            }
        )

        self.assertNotIn("instance_name", result)
        self.assertEqual(result["block_uid"], "block:not-a-mutation-handle")

    def test_normalizer_does_not_synthesize_uid_target_ref(self) -> None:
        normalizer = TransactionNormalizer()

        result = normalizer.normalize_transaction_instance_names(
            {
                "op_type": "update_params",
                "instance_name": "dup",
                "block_uid": "block:0123456789abcdef",
                "params": {"value": "1"},
            }
        )

        self.assertEqual(result["instance_name"], "dup")
        self.assertEqual(result["block_uid"], "block:0123456789abcdef")
        self.assertNotIn("target_ref", result)

    def test_route_mismatch_is_not_normalized_into_allowed_operation(self) -> None:
        agent, session = _load_agent()
        before = _raw_snapshot(session)
        agent.init_turn_requirements("Set samp_rate to 44100.")
        transaction = agent.normalize_tool_call_arguments(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "remove_block please",
                    "instance_name": "blocks_throttle2_0",
                }
            },
        )["transaction"]

        route_result = agent.validate_turn_route("apply_edit", {"transaction": transaction})

        self.assertEqual(transaction["op_type"], "remove_block")
        self.assertIsNotNone(route_result)
        assert route_result is not None
        self.assertFalse(route_result["ok"])
        self.assertEqual(route_result["error_type"], "route_mismatch")
        self.assertEqual(_raw_snapshot(session), before)

    def test_normalizer_does_not_repair_gnu_invalid_duplicate_connection(self) -> None:
        agent, session = _load_agent()
        before = _raw_snapshot(session)
        agent.init_turn_requirements("Connect analog_random_source_x_0:0 to blocks_throttle2_0:0.")

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "add_connection",
                    "src_block": "analog_random_source_x_0",
                    "src_port": 0,
                    "dst_block": "blocks_throttle2_0",
                    "dst_port": 0,
                }
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(_raw_snapshot(session), before)


if __name__ == "__main__":
    unittest.main()
