"""Regression guards for architecture-audit maintenance watch items."""

from __future__ import annotations

import unittest
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
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
        session._serialize_raw_data(session.flowgraph.export_data()),
    )


class RuntimeRefactorGuardTests(unittest.TestCase):
    """Deleted adapter symbols and text recovery paths must stay absent."""

    def test_old_llama_adapter_symbols_are_absent_from_repo_code(self) -> None:
        root = Path(__file__).resolve().parents[1]
        needles = (
            "LlamaServerClient",
            "LlamaToolCall",
            "run_bounded_llama_turn",
        )
        offenders: list[str] = []
        for path in [*root.glob("src/**/*.py"), *root.glob("scripts/**/*.py")]:
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                if needle in text:
                    offenders.append(f"{path.relative_to(root)}:{needle}")
        self.assertEqual(offenders, [])

    def test_unknown_tool_call_is_rejected_by_runtime_schema(self) -> None:
        agent, session = _load_agent()
        before = _raw_snapshot(session)

        result = agent.validate_tool_call("raw_yaml_edit", {"path": "graph.grc"})

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "unknown_tool")
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

    def test_route_validation_does_not_mutate_or_normalize_operation(self) -> None:
        agent, session = _load_agent()
        before = _raw_snapshot(session)
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
        self.assertEqual(route_result.get("error_type"), "tool_not_allowed_for_surface")
        self.assertEqual(_raw_snapshot(session), before)

    def test_normalizer_does_not_repair_gnu_invalid_duplicate_connection(self) -> None:
        agent, session = _load_agent()
        before = _raw_snapshot(session)

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
