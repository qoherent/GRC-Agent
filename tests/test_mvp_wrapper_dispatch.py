"""Dispatch correctness and efficiency tests for MVP wrapper tool surface."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest import mock

from grc_agent.agent import GrcAgent
import grc_agent.agent as agent_module
from grc_agent.flowgraph_session import FlowgraphSession


class MvpWrapperDispatchTests(unittest.TestCase):
    def _fixture_path(self) -> Path:
        return Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"

    def _load_agent(self) -> GrcAgent:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return GrcAgent(session)

    def test_inspect_graph_dispatch_map_with_debug_telemetry(self) -> None:
        agent = self._load_agent()
        cases = [
            ("summarize", {"operation": "summarize", "debug": True}, "summarize_graph"),
            (
                "context",
                {"operation": "context", "target": "samp_rate", "debug": True},
                "get_grc_context",
            ),
            ("validate", {"operation": "validate", "debug": True}, "validate_graph"),
            ("list_blocks", {"operation": "list_blocks", "debug": True}, "session_snapshot.list_blocks"),
            (
                "list_connections",
                {"operation": "list_connections", "debug": True},
                "session_snapshot.list_connections",
            ),
            (
                "list_variables",
                {"operation": "list_variables", "debug": True},
                "session_snapshot.list_variables",
            ),
        ]
        for operation, payload, expected_handler in cases:
            with self.subTest(operation=operation):
                before_revision = agent.session.state_revision
                before_dirty = agent.session.is_dirty
                result = agent.execute_tool("inspect_graph", payload)
                self.assertTrue(result["ok"], result)
                telemetry = result.get("dispatch_telemetry")
                self.assertIsInstance(telemetry, dict)
                self.assertEqual(telemetry["wrapper_name"], "inspect_graph")
                self.assertEqual(telemetry["wrapper_action"], operation)
                self.assertIn(expected_handler, telemetry["internal_handler_called"])
                self.assertFalse(telemetry["graph_mutated"])
                self.assertEqual(agent.session.state_revision, before_revision)
                self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_search_blocks_exact_block_id_ranks_first(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "search_blocks",
            {"query": "blocks_throttle2", "k": 5},
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result.get("retrieval_mode"), "exact")
        self.assertGreaterEqual(len(result["results"]), 1)
        first = result["results"][0]
        self.assertEqual(first["block_id"], "blocks_throttle2")

    def test_search_blocks_exact_block_id_skips_semantic_call(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "blocks_throttle2",
            "scope": "catalog",
            "results": [
                {
                    "block_id": "blocks_throttle2",
                    "label": "Throttle",
                    "summary": "Throttle samples per second.",
                    "node_id": "catalog:block:blocks_throttle2",
                }
            ],
        }
        with (
            mock.patch.object(agent_module, "_search_grc_with_context", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc") as semantic_mock,
        ):
            result = agent.execute_tool("search_blocks", {"query": "blocks_throttle2", "k": 3})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result.get("retrieval_mode"), "exact")
        self.assertEqual(semantic_mock.call_count, 0)
        self.assertEqual(result["results"][0]["block_id"], "blocks_throttle2")

    def test_search_blocks_exact_canonical_name_skips_semantic_call(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "Char to Float",
            "scope": "catalog",
            "results": [
                {
                    "block_id": "blocks_char_to_float",
                    "label": "Char To Float",
                    "summary": "Convert char stream to float stream.",
                    "node_id": "catalog:block:blocks_char_to_float",
                }
            ],
        }
        with (
            mock.patch.object(agent_module, "_search_grc_with_context", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc") as semantic_mock,
        ):
            result = agent.execute_tool("search_blocks", {"query": "Char To Float", "k": 3})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result.get("retrieval_mode"), "exact")
        self.assertEqual(semantic_mock.call_count, 0)
        self.assertEqual(result["results"][0]["block_id"], "blocks_char_to_float")

    def test_search_blocks_exact_alias_skips_semantic_call(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "catalog:block:blocks_throttle2",
            "scope": "catalog",
            "results": [
                {
                    "block_id": "blocks_throttle2",
                    "label": "Throttle",
                    "summary": "Throttle samples per second.",
                    "node_id": "catalog:block:blocks_throttle2",
                }
            ],
        }
        with (
            mock.patch.object(agent_module, "_search_grc_with_context", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc") as semantic_mock,
        ):
            result = agent.execute_tool(
                "search_blocks",
                {"query": "catalog:block:blocks_throttle2", "k": 3},
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result.get("retrieval_mode"), "exact")
        self.assertEqual(semantic_mock.call_count, 0)
        self.assertEqual(result["results"][0]["block_id"], "blocks_throttle2")

    def test_search_blocks_missing_vector_index_falls_back_safely(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "throttle",
            "scope": "catalog",
            "results": [
                {
                    "block_id": "blocks_throttle2",
                    "label": "Throttle",
                    "summary": "Throttle samples per second.",
                    "node_id": "catalog:blocks_throttle2",
                }
            ],
        }
        semantic_payload = {
            "ok": False,
            "error_type": "missing_index",
            "message": "Vector index missing.",
        }
        with (
            mock.patch.object(agent_module, "_search_grc_with_context", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc", return_value=semantic_payload),
        ):
            result = agent.execute_tool("search_blocks", {"query": "limit sample rate", "k": 3})
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["degraded_retrieval"])
        self.assertEqual(result.get("retrieval_mode"), "lexical_fallback")
        self.assertEqual(result["results"][0]["block_id"], "blocks_throttle2")

    def test_search_blocks_output_stays_minimal_by_default(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool("search_blocks", {"query": "throttle"})
        self.assertTrue(result["ok"], result)
        self.assertLessEqual(len(result["results"]), 5)
        for row in result["results"]:
            self.assertEqual(sorted(row.keys()), ["block_id", "name", "summary"])
            self.assertNotIn("ports", row)
            self.assertNotIn("params", row)
            self.assertNotIn("insert_tool_args", row)
        self.assertLess(len(str(result)), 2200)

    def test_search_blocks_concept_output_is_compact_where_practical(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool(
            "search_blocks",
            {"query": "limit sample rate of stream", "k": 5},
        )
        self.assertTrue(result["ok"], result)
        self.assertLessEqual(len(result["results"]), 5)
        self.assertLess(len(str(result)), 2300)

    def test_search_blocks_repeated_concept_query_hits_cache(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "limit sample rate",
            "scope": "catalog",
            "results": [
                {
                    "block_id": "blocks_throttle2",
                    "label": "Throttle",
                    "summary": "Limit throughput by sample rate in software flowgraphs.",
                    "node_id": "catalog:block:blocks_throttle2",
                }
            ],
        }
        semantic_payload = {
            "ok": True,
            "results": [
                {
                    "canonical_block_id": "blocks_throttle2",
                    "title": "Throttle",
                    "excerpt": "Limit throughput by sample rate in software flowgraphs.",
                    "record_id": "rec-throttle",
                    "vector_score_raw": 0.91,
                }
            ],
        }
        with (
            mock.patch.object(agent, "_search_blocks_version_token", return_value="v1"),
            mock.patch.object(agent_module, "_search_grc_with_context", return_value=lexical_payload) as lexical_mock,
            mock.patch.object(agent_module, "semantic_search_grc", return_value=semantic_payload) as semantic_mock,
        ):
            first = agent.execute_tool("search_blocks", {"query": "limit sample rate", "k": 3})
            second = agent.execute_tool("search_blocks", {"query": "limit sample rate", "k": 3})
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(first["results"], second["results"])
        self.assertEqual(lexical_mock.call_count, 1)
        self.assertEqual(semantic_mock.call_count, 1)

    def test_search_blocks_cache_miss_when_k_or_query_changes(self) -> None:
        agent = self._load_agent()
        lexical_payload = {"ok": True, "results": []}
        semantic_payload = {"ok": True, "results": []}
        with (
            mock.patch.object(agent, "_search_blocks_version_token", return_value="v1"),
            mock.patch.object(agent_module, "_search_grc_with_context", return_value=lexical_payload) as lexical_mock,
            mock.patch.object(agent_module, "semantic_search_grc", return_value=semantic_payload) as semantic_mock,
        ):
            agent.execute_tool("search_blocks", {"query": "limit sample rate", "k": 3})
            agent.execute_tool("search_blocks", {"query": "limit sample rate", "k": 4})
            agent.execute_tool("search_blocks", {"query": "limit throughput", "k": 3})
        self.assertEqual(lexical_mock.call_count, 3)
        self.assertEqual(semantic_mock.call_count, 3)

    def test_search_blocks_cache_invalidates_on_version_change(self) -> None:
        agent = self._load_agent()
        lexical_payload = {"ok": True, "results": []}
        semantic_payload = {"ok": True, "results": []}
        with (
            mock.patch.object(
                agent,
                "_search_blocks_version_token",
                side_effect=["v1", "v2"],
            ),
            mock.patch.object(agent_module, "_search_grc_with_context", return_value=lexical_payload) as lexical_mock,
            mock.patch.object(agent_module, "semantic_search_grc", return_value=semantic_payload) as semantic_mock,
        ):
            agent.execute_tool("search_blocks", {"query": "limit sample rate", "k": 3})
            agent.execute_tool("search_blocks", {"query": "limit sample rate", "k": 3})
        self.assertEqual(lexical_mock.call_count, 2)
        self.assertEqual(semantic_mock.call_count, 2)

    def test_search_help_returns_explanation_only_payload(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool("search_help", {"query": "stream tags"})
        self.assertTrue(result["ok"], result)
        self.assertLessEqual(len(result["results"]), 3)
        for row in result["results"]:
            self.assertEqual(sorted(row.keys()), ["excerpt", "source", "title"])
            self.assertNotIn("block_id", row)
            self.assertNotIn("transaction", row)
            self.assertNotIn("params", row)

    def test_search_blocks_concept_query_uses_single_lexical_and_semantic_call(self) -> None:
        agent = self._load_agent()
        lexical_payload = {"ok": True, "results": []}
        semantic_payload = {"ok": True, "results": []}
        with (
            mock.patch.object(agent_module, "_search_grc_with_context", return_value=lexical_payload) as lexical_mock,
            mock.patch.object(agent_module, "semantic_search_grc", return_value=semantic_payload) as semantic_mock,
        ):
            result = agent.execute_tool("search_blocks", {"query": "limit sample rate", "k": 4})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result.get("retrieval_mode"), "hybrid")
        self.assertEqual(lexical_mock.call_count, 1)
        self.assertEqual(semantic_mock.call_count, 1)

    def test_change_graph_preview_routes_to_propose_only(self) -> None:
        agent = self._load_agent()
        with (
            mock.patch.object(agent, "_propose_edit", wraps=agent._propose_edit) as propose_mock,
            mock.patch.object(agent, "_apply_edit", wraps=agent._apply_edit) as apply_mock,
            mock.patch.object(agent, "_remove_connection", wraps=agent._remove_connection) as remove_mock,
            mock.patch.object(agent, "_rewire_connection", wraps=agent._rewire_connection) as rewire_mock,
        ):
            result = agent.execute_tool(
                "change_graph",
                {
                    "dry_run": True,
                    "user_goal": "Preview setting samp_rate to 48000.",
                    "instance_name": "samp_rate",
                    "param_key": "value",
                    "param_value": "48000",
                    "debug": True,
                },
            )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["dry_run"])
        telemetry = result.get("dispatch_telemetry") or {}
        self.assertFalse(telemetry.get("graph_mutated"))
        self.assertFalse(telemetry.get("checkpoint_created"))
        self.assertEqual(propose_mock.call_count, 1)
        self.assertEqual(apply_mock.call_count, 0)
        self.assertEqual(remove_mock.call_count, 0)
        self.assertEqual(rewire_mock.call_count, 0)

    def test_change_graph_exact_disconnect_routes_to_remove_handler(self) -> None:
        agent = self._load_agent()
        listed = agent.execute_tool("inspect_graph", {"operation": "list_connections"})
        self.assertTrue(listed["ok"], listed)
        connection_id = listed["items"][0]
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        with (
            mock.patch.object(agent, "_remove_connection", wraps=agent._remove_connection) as remove_mock,
            mock.patch.object(agent, "_rewire_connection", wraps=agent._rewire_connection) as rewire_mock,
            mock.patch.object(agent, "_insert_block_on_connection", wraps=agent._insert_block_on_connection) as insert_mock,
        ):
            result = agent.execute_tool(
                "change_graph",
                {
                    "dry_run": False,
                    "user_goal": "Remove exact connection.",
                    "connection_id": connection_id,
                    "debug": True,
                },
            )
        self.assertEqual(result["operation_summary"], "remove_connection")
        self.assertEqual(remove_mock.call_count, 1)
        self.assertEqual(rewire_mock.call_count, 0)
        self.assertEqual(insert_mock.call_count, 0)
        if not result["ok"]:
            self.assertEqual(agent.session.state_revision, before_revision)
            self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_exact_rewire_routes_to_rewire_handler(self) -> None:
        agent = self._load_agent()
        listed = agent.execute_tool("inspect_graph", {"operation": "list_connections"})
        self.assertTrue(listed["ok"], listed)
        connection_id = listed["items"][0]
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        with (
            mock.patch.object(agent, "_rewire_connection", wraps=agent._rewire_connection) as rewire_mock,
            mock.patch.object(agent, "_remove_connection", wraps=agent._remove_connection) as remove_mock,
        ):
            result = agent.execute_tool(
                "change_graph",
                {
                    "dry_run": False,
                    "user_goal": "Rewire exact connection.",
                    "connection_id": connection_id,
                    "new_src_block": "analog_random_source_x_0",
                    "new_src_port": 0,
                    "new_dst_block": "blocks_char_to_float_0",
                    "new_dst_port": 0,
                    "debug": True,
                },
            )
        self.assertEqual(result["operation_summary"], "rewire_connection")
        self.assertEqual(rewire_mock.call_count, 1)
        self.assertEqual(remove_mock.call_count, 0)
        if not result["ok"]:
            self.assertEqual(agent.session.state_revision, before_revision)
            self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_exact_insert_routes_to_insert_handler(self) -> None:
        agent = self._load_agent()
        listed = agent.execute_tool("inspect_graph", {"operation": "list_connections"})
        self.assertTrue(listed["ok"], listed)
        connection_id = listed["items"][0]
        before_revision = agent.session.state_revision
        before_dirty = agent.session.is_dirty
        with (
            mock.patch.object(
                agent,
                "_insert_block_on_connection",
                wraps=agent._insert_block_on_connection,
            ) as insert_mock,
            mock.patch.object(agent, "_remove_connection", wraps=agent._remove_connection) as remove_mock,
        ):
            result = agent.execute_tool(
                "change_graph",
                {
                    "dry_run": False,
                    "user_goal": "Insert a throttle on the exact connection.",
                    "connection_id": connection_id,
                    "block_id": "blocks_throttle2",
                    "instance_name": "blocks_throttle2_tmp",
                    "debug": True,
                },
            )
        self.assertEqual(result["operation_summary"], "insert_block_on_connection")
        self.assertEqual(insert_mock.call_count, 1)
        self.assertEqual(remove_mock.call_count, 0)
        if not result["ok"]:
            self.assertEqual(agent.session.state_revision, before_revision)
            self.assertEqual(agent.session.is_dirty, before_dirty)

    def test_change_graph_compatible_insertion_dispatches_by_dry_run(self) -> None:
        agent = self._load_agent()
        listed = agent.execute_tool("inspect_graph", {"operation": "list_connections"})
        self.assertTrue(listed["ok"], listed)
        connection_id = listed["items"][0]
        with (
            mock.patch.object(
                agent,
                "_suggest_compatible_insertions",
                wraps=agent._suggest_compatible_insertions,
            ) as suggest_mock,
            mock.patch.object(agent, "_auto_insert_block", wraps=agent._auto_insert_block) as auto_mock,
        ):
            dry = agent.execute_tool(
                "change_graph",
                {
                    "dry_run": True,
                    "user_goal": "Insert a compatible block on this connection.",
                    "connection_id": connection_id,
                    "debug": True,
                },
            )
            live = agent.execute_tool(
                "change_graph",
                {
                    "dry_run": False,
                    "user_goal": "Insert a compatible block on this connection.",
                    "connection_id": connection_id,
                    "debug": True,
                },
            )
        self.assertTrue(dry["ok"], dry)
        self.assertEqual(dry["operation_summary"], "auto_insert_block")
        self.assertGreaterEqual(suggest_mock.call_count, 1)
        self.assertGreaterEqual(auto_mock.call_count, 1)
        self.assertIn("operation_summary", live)

    def test_change_graph_ambiguous_and_unsupported_paths(self) -> None:
        agent = self._load_agent()
        clarify = agent.execute_tool(
            "change_graph",
            {"dry_run": False, "user_goal": "Fix this graph.", "debug": True},
        )
        unsupported = agent.execute_tool(
            "change_graph",
            {"dry_run": False, "user_goal": "Edit raw .grc YAML source text.", "debug": True},
        )
        self.assertFalse(clarify["ok"], clarify)
        self.assertEqual(clarify["error_type"], "clarification_required")
        self.assertFalse(unsupported["ok"], unsupported)
        self.assertEqual(unsupported["error_type"], "unsupported_op")
        self.assertTrue((clarify.get("dispatch_telemetry") or {}).get("clarification_returned"))

    def test_change_graph_committed_checkpoint_only_on_successful_mutation(self) -> None:
        agent = self._load_agent()
        preview = agent.execute_tool(
            "change_graph",
            {
                "dry_run": True,
                "user_goal": "Preview setting samp_rate to 48000.",
                "instance_name": "samp_rate",
                "param_key": "value",
                "param_value": "48000",
                "debug": True,
            },
        )
        commit = agent.execute_tool(
            "change_graph",
            {
                "dry_run": False,
                "user_goal": "Set samp_rate to 48000.",
                "instance_name": "samp_rate",
                "param_key": "value",
                "param_value": "48000",
                "debug": True,
            },
        )
        self.assertTrue(preview["ok"], preview)
        self.assertFalse((preview.get("dispatch_telemetry") or {}).get("checkpoint_created"))
        self.assertTrue(commit["ok"], commit)
        self.assertTrue(commit.get("checkpoint_id"))
        self.assertTrue((commit.get("dispatch_telemetry") or {}).get("checkpoint_created"))


if __name__ == "__main__":
    unittest.main()
