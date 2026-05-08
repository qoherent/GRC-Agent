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
        agent = GrcAgent(session)
        object.__setattr__(agent._docs_answer_cfg, "helper_mode", "always")
        return agent

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
        self.assertEqual(result.get("retrieval_mode"), "lexical_fallback_missing_vector")
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

    def test_ask_grc_docs_returns_explanation_only_payload(self) -> None:
        agent = self._load_agent()
        result = agent.execute_tool("ask_grc_docs", {"question": "What are stream tags?"})
        self.assertTrue(result["ok"], result)
        self.assertLessEqual(len(result["sources"]), 3)
        for row in result["sources"]:
            self.assertEqual(sorted(row.keys()), ["excerpt", "source", "title"])
            self.assertNotIn("block_id", row)
            self.assertNotIn("transaction", row)
        self.assertNotIn("params", row)
        self.assertNotIn("insert_tool_args", result)
        self.assertIn(result.get("retrieval_mode"), {
            "lexical_only",
            "lexical_plus_manual_semantic",
            "lexical_plus_tutorial_semantic",
            "lexical_plus_manual_and_tutorial_semantic",
            "lexical_fallback_missing_vector",
        })

    def test_ask_grc_docs_strong_lexical_can_skip_semantic(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "difference between stream and message ports",
            "results": [
                {
                    "title": "Message Passing",
                    "excerpt": "Messages are an asynchronous way to send control data between blocks.",
                    "score": 32.5,
                    "citation": {
                        "path": "docs/wiki_gnuradio_org/Message_Passing.md",
                        "url": "https://wiki.gnuradio.org/index.php/Message_Passing",
                    },
                },
                {
                    "title": "Streams and Vectors",
                    "excerpt": "Stream ports carry regularly-sampled stream data between blocks.",
                    "score": 31.8,
                    "citation": {
                        "path": "docs/wiki_gnuradio_org/Streams_and_Vectors.md",
                        "url": "https://wiki.gnuradio.org/index.php/Streams_and_Vectors",
                    },
                }
            ],
        }
        helper_payload = {
            "answer": "stream ports: stream ports carry sampled stream data between blocks. message ports: messages carry asynchronous control data between blocks. Difference: one carries sample streams while the other carries asynchronous messages.",
            "source_indexes": [0, 1],
            "insufficient_evidence": False,
        }

        def _run_helper(**_: object) -> dict[str, object]:
            agent._last_docs_advisor_meta = {
                "advisor_attempted": True,
                "advisor_success": True,
                "fallback_reason": "none",
                "helper_latency_ms": 123,
                "prompt_chars": 500,
                "snippet_count": 1,
                "schema_valid": True,
                "timeout_ms": 2000,
                "cache_hit": False,
                "helper_finish_reason": "stop",
            }
            return helper_payload

        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc") as semantic_mock,
            mock.patch.object(
                agent,
                "_helper_eligibility_for_docs_answer",
                return_value=(True, "eligible_test_override"),
            ),
            mock.patch.object(agent, "_run_docs_answer_advisor", side_effect=_run_helper),
        ):
            result = agent.execute_tool(
                "ask_grc_docs",
                {"question": "Difference between stream and message ports?", "debug": True},
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result.get("retrieval_mode"), "lexical_only")
        self.assertEqual(semantic_mock.call_count, 0)
        self.assertFalse(result.get("fallback_used"))
        telemetry = result.get("docs_answer_telemetry") or {}
        self.assertTrue(telemetry.get("advisor_success"))
        self.assertEqual(telemetry.get("fallback_reason"), "none")

    def test_ask_grc_docs_missing_vector_index_falls_back_without_auto_build(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "stream tags",
            "results": [
                {
                    "title": "Tutorials",
                    "excerpt": "Please leave tutorials-related feedback. Beginner Tutorials 1. What is GNU Radio?",
                    "score": 7.0,
                    "citation": {
                        "path": "docs/wiki_gnuradio_org/Tutorials.md",
                        "url": "https://wiki.gnuradio.org/index.php/Tutorials",
                    },
                }
            ],
        }
        missing_index = {
            "ok": False,
            "error_type": "missing_index",
            "message": "Vector index missing.",
        }
        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc", return_value=missing_index),
            mock.patch.object(agent, "_run_docs_answer_advisor", return_value=None),
        ):
            result = agent.execute_tool(
                "ask_grc_docs",
                {"question": "What are stream tags?", "debug": True},
            )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["degraded_retrieval"])
        self.assertTrue(result["fallback_used"])
        self.assertGreaterEqual(len(result["sources"]), 1)
        self.assertIn("vector_index_missing_or_not_ready", result.get("warnings", []))
        self.assertEqual(result.get("retrieval_mode"), "lexical_fallback_missing_vector")

    def test_ask_grc_docs_no_useful_result_sets_insufficient_evidence(self) -> None:
        agent = self._load_agent()
        empty = {"ok": True, "query": "x", "results": [], "warnings": ["No manual matches found for 'x'."]}
        with (
            mock.patch.object(agent_module, "search_manual", return_value=empty),
            mock.patch.object(agent_module, "semantic_search_grc", return_value=empty),
            mock.patch.object(agent, "_run_docs_answer_advisor", return_value=None),
        ):
            result = agent.execute_tool("ask_grc_docs", {"question": "obscure question"})
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["insufficient_evidence"])
        self.assertEqual(result["sources"], [])

    def test_ask_grc_docs_helper_timeout_records_fallback_reason_in_debug(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "difference between stream and message ports",
            "results": [
                {
                    "title": "Message Passing",
                    "excerpt": "Messages are an asynchronous way to send control data between blocks.",
                    "score": 30.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Message_Passing"},
                },
                {
                    "title": "Streams and Vectors",
                    "excerpt": "Stream ports carry regularly-sampled stream data between blocks.",
                    "score": 29.5,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Streams_and_Vectors"},
                }
            ],
        }

        def _run_timeout(**_: object) -> None:
            agent._last_docs_advisor_meta = {
                "advisor_attempted": True,
                "advisor_success": False,
                "fallback_reason": "timeout",
                "helper_latency_ms": None,
                "prompt_chars": 300,
                "snippet_count": 1,
                "schema_valid": False,
                "timeout_ms": 2000,
                "cache_hit": False,
                "helper_finish_reason": "timeout",
            }
            return None

        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc") as semantic_mock,
            mock.patch.object(
                agent,
                "_helper_eligibility_for_docs_answer",
                return_value=(True, "eligible_test_override"),
            ),
            mock.patch.object(agent, "_run_docs_answer_advisor", side_effect=_run_timeout),
        ):
            result = agent.execute_tool(
                "ask_grc_docs",
                {"question": "Difference between stream and message ports?", "debug": True},
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(semantic_mock.call_count, 0)
        self.assertTrue(result.get("fallback_used"))
        telemetry = result.get("docs_answer_telemetry") or {}
        self.assertEqual(telemetry.get("fallback_reason"), "timeout")

    def test_ask_grc_docs_weak_lexical_triggers_manual_semantic(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "pmt metadata wire format",
            "results": [
                {
                    "title": "Manual Index",
                    "excerpt": "General overview text.",
                    "score": 8.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Main_Page"},
                }
            ],
        }
        semantic_manual = {"ok": True, "results": []}

        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc", return_value=semantic_manual) as semantic_mock,
            mock.patch.object(agent, "_run_docs_answer_advisor", return_value=None),
        ):
            result = agent.execute_tool("ask_grc_docs", {"question": "PMT metadata wire format", "debug": True})
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(semantic_mock.call_count, 1)
        self.assertIn(result.get("retrieval_mode"), {
            "lexical_plus_manual_semantic",
            "lexical_plus_manual_and_tutorial_semantic",
            "lexical_fallback_missing_vector",
        })

    def test_ask_grc_docs_howto_query_can_include_tutorial_semantic(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "how to use qt gui sink",
            "results": [
                {
                    "title": "What Is GNU Radio",
                    "excerpt": "Introductory overview.",
                    "score": 6.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/What_Is_GNU_Radio"},
                }
            ],
        }
        semantic_payloads = [
            {"ok": True, "results": []},
            {"ok": True, "results": []},
            {"ok": True, "results": []},
        ]
        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc", side_effect=semantic_payloads) as semantic_mock,
            mock.patch.object(agent, "_run_docs_answer_advisor", return_value=None),
        ):
            result = agent.execute_tool("ask_grc_docs", {"question": "How to use QT GUI sink tutorial?", "debug": True})
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(semantic_mock.call_count, 2)
        self.assertEqual(result.get("retrieval_mode"), "lexical_plus_manual_and_tutorial_semantic")

    def test_ask_grc_docs_fallback_chooses_relevant_sentence(self) -> None:
        agent = self._load_agent()
        object.__setattr__(agent._docs_answer_cfg, "helper_mode", "never")
        
        lexical_payload = {
            "ok": True,
            "query": "pmt",
            "results": [
                {
                    "title": "What is PMT",
                    "excerpt": "Introduction page for PMT. Please leave feedback on the discussion page. Polymorphic Types (PMTs) are an opaque data type designed to safely pass data between blocks. See more below.",
                    "score": 30.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/PMT"},
                }
            ],
        }
        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc", return_value={"ok": True, "results": []}),
        ):
            result = agent.execute_tool("ask_grc_docs", {"question": "What is PMT?", "debug": True})
        
        self.assertTrue(result["ok"])
        self.assertTrue(result["fallback_used"])
        answer = result["answer"]
        self.assertIn("Polymorphic Types (PMTs) are an opaque data type designed to safely pass data between blocks.", answer)
        self.assertNotIn("Please leave feedback", answer)
        self.assertNotIn("See more", answer)

    def test_ask_grc_docs_menu_page_is_penalized_for_stream_tags(self) -> None:
        agent = self._load_agent()
        object.__setattr__(agent._docs_answer_cfg, "helper_mode", "never")
        lexical_payload = {
            "ok": True,
            "query": "stream tags",
            "results": [
                {
                    "title": "Tutorials",
                    "section": "Tutorials",
                    "excerpt": "Please leave tutorials-related feedback. Beginner Tutorials 1. What is GNU Radio? 2. Installing GNU Radio.",
                    "score": 42.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Tutorials"},
                },
                {
                    "title": "Stream Tags",
                    "section": "Introduction",
                    "excerpt": "Stream tags are an isosynchronous data stream that runs parallel to the main data stream.",
                    "score": 18.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Stream_Tags"},
                },
            ],
        }
        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc", return_value={"ok": True, "results": []}),
        ):
            result = agent.execute_tool("ask_grc_docs", {"question": "What are stream tags?"})
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(len(result["sources"]), 1)
        self.assertEqual(result["sources"][0]["title"], "Stream Tags")

    def test_ask_grc_docs_pmt_prefers_pmt_source_over_generic_page(self) -> None:
        agent = self._load_agent()
        object.__setattr__(agent._docs_answer_cfg, "helper_mode", "never")
        lexical_payload = {
            "ok": True,
            "query": "what is pmt",
            "results": [
                {
                    "title": "What Is GNU Radio",
                    "section": "Overview",
                    "excerpt": "GNU Radio is a free software toolkit. Beginner Tutorials 1. What is GNU Radio?",
                    "score": 50.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/What_Is_GNU_Radio"},
                }
            ],
        }
        semantic_manual = {
            "ok": True,
            "results": [
                {
                    "title": "Polymorphic Types (PMTs)",
                    "excerpt": "Polymorphic Types (PMTs) are used as the carrier of data for stream tags and message passing.",
                    "source_type": "manual_chunk",
                    "vector_score_raw": 0.86,
                    "provenance": {"url": "https://wiki.gnuradio.org/index.php/Polymorphic_Types_(PMTs)"},
                }
            ],
        }
        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(
                agent_module,
                "semantic_search_grc",
                side_effect=[semantic_manual, {"ok": True, "results": []}],
            ),
        ):
            result = agent.execute_tool("ask_grc_docs", {"question": "What is PMT?"})
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(len(result["sources"]), 1)
        self.assertEqual(result["sources"][0]["title"], "Polymorphic Types (PMTs)")

    def test_ask_grc_docs_grcc_without_evidence_sets_insufficient(self) -> None:
        agent = self._load_agent()
        object.__setattr__(agent._docs_answer_cfg, "helper_mode", "never")
        lexical_payload = {
            "ok": True,
            "query": "grcc validation",
            "results": [
                {
                    "title": "What Is GNU Radio",
                    "section": "Overview",
                    "excerpt": "GNU Radio is a free toolkit for signal processing.",
                    "score": 7.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/What_Is_GNU_Radio"},
                }
            ],
        }
        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc", return_value={"ok": True, "results": []}),
        ):
            result = agent.execute_tool("ask_grc_docs", {"question": "What does grcc do?"})
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["insufficient_evidence"])
        self.assertIn("did not contain enough direct evidence", result["answer"])
        self.assertNotIn("cmake", result["answer"].lower())
        self.assertNotIn("oot", result["answer"].lower())

    def test_ask_grc_docs_fallback_avoids_tutorial_wiring_for_block_definition(self) -> None:
        agent = self._load_agent()
        object.__setattr__(agent._docs_answer_cfg, "helper_mode", "never")
        lexical_payload = {
            "ok": True,
            "query": "throttle block",
            "results": [
                {
                    "title": "Converting Data Types",
                    "section": "Converting Byte to Float",
                    "excerpt": "Add the QT GUI Time Sink and the Throttle block into the workspace and connect the blocks.",
                    "score": 30.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Converting_Data_Types"},
                }
            ],
        }
        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc", return_value={"ok": True, "results": []}),
            mock.patch.object(agent, "_search_blocks", return_value={
                "ok": True,
                "results": [
                    {
                        "block_id": "blocks_throttle2",
                        "name": "Throttle",
                        "summary": "Throttle rate-limits sample flow for simulation flowgraphs that do not have hardware pacing.",
                    }
                ],
            }),
        ):
            result = agent.execute_tool("ask_grc_docs", {"question": "What does the throttle block do?"})
        self.assertTrue(result["ok"], result)
        self.assertIn("limits the rate of samples", result["answer"].lower())
        self.assertNotIn("connect the blocks", result["answer"].lower())
        self.assertNotIn("input port(s)", result["answer"].lower())
        self.assertNotIn("parameter(s)", result["answer"].lower())

    def test_ask_grc_docs_null_sink_uses_catalog_purpose_not_counts(self) -> None:
        agent = self._load_agent()
        object.__setattr__(agent._docs_answer_cfg, "helper_mode", "never")
        with (
            mock.patch.object(agent_module, "search_manual", return_value={"ok": True, "results": []}),
            mock.patch.object(agent_module, "semantic_search_grc", return_value={"ok": True, "results": []}),
            mock.patch.object(
                agent,
                "_search_blocks",
                return_value={
                    "ok": True,
                    "results": [
                        {
                            "block_id": "blocks_null_sink",
                            "name": "Null Sink",
                            "summary": "Null Sink drop output samples discard stream data sink to nowhere Null Sink (blocks_null_sink) with 1 input port(s), 0 output port(s), and 4 parameter(s).",
                        }
                    ],
                },
            ),
        ):
            result = agent.execute_tool("ask_grc_docs", {"question": "What does null sink do?"})
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["insufficient_evidence"])
        self.assertIn("discard", result["answer"].lower())
        self.assertNotIn("input port(s)", result["answer"].lower())
        self.assertNotIn("parameter(s)", result["answer"].lower())

    def test_ask_grc_docs_head_uses_catalog_purpose_not_counts(self) -> None:
        agent = self._load_agent()
        object.__setattr__(agent._docs_answer_cfg, "helper_mode", "never")
        with (
            mock.patch.object(agent_module, "search_manual", return_value={"ok": True, "results": []}),
            mock.patch.object(agent_module, "semantic_search_grc", return_value={"ok": True, "results": []}),
            mock.patch.object(
                agent,
                "_search_blocks",
                return_value={
                    "ok": True,
                    "results": [
                        {
                            "block_id": "blocks_head",
                            "name": "Head",
                            "summary": "Head limit stream length pass fixed number of samples then stop.",
                        }
                    ],
                },
            ),
        ):
            result = agent.execute_tool("ask_grc_docs", {"question": "What does the head block do?"})
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["insufficient_evidence"])
        lower = result["answer"].lower()
        self.assertIn("fixed number of samples", lower)
        self.assertNotIn("input port(s)", lower)
        self.assertNotIn("parameter(s)", lower)

    def test_ask_grc_docs_message_ports_returns_conceptual_definition(self) -> None:
        agent = self._load_agent()
        object.__setattr__(agent._docs_answer_cfg, "helper_mode", "never")
        lexical_payload = {
            "ok": True,
            "query": "message ports",
            "results": [
                {
                    "title": "Message Passing",
                    "excerpt": "Messages are an asynchronous way to send information between blocks. The message ports are described by a name, which is in practice a PMT symbol.",
                    "score": 60.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Message_Passing"},
                }
            ],
        }
        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc", return_value={"ok": True, "results": []}),
        ):
            result = agent.execute_tool("ask_grc_docs", {"question": "What are message ports?"})
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["insufficient_evidence"])
        self.assertIn("asynchronous", result["answer"].lower())

    def test_ask_grc_docs_comparison_requires_both_sides(self) -> None:
        agent = self._load_agent()
        object.__setattr__(agent._docs_answer_cfg, "helper_mode", "never")
        lexical_payload = {
            "ok": True,
            "query": "difference between stream and message ports",
            "results": [
                {
                    "title": "Message Passing",
                    "excerpt": "Messages are an asynchronous way to send information between blocks.",
                    "score": 70.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Message_Passing"},
                }
            ],
        }
        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc", return_value={"ok": True, "results": []}),
        ):
            result = agent.execute_tool(
                "ask_grc_docs",
                {"question": "Difference between stream and message ports?"},
            )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["insufficient_evidence"])
        self.assertIn("did not contain enough direct evidence", result["answer"].lower())

    def test_ask_grc_docs_comparison_with_two_sides_returns_structured_answer(self) -> None:
        agent = self._load_agent()
        object.__setattr__(agent._docs_answer_cfg, "helper_mode", "never")
        lexical_payload = {
            "ok": True,
            "query": "difference between stream and message ports",
            "results": [
                {
                    "title": "Message Passing",
                    "excerpt": "Message ports carry asynchronous control data between blocks.",
                    "score": 72.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Message_Passing"},
                },
                {
                    "title": "Streams and Vectors",
                    "excerpt": "Stream ports carry regularly sampled stream data between blocks.",
                    "score": 70.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Streams_and_Vectors"},
                },
            ],
        }
        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc", return_value={"ok": True, "results": []}),
        ):
            result = agent.execute_tool(
                "ask_grc_docs",
                {"question": "Difference between stream and message ports?"},
            )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["insufficient_evidence"])
        lower = result["answer"].lower()
        self.assertIn("difference:", lower)
        self.assertGreaterEqual(result["answer"].count(":"), 3)
        self.assertIn("stream", lower)
        self.assertIn("message", lower)

    def test_ask_grc_docs_repeated_query_hits_answer_cache(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "difference between stream and message ports",
            "results": [
                {
                    "title": "Message Passing",
                    "excerpt": "Messages are an asynchronous way to send control data between blocks.",
                    "score": 31.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Message_Passing"},
                },
                {
                    "title": "Streams and Vectors",
                    "excerpt": "Stream ports carry regularly-sampled stream data between blocks.",
                    "score": 30.8,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Streams_and_Vectors"},
                }
            ],
        }
        helper_payload = {
            "answer": "stream ports: stream ports carry sampled stream data between blocks. message ports: messages carry asynchronous control data between blocks. Difference: one carries sample streams while the other carries asynchronous messages.",
            "source_indexes": [0, 1],
            "insufficient_evidence": False,
        }
        with (
            mock.patch.object(agent_module, "_manual_corpus_version_token", return_value="manual-v1"),
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc") as semantic_mock,
            mock.patch.object(
                agent,
                "_helper_eligibility_for_docs_answer",
                return_value=(True, "eligible_test_override"),
            ),
            mock.patch.object(agent, "_run_docs_answer_advisor", return_value=helper_payload) as helper_mock,
        ):
            first = agent.execute_tool(
                "ask_grc_docs",
                {"question": "Difference between stream and message ports?", "debug": True},
            )
            second = agent.execute_tool(
                "ask_grc_docs",
                {"question": "Difference between stream and message ports?", "debug": True},
            )
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(helper_mock.call_count, 1)
        self.assertEqual(semantic_mock.call_count, 0)
        telemetry = second.get("docs_answer_telemetry") or {}
        self.assertTrue(telemetry.get("cache_hit"))
        self.assertEqual(second.get("answer"), first.get("answer"))

    def test_ask_grc_docs_cache_miss_when_query_or_k_changes(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "difference between stream and message ports",
            "results": [
                {
                    "title": "Message Passing",
                    "excerpt": "Messages are an asynchronous way to send control data between blocks.",
                    "score": 31.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Message_Passing"},
                },
                {
                    "title": "Streams and Vectors",
                    "excerpt": "Stream ports carry regularly-sampled stream data between blocks.",
                    "score": 30.8,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Streams_and_Vectors"},
                }
            ],
        }
        helper_payload = {
            "answer": "stream ports: stream ports carry sampled stream data between blocks. message ports: messages carry asynchronous control data between blocks. Difference: one carries sample streams while the other carries asynchronous messages.",
            "source_indexes": [0, 1],
            "insufficient_evidence": False,
        }
        with (
            mock.patch.object(agent_module, "_manual_corpus_version_token", return_value="manual-v1"),
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc") as semantic_mock,
            mock.patch.object(
                agent,
                "_helper_eligibility_for_docs_answer",
                return_value=(True, "eligible_test_override"),
            ),
            mock.patch.object(agent, "_run_docs_answer_advisor", return_value=helper_payload) as helper_mock,
        ):
            agent.execute_tool(
                "ask_grc_docs",
                {"question": "Difference between stream and message ports?", "k": 3},
            )
            agent.execute_tool(
                "ask_grc_docs",
                {"question": "Difference between stream and message ports?", "k": 1},
            )
            agent.execute_tool("ask_grc_docs", {"question": "What are PMTs?", "k": 3})
        self.assertEqual(helper_mock.call_count, 3)
        self.assertEqual(semantic_mock.call_count, 2)

    def test_ask_grc_docs_cache_invalidates_on_corpus_version_change(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "difference between stream and message ports",
            "results": [
                {
                    "title": "Message Passing",
                    "excerpt": "Messages are an asynchronous way to send control data between blocks.",
                    "score": 31.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Message_Passing"},
                },
                {
                    "title": "Streams and Vectors",
                    "excerpt": "Stream ports carry regularly-sampled stream data between blocks.",
                    "score": 30.8,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Streams_and_Vectors"},
                }
            ],
        }
        helper_payload = {
            "answer": "stream ports: stream ports carry sampled stream data between blocks. message ports: messages carry asynchronous control data between blocks. Difference: one carries sample streams while the other carries asynchronous messages.",
            "source_indexes": [0, 1],
            "insufficient_evidence": False,
        }
        with (
            mock.patch.object(
                agent_module,
                "_manual_corpus_version_token",
                side_effect=["manual-v1", "manual-v2"],
            ),
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc") as semantic_mock,
            mock.patch.object(
                agent,
                "_helper_eligibility_for_docs_answer",
                return_value=(True, "eligible_test_override"),
            ),
            mock.patch.object(agent, "_run_docs_answer_advisor", return_value=helper_payload) as helper_mock,
        ):
            agent.execute_tool(
                "ask_grc_docs",
                {"question": "Difference between stream and message ports?", "k": 3},
            )
            agent.execute_tool(
                "ask_grc_docs",
                {"question": "Difference between stream and message ports?", "k": 3},
            )
        self.assertEqual(helper_mock.call_count, 2)
        self.assertEqual(semantic_mock.call_count, 0)

    def test_ask_grc_docs_weak_evidence_skips_helper_and_sets_insufficient(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "unknown internals",
            "results": [
                {
                    "title": "Tutorials",
                    "excerpt": "Beginner Tutorials 1. What is GNU Radio? Please leave tutorials-related feedback.",
                    "score": 5.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Tutorials"},
                }
            ],
        }
        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc", return_value={"ok": True, "results": []}),
            mock.patch.object(agent, "_run_docs_answer_advisor") as helper_mock,
        ):
            result = agent.execute_tool(
                "ask_grc_docs",
                {
                    "question": "What is GNU Radio scheduler internals for zero-copy lock-free graph execution?",
                    "debug": True,
                },
            )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["insufficient_evidence"])
        telemetry = result.get("docs_answer_telemetry") or {}
        self.assertEqual(telemetry.get("source_quality", {}).get("quality"), "weak")
        self.assertEqual(telemetry.get("helper_skipped_reason"), "weak_evidence")
        self.assertEqual(helper_mock.call_count, 0)

    def test_ask_grc_docs_helper_maps_source_indexes_only_from_selected_snippets(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "difference between stream and message ports",
            "results": [
                {
                    "title": "Message Passing",
                    "excerpt": "Messages are an asynchronous way to send control data between blocks.",
                    "score": 33.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Message_Passing"},
                },
                {
                    "title": "Streams and Vectors",
                    "excerpt": "Stream ports carry regularly-sampled stream data between blocks.",
                    "score": 32.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Streams_and_Vectors"},
                },
            ],
        }
        helper_payload = {
            "answer": "stream ports: sampled stream data. message ports: asynchronous control data. Difference: distinct transport.",
            "source_indexes": [1, 99],
            "insufficient_evidence": False,
        }
        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc", return_value={"ok": True, "results": []}),
            mock.patch.object(
                agent,
                "_helper_eligibility_for_docs_answer",
                return_value=(True, "eligible_test_override"),
            ),
            mock.patch.object(agent, "_run_docs_answer_advisor", return_value=helper_payload),
        ):
            result = agent.execute_tool(
                "ask_grc_docs",
                {"question": "Difference between stream and message ports?", "debug": True},
            )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["fallback_used"])
        self.assertEqual(len(result["sources"]), 1)
        self.assertIn(
            result["sources"][0]["title"],
            {"Message Passing", "Streams and Vectors"},
        )

    def test_ask_grc_docs_malformed_helper_output_falls_back_safely(self) -> None:
        agent = self._load_agent()
        lexical_payload = {
            "ok": True,
            "query": "difference between stream and message ports",
            "results": [
                {
                    "title": "Message Passing",
                    "excerpt": "Messages are an asynchronous way to send control data between blocks.",
                    "score": 33.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Message_Passing"},
                },
                {
                    "title": "Streams and Vectors",
                    "excerpt": "Stream ports carry regularly-sampled stream data between blocks.",
                    "score": 32.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Streams_and_Vectors"},
                },
            ],
        }

        def _run_malformed(**_: object) -> None:
            agent._last_docs_advisor_meta = {
                "advisor_attempted": True,
                "advisor_success": False,
                "fallback_reason": "schema_parse_failure",
                "helper_latency_ms": 25,
                "prompt_chars": 250,
                "snippet_count": 2,
                "schema_valid": False,
                "timeout_ms": 2000,
                "cache_hit": False,
                "helper_finish_reason": "error",
            }
            return None

        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc", return_value={"ok": True, "results": []}),
            mock.patch.object(
                agent,
                "_helper_eligibility_for_docs_answer",
                return_value=(True, "eligible_test_override"),
            ),
            mock.patch.object(agent, "_run_docs_answer_advisor", side_effect=_run_malformed),
        ):
            result = agent.execute_tool(
                "ask_grc_docs",
                {"question": "Difference between stream and message ports?", "debug": True},
            )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["fallback_used"])
        telemetry = result.get("docs_answer_telemetry") or {}
        self.assertEqual(telemetry.get("fallback_reason"), "schema_parse_failure")

    def test_ask_grc_docs_block_definition_skips_helper_when_catalog_answer_is_sufficient(self) -> None:
        agent = self._load_agent()
        object.__setattr__(agent._docs_answer_cfg, "helper_mode", "always")
        lexical_payload = {
            "ok": True,
            "query": "what does throttle block do",
            "results": [
                {
                    "title": "Throttle",
                    "excerpt": "Throttle limits sample processing speed in flowgraphs without hardware pacing.",
                    "score": 34.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Throttle"},
                },
                {
                    "title": "Sample Rate Tutorial",
                    "excerpt": "Throttle slows processing and does not alter sample values.",
                    "score": 30.0,
                    "citation": {"url": "https://wiki.gnuradio.org/index.php/Sample_Rate_Tutorial"},
                },
            ],
        }
        with (
            mock.patch.object(agent_module, "search_manual", return_value=lexical_payload),
            mock.patch.object(agent_module, "semantic_search_grc", return_value={"ok": True, "results": []}),
            mock.patch.object(
                agent,
                "_search_blocks",
                return_value={
                    "ok": True,
                    "results": [
                        {
                            "block_id": "blocks_throttle",
                            "name": "Throttle",
                            "summary": "Throttle limits sample processing rate for simulation flowgraphs.",
                        }
                    ],
                },
            ),
            mock.patch.object(agent, "_run_docs_answer_advisor") as helper_mock,
        ):
            result = agent.execute_tool(
                "ask_grc_docs",
                {"question": "What does the throttle block do?", "debug": True},
            )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["insufficient_evidence"])
        self.assertIn("limits the rate of samples", result["answer"].lower())
        self.assertNotIn("input port(s)", result["answer"].lower())
        self.assertEqual(helper_mock.call_count, 0)
        telemetry = result.get("docs_answer_telemetry") or {}
        self.assertEqual(telemetry.get("helper_skipped_reason"), "concise_catalog_answer")

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
                    "operation_kind": "set_param",
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
                    "operation_kind": "disconnect",
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
                    "operation_kind": "rewire",
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
                    "operation_kind": "insert_block",
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
                    "operation_kind": "auto_insert",
                    "connection_id": connection_id,
                    "debug": True,
                },
            )
            live = agent.execute_tool(
                "change_graph",
                {
                    "dry_run": False,
                    "user_goal": "Insert a compatible block on this connection.",
                    "operation_kind": "auto_insert",
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
                "operation_kind": "set_param",
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
                "operation_kind": "set_param",
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
