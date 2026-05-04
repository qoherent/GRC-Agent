"""Runtime-contract tests for the model-facing `GrcAgent` surface."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import yaml

from grc_agent.agent import GrcAgent
from grc_agent.runtime.transaction_normalization import TransactionNormalizer
from grc_agent.runtime.tool_schemas import MVP_MODEL_TOOL_NAMES, PUBLIC_TOOL_NAMES
from grc_agent.cli import _run_fake_runtime
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.models import Connection


class GrcAgentTests(unittest.TestCase):
    """Tests for the final routed model-facing runtime contract."""

    def _fixture_path(self) -> Path:
        test_directory = Path(__file__).resolve().parent
        return test_directory / "data" / "random_bit_generator.grc"

    def _load_agent(self) -> tuple[GrcAgent, FlowgraphSession]:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return GrcAgent(session), session

    def _build_message_rewire_agent(self) -> tuple[GrcAgent, FlowgraphSession]:
        agent = GrcAgent()
        result = agent.execute_tool("new_grc", {"graph_id": "message_rewire_test"})
        self.assertTrue(result["ok"])
        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_message_strobe",
                        "instance_name": "strobe_0",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_message_debug",
                        "instance_name": "debug_0",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_message_debug",
                        "instance_name": "debug_1",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "strobe_0",
                        "src_port": "strobe",
                        "dst_block": "debug_0",
                        "dst_port": "print",
                    },
                ]
            },
        )
        self.assertTrue(result["ok"], result.get("message"))
        return agent, agent.session

    def _write_alt_fixture(self, directory: Path) -> Path:
        alt_path = directory / "random_bit_generator_alt.grc"
        alt_path.write_text(
            self._fixture_path()
            .read_text(encoding="utf-8")
            .replace("samp_rate", "fresh_clock_value"),
            encoding="utf-8",
        )
        return alt_path

    def _fixture_raw_data(self) -> dict:
        return yaml.safe_load(self._fixture_path().read_text(encoding="utf-8"))

    def _detached_variable_block(self, name: str = "dup", *, state: str = "enabled") -> dict:
        return {
            "name": name,
            "id": "variable",
            "parameters": {"comment": "", "value": "123"},
            "states": {
                "bus_sink": False,
                "bus_source": False,
                "bus_structure": None,
                "coordinate": [16, 16],
                "rotation": 0,
                "state": state,
            },
        }

    def _detached_import_block(self, name: str = "dup", *, state: str = "disabled") -> dict:
        return {
            "name": name,
            "id": "import",
            "parameters": {"alias": "", "comment": "", "imports": "import math"},
            "states": {
                "bus_sink": False,
                "bus_source": False,
                "bus_structure": None,
                "coordinate": [96, 128],
                "rotation": 0,
                "state": state,
            },
        }

    def _load_agent_from_raw(self, raw_data: dict) -> tuple[GrcAgent, FlowgraphSession]:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "graph.grc"
            path.write_text(
                yaml.safe_dump(raw_data, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            session = FlowgraphSession()
            session.load(path)
        return GrcAgent(session), session

    def _graph_identity_snapshot(self, session: FlowgraphSession) -> tuple:
        assert session.flowgraph is not None
        blocks = tuple(
            (
                block.instance_name,
                block.block_type,
                yaml.safe_dump(block.params, sort_keys=True, allow_unicode=True),
            )
            for block in session.flowgraph.blocks
        )
        connections = tuple(
            (
                connection.src_block,
                connection.src_port,
                connection.dst_block,
                connection.dst_port,
            )
            for connection in session.flowgraph.connections
        )
        return (session.state_revision, session.is_dirty, blocks, connections)

    def _block_target_ref(self, session: FlowgraphSession, *, name: str, block_type: str, index: int = 0) -> dict:
        assert session.flowgraph is not None
        matches = [
            block
            for block in session.flowgraph.blocks
            if block.instance_name == name and block.block_type == block_type
        ]
        block = matches[index]
        return {
            "block_uid": block.block_uid,
            "expected_instance_name": block.instance_name,
            "expected_block_type": block.block_type,
            "base_state_revision": session.state_revision,
        }

    def test_runtime_tool_surface_matches_phase_six_contract(self) -> None:
        agent, _session = self._load_agent()

        self.assertEqual(tuple(agent._tools), PUBLIC_TOOL_NAMES)
        self.assertNotIn("set_variable", agent._tools)
        self.assertNotIn("set_param", agent._tools)

    def test_turn_plan_narrows_disable_request_to_apply_edit_and_validate(self) -> None:
        agent, _session = self._load_agent()

        plan = agent.init_turn_requirements("Disable blocks_throttle2_0 and validate.")
        schemas = agent.get_tool_schemas_for_turn()

        self.assertEqual(plan.intent, "state_edit")
        self.assertEqual([schema["function"]["name"] for schema in schemas], [
            "apply_edit",
            "validate_graph",
        ])

    def test_turn_plan_narrows_state_edit_transaction_schema(self) -> None:
        agent, _session = self._load_agent()
        agent.init_turn_requirements("Disable blocks_throttle2_0.")

        schema = agent.get_tool_schemas_for_turn()[0]
        transaction = schema["function"]["parameters"]["properties"]["transaction"]

        self.assertEqual(transaction["properties"]["op_type"]["enum"], ["update_states"])
        self.assertEqual(transaction["properties"]["state"]["enum"], ["enabled", "disabled"])
        self.assertFalse(transaction["additionalProperties"])

    def test_turn_plan_narrows_exact_parameter_key_when_named(self) -> None:
        agent, _session = self._load_agent()
        agent.init_turn_requirements("Set blocks_throttle2_0 samples_per_second to 48000.")

        schema = next(
            schema
            for schema in agent.get_tool_schemas_for_turn()
            if schema["function"]["name"] == "apply_edit"
        )
        transaction = schema["function"]["parameters"]["properties"]["transaction"]
        params = transaction["properties"]["params"]

        self.assertEqual(transaction["properties"]["op_type"]["enum"], ["update_params"])
        self.assertEqual(
            transaction["properties"]["instance_name"]["enum"],
            ["blocks_throttle2_0"],
        )
        self.assertEqual(list(params["properties"]), ["samples_per_second"])
        self.assertFalse(params["additionalProperties"])

    def test_turn_plan_narrows_exact_preview_apply_validate_surface(self) -> None:
        agent, _session = self._load_agent()
        agent.init_turn_requirements("Preview setting samp_rate to 48000, apply it, and validate.")

        tool_names = [schema["function"]["name"] for schema in agent.get_tool_schemas_for_turn()]

        self.assertEqual(tool_names, ["propose_edit", "apply_edit", "validate_graph"])

    def test_preview_do_not_apply_does_not_expose_or_nudge_apply_edit(self) -> None:
        agent, _session = self._load_agent()
        agent.init_turn_requirements(
            "Preview changing samp_rate to 48000. Do not apply it."
        )

        tool_names = [schema["function"]["name"] for schema in agent.get_tool_schemas_for_turn()]
        self.assertEqual(tool_names, ["propose_edit"])

        route_error = agent.validate_turn_route(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "48000"},
                }
            },
        )
        self.assertIsNotNone(route_error)
        self.assertEqual(route_error["error_type"], "route_mismatch")

        agent.record_tool_completion("propose_edit", ok=True)
        self.assertEqual(agent.check_turn_continuation(), (False, ""))

    def test_turn_plan_narrows_context_node_when_target_known(self) -> None:
        agent, _session = self._load_agent()
        agent.init_turn_requirements(
            "Show me what uses the samp_rate block, then change its value to 22050."
        )

        schema = agent.get_tool_schemas_for_turn()[0]
        node_id = schema["function"]["parameters"]["properties"]["node_id"]

        self.assertEqual(schema["function"]["name"], "get_grc_context")
        self.assertEqual(node_id["enum"], ["samp_rate"])

    def test_route_policy_rejects_remove_block_for_disable_request(self) -> None:
        agent, session = self._load_agent()
        before_revision = session.state_revision
        agent.init_turn_requirements("Disable blocks_throttle2_0.")

        result = agent.validate_turn_route(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "remove_block",
                    "instance_name": "blocks_throttle2_0",
                }
            },
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "route_mismatch")
        self.assertEqual(session.state_revision, before_revision)

    def test_uncertain_mutation_requires_clarification_without_tool_surface(self) -> None:
        agent, _session = self._load_agent()

        plan = agent.init_turn_requirements("Swap the signal chain around and save it.")
        tool_names = [schema["function"]["name"] for schema in agent.get_tool_schemas_for_turn()]

        self.assertEqual(plan.intent, "uncertain_mutation")
        self.assertTrue(plan.requires_clarification)
        self.assertEqual(plan.unsupported_reason, "uncertain_mutation")
        self.assertEqual(tool_names, [])
        self.assertNotIn("apply_edit", tool_names)
        self.assertNotIn("propose_edit", tool_names)
        self.assertNotIn("save_graph", tool_names)
        self.assertNotIn("remove_connection", tool_names)

    def test_uncertain_mutation_route_gate_blocks_apply_edit_without_mutation(self) -> None:
        agent, session = self._load_agent()
        before_revision = session.state_revision
        agent.init_turn_requirements("Swap the signal chain around and save it.")

        result = agent.validate_turn_route(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "remove_block",
                    "instance_name": "blocks_throttle2_0",
                }
            },
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "route_mismatch")
        self.assertEqual(session.state_revision, before_revision)

    def test_connection_sort_key_handles_mixed_stream_and_message_ports(self) -> None:
        connections = [
            Connection("same_src", "msg", "same_dst", "out"),
            Connection("same_src", 0, "same_dst", 0),
        ]

        ordered = sorted(connections, key=FlowgraphSession._connection_sort_key)

        self.assertEqual([connection.src_port for connection in ordered], [0, "msg"])

    def test_tool_schemas_match_phase_six_surface(self) -> None:
        agent, _session = self._load_agent()

        schemas = agent.get_tool_schemas()
        schema_by_name = {schema["function"]["name"]: schema for schema in schemas}

        names = [schema["function"]["name"] for schema in schemas]
        self.assertEqual(names[: len(PUBLIC_TOOL_NAMES)], list(PUBLIC_TOOL_NAMES))
        for mvp_name in MVP_MODEL_TOOL_NAMES:
            self.assertIn(mvp_name, names)
        self.assertEqual(
            schema_by_name["load_grc"]["function"]["parameters"]["required"],
            ["file_path"],
        )
        self.assertEqual(
            schema_by_name["propose_edit"]["function"]["parameters"]["required"],
            ["transaction"],
        )
        self.assertEqual(
            schema_by_name["apply_edit"]["function"]["parameters"]["required"],
            ["transaction"],
        )
        self.assertEqual(
            schema_by_name["remove_connection"]["function"]["parameters"]["required"],
            [],
        )

    def test_exact_rewire_turn_schema_requires_exact_new_endpoints(self) -> None:
        agent, _session = self._load_agent()
        agent.init_turn_requirements(
            "Rewire connection_id blocks_throttle2_0:0->blocks_char_to_float_0:0 "
            "to analog_random_source_x_0:0->blocks_char_to_float_0:0, then validate."
        )

        schemas = agent.get_tool_schemas_for_turn()
        schema_by_name = {schema["function"]["name"]: schema for schema in schemas}

        self.assertEqual(
            schema_by_name["rewire_connection"]["function"]["parameters"]["required"],
            ["new_src_block", "new_src_port", "new_dst_block", "new_dst_port"],
        )
        properties = schema_by_name["rewire_connection"]["function"]["parameters"]["properties"]
        self.assertEqual(properties["new_src_block"]["enum"], ["analog_random_source_x_0"])
        self.assertEqual(properties["new_src_port"]["enum"], [0])
        self.assertEqual(properties["new_dst_block"]["enum"], ["blocks_char_to_float_0"])
        self.assertEqual(properties["new_dst_port"]["enum"], [0])

    def test_exact_message_rewire_turn_schema_preserves_string_ports(self) -> None:
        agent, _session = self._load_agent()
        agent.init_turn_requirements(
            "Rewire connection_id pdu_tagged_stream_to_pdu_0:pdus->qtgui_const_sink_x_0:in "
            "to pdu_tagged_stream_to_pdu_1:pdus->qtgui_const_sink_x_0:in."
        )

        schemas = agent.get_tool_schemas_for_turn()
        schema_by_name = {schema["function"]["name"]: schema for schema in schemas}
        properties = schema_by_name["rewire_connection"]["function"]["parameters"]["properties"]

        self.assertEqual(properties["new_src_block"]["enum"], ["pdu_tagged_stream_to_pdu_1"])
        self.assertEqual(properties["new_src_port"]["enum"], ["pdus"])
        self.assertEqual(properties["new_dst_block"]["enum"], ["qtgui_const_sink_x_0"])
        self.assertEqual(properties["new_dst_port"]["enum"], ["in"])

    def test_deterministic_exact_rewire_is_runtime_owned_and_validated(self) -> None:
        agent, session = self._load_agent()
        agent.init_turn_requirements(
            "Rewire connection_id blocks_throttle2_0:0->blocks_char_to_float_0:0 "
            "to new endpoint analog_random_source_x_0:0->blocks_char_to_float_0:0."
        )

        tool_call = agent.deterministic_turn_tool_call(agent._turn_user_message)

        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "rewire_connection")
        self.assertIsNone(
            agent.validate_turn_route(tool_call["name"], tool_call["arguments"])
        )
        self.assertIsNone(agent.validate_tool_call(tool_call["name"], tool_call["arguments"]))
        result = agent.execute_tool(tool_call["name"], tool_call["arguments"])
        self.assertTrue(result["ok"], result.get("message"))
        connection_ids = {
            f"{connection.src_block}:{connection.src_port}->"
            f"{connection.dst_block}:{connection.dst_port}"
            for connection in session.flowgraph.connections
        }
        self.assertNotIn("blocks_throttle2_0:0->blocks_char_to_float_0:0", connection_ids)
        self.assertIn("analog_random_source_x_0:0->blocks_char_to_float_0:0", connection_ids)

    def test_deterministic_exact_rewire_requires_rewire_turn_plan(self) -> None:
        agent, _session = self._load_agent()
        agent.init_turn_requirements(
            "Summarize blocks_throttle2_0:0->blocks_char_to_float_0:0 "
            "and analog_random_source_x_0:0->blocks_char_to_float_0:0."
        )

        self.assertIsNone(agent.deterministic_turn_tool_call(agent._turn_user_message))

    def test_system_prompt_mentions_read_and_edit_routes(self) -> None:
        agent, _session = self._load_agent()

        prompt = agent.get_system_prompt()

        self.assertIn(
            "The active session context tells you which `.grc` file is loaded",
            prompt,
        )
        self.assertIn(
            "If a tool result includes `suggested_next_tools` plus a hint that an explicit user-requested step is still pending",
            prompt,
        )
        self.assertIn(
            "Active-session previews, variable previews, block previews, and prior tool outputs are routing hints only",
            prompt,
        )
        self.assertIn(
            "`save_graph` only writes the current `.grc` file. Requests like `export as a standalone Python script`",
            prompt,
        )
        self.assertIn("After `search_grc`, block results include `block_id`", prompt)
        self.assertIn(
            "If a later follow-up asks what that found block looks like",
            prompt,
        )
        self.assertIn("ONLY use `propose_edit` when the user explicitly says", prompt)
        self.assertIn("are real edit requests, not preview requests", prompt)
        self.assertIn(
            "Only call `save_graph` after successful validation",
            prompt,
        )
        self.assertIn(
            "For vague connection edits, inspect the graph before `apply_edit`",
            prompt,
        )
        self.assertIn("prefer the `remove_connection(connection_id=...)` tool", prompt)
        self.assertIn(
            "You may emit multiple tool calls in one assistant message",
            prompt,
        )
        self.assertIn(
            "An explicit save request still requires `save_graph` even if the graph is already clean or unchanged.",
            prompt,
        )
        self.assertIn(
            "If the same user turn asks for an edit plus validation, summary, or save",
            prompt,
        )
        self.assertIn(
            "After other successful flows, return one short factual sentence.",
            prompt,
        )
        self.assertIn(
            "you MUST still call `summarize_graph` for a state question like `Is the graph dirty?`",
            prompt,
        )
        self.assertIn(
            "Do NOT use `summarize_graph` for `I want to see the spectrum`",
            prompt,
        )
        self.assertIn(
            "`Describe the variable block type.` => `describe_block(block_id=\"variable\")`",
            prompt,
        )
        self.assertIn("carrier recovery / spectrum / frequency sink", prompt)
        self.assertIn("prefer the `remove_connection(connection_id=...)` tool", prompt)
        self.assertIn("Parameter values may stay as GNU/Python expressions", prompt)
        self.assertIn("If the user explicitly names a loaded block or variable like `samp_rate`", prompt)
        self.assertIn("Supported `op_type` values: `update_params`, `update_states`", prompt)
        self.assertIn(
            "then call `suggest_compatible_insertions(connection_id)`, then use `insert_block_on_connection`",
            prompt,
        )
        self.assertNotIn("then use `apply_edit` with one suggested candidate", prompt)
        self.assertIn("use `search_manual`", prompt)
        self.assertIn("Manual excerpts are explanation-only", prompt)
        self.assertIn("Cite the returned manual source", prompt)
        self.assertNotIn("EXPERT RECIPES", prompt)
        self.assertNotIn("3D vector indexing", prompt)
        self.assertNotIn("answer directly from these expert recipes", prompt)
        self.assertIn("copy the tool summary verbatim as your final answer", prompt)

    def test_transaction_normalizer_supports_wrapped_insert_operation(self) -> None:
        normalizer = TransactionNormalizer()

        result = normalizer.normalize_transaction_instance_names(
            {
                "insert_block_on_connection": {
                    "connection_id": "src:0->dst:0",
                    "block_type": "blocks_head",
                    "instance_name": "head_0",
                    "params": {"num_items": "1024"},
                }
            }
        )

        self.assertEqual(
            result,
            {
                "op_type": "insert_block_on_connection",
                "connection_id": "src:0->dst:0",
                "block_type": "blocks_head",
                "instance_name": "head_0",
                "params": {"num_items": "1024"},
            },
        )

    def test_summarize_tool_message_to_model_is_plain_summary_text(self) -> None:
        agent, _session = self._load_agent()

        summary_result = agent.execute_tool("summarize_graph", {})
        agent.history.append(
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "summarize_graph",
                "content": summary_result,
            }
        )

        tool_message = agent.get_model_messages()[-1]

        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(tool_message["name"], "summarize_graph")
        self.assertEqual(tool_message["content"], summary_result["summary"])

    def test_apply_edit_infers_update_params_op_type_when_omitted(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "instance_name": "samp_rate",
                    "params": {"value": "44100"},
                }
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["normalized_operations"],
            [
                {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "44100"},
                }
            ],
        )

    def test_propose_edit_repairs_list_encoded_remove_connection_operation(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "propose_edit",
            {
                "transaction": [
                    {
                        "op_type": [
                            "remove_connection",
                            "src_block",
                            "analog_random_source_x_0",
                            "src_port",
                            0,
                            "dst_block",
                            "blocks_throttle2_0",
                            "dst_port",
                            0,
                        ]
                    }
                ]
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["normalized_operations"],
            [
                {
                    "op_type": "remove_connection",
                    "src_block": "analog_random_source_x_0",
                    "src_port": 0,
                    "dst_block": "blocks_throttle2_0",
                    "dst_port": 0,
                }
            ],
        )

    def test_propose_edit_ignores_unhashable_list_encoded_keys(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "propose_edit",
            {
                "transaction": [
                    {
                        "op_type": [
                            "remove_connection",
                            {"bad": "key"},
                            "ignored",
                            "src_block",
                            "analog_random_source_x_0",
                            "src_port",
                            0,
                            "dst_block",
                            "blocks_throttle2_0",
                            "dst_port",
                            0,
                        ]
                    }
                ]
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["normalized_operations"],
            [
                {
                    "op_type": "remove_connection",
                    "src_block": "analog_random_source_x_0",
                    "src_port": 0,
                    "dst_block": "blocks_throttle2_0",
                    "dst_port": 0,
                }
            ],
        )

    def test_model_messages_include_active_session_context(self) -> None:
        agent, _session = self._load_agent()

        messages = agent.get_model_messages()

        session_messages = [
            message
            for message in messages
            if message.get("role") == "system"
            and isinstance(message.get("content"), str)
            and message["content"].startswith("Active session:")
        ]
        self.assertEqual(len(session_messages), 1)
        self.assertIn(str(self._fixture_path()), session_messages[0]["content"])
        self.assertIn("blocks_throttle2_0", session_messages[0]["content"])
        self.assertIn("blocks=5", session_messages[0]["content"])
        self.assertIn("connections=3", session_messages[0]["content"])
        self.assertIn(
            "analog_random_source_x_0:0->blocks_throttle2_0:0",
            session_messages[0]["content"],
        )

    def test_session_history_messages_render_recorded_snapshot_not_live_session(
        self,
    ) -> None:
        agent, _session = self._load_agent()

        with tempfile.TemporaryDirectory() as tmpdir:
            alt_path = self._write_alt_fixture(Path(tmpdir))
            agent.execute_tool("load_grc", {"file_path": str(alt_path)})

        session_messages = [
            message["content"]
            for message in agent.get_model_messages()
            if message.get("role") == "system"
            and isinstance(message.get("content"), str)
            and message["content"].startswith(
                ("Active session:", "Switched active session:")
            )
        ]

        self.assertEqual(len(session_messages), 2)
        self.assertIn("path=" + str(self._fixture_path()), session_messages[0])
        self.assertIn("variables=[samp_rate=32000]", session_messages[0])
        self.assertIn("path=" + str(alt_path), session_messages[1])
        self.assertIn("variables=[fresh_clock_value=32000]", session_messages[1])

    def test_turn_refresh_session_message_omits_previews(self) -> None:
        agent, _session = self._load_agent()

        rendered = agent._session_history_content_as_text(
            {
                "path": "/tmp/example.grc",
                "graph_id": "grc:test",
                "state_revision": 2,
                "dirty": True,
                "validation": {"status": "valid", "returncode": 0},
                "block_count": 2,
                "connection_count": 1,
                "variable_count": 1,
                "variable_preview": ["samp_rate=48000"],
                "block_preview": ["blocks_throttle2_0 (blocks_throttle2; throttle)"],
                "connection_preview": ["src:0->dst:0"],
            },
            reason="turn_refresh",
        )

        self.assertIn("dirty=True", rendered)
        self.assertIn("blocks=2", rendered)
        self.assertIn("connections=1", rendered)
        self.assertIn("variables=1", rendered)
        self.assertNotIn("variables=[", rendered)
        self.assertNotIn("blocks=[", rendered)
        self.assertNotIn("connections_preview=[", rendered)

    def test_tool_history_compaction_keeps_active_session_counts_and_connections(self) -> None:
        agent, _session = self._load_agent()

        rendered = agent._tool_history_content_as_text(
            {
                "ok": True,
                "active_session": {
                    "path": "/tmp/example.grc",
                    "graph_id": "grc:test",
                    "state_revision": 2,
                    "dirty": False,
                    "validation": {"status": "valid", "returncode": 0},
                    "block_count": 2,
                    "connection_count": 1,
                    "variable_count": 1,
                    "variable_preview": ["samp_rate=48000"],
                    "block_preview": ["blocks_throttle2_0 (blocks_throttle2; throttle)"],
                    "connection_preview": ["src:0->dst:0"],
                },
            },
            tool_name="validate_graph",
        )

        self.assertIn('"block_count": 2', rendered)
        self.assertIn('"connection_count": 1', rendered)
        self.assertIn('"variable_count": 1', rendered)
        self.assertIn('"connection_preview": ["src:0->dst:0"]', rendered)

    def test_execute_tool_unknown_name_returns_structured_error(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool("set_variable", {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["tool"], "set_variable")
        self.assertEqual(result["error_type"], "unknown_tool")

    def test_load_grc_tool_replaces_empty_session(self) -> None:
        agent = GrcAgent()

        result = agent.execute_tool(
            "load_grc", {"file_path": str(self._fixture_path())}
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "load_grc")
        self.assertEqual(result["message"], "Graph loaded.")
        self.assertEqual(result["path"], str(self._fixture_path()))
        self.assertEqual(result["provenance"]["path"], str(self._fixture_path()))
        self.assertEqual(result["active_session"]["path"], str(self._fixture_path()))
        self.assertIsNotNone(agent.session.flowgraph)
        self.assertEqual(agent.history[-1]["role"], "session")
        self.assertEqual(
            agent.history[-1]["content"]["path"], str(self._fixture_path())
        )

    def test_search_grc_routes_session_search(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "search_grc",
            {"query": "samp_rate", "scope": "session", "k": 3},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "search_grc")
        self.assertEqual(result["scope"], "session")
        self.assertGreaterEqual(len(result["results"]), 1)
        self.assertIn("block_id", result["results"][0])
        self.assertIn("block_id", result["hint"])
        self.assertEqual(result["active_session"]["path"], str(self._fixture_path()))

    def test_search_grc_uses_explicit_runtime_context(self) -> None:
        agent, session = self._load_agent()

        with mock.patch(
            "grc_agent.agent._search_grc_with_context",
            side_effect=[
                {"ok": True, "scope": "session", "query": "samp_rate", "results": []},
                {
                    "ok": True,
                    "scope": "catalog",
                    "query": "samp_rate",
                    "results": [
                        {
                            "block_id": "blocks_throttle2",
                            "label": "Throttle",
                            "summary": "Throttle block.",
                        }
                    ],
                },
            ],
        ) as search_mock:
            result = agent.execute_tool(
                "search_grc",
                {"query": "samp_rate", "scope": "session", "k": 3},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(search_mock.call_count, 2)
        self.assertEqual(
            search_mock.call_args_list[0],
            mock.call(
                "samp_rate",
                scope="session",
                k=3,
                session=session,
                catalog_root=None,
            ),
        )
        self.assertEqual(
            search_mock.call_args_list[1],
            mock.call(
                "samp_rate",
                scope="catalog",
                k=3,
                session=session,
                catalog_root=None,
            ),
        )
        self.assertEqual(
            result["catalog_fallback_preview"][0]["block_id"],
            "blocks_throttle2",
        )

    def test_search_grc_session_miss_hint_points_to_catalog_fallback_result(self) -> None:
        agent, _session = self._load_agent()

        with mock.patch(
            "grc_agent.agent._search_grc_with_context",
            side_effect=[
                {
                    "ok": True,
                    "scope": "session",
                    "query": "carrier recovery",
                    "results": [],
                },
                {
                    "ok": True,
                    "scope": "catalog",
                    "query": "carrier recovery",
                    "results": [
                        {
                            "block_id": "digital_costas_loop_cc",
                            "label": "Costas Loop",
                            "summary": "Carrier recovery loop.",
                        }
                    ],
                },
            ],
        ):
            result = agent.execute_tool(
                "search_grc",
                {"query": "carrier recovery", "scope": "session", "k": 3},
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["results"])
        self.assertEqual(
            result["catalog_fallback_preview"][0]["block_id"],
            "digital_costas_loop_cc",
        )
        self.assertIn("describe_block", result["hint"])
        self.assertIn("digital_costas_loop_cc", result["hint"])

    def test_apply_edit_repairs_wrapped_add_block_list_payload(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": {
                            "add_block": [
                                {
                                    "instance_name": "cutoff",
                                    "block_type": "variable",
                                    "parameters": {"value": 1000},
                                }
                            ]
                        }
                    }
                ]
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["normalized_operations"],
            [
                {
                    "op_type": "add_block",
                    "block_type": "variable",
                    "instance_name": "cutoff",
                    "parameters": {"value": 1000},
                }
            ],
        )

    def test_search_tool_history_marks_preview_as_routing_only(self) -> None:
        agent, _session = self._load_agent()
        rendered = agent._tool_history_content_as_text(
            {
                "ok": True,
                "query": "scrambler",
                "scope": "catalog",
                "results": [
                    {
                        "block_id": "digital_additive_scrambler_bb",
                        "label": "Additive Scrambler",
                        "summary": "A detailed summary that should not be preserved in tool history.",
                    }
                ],
            },
            tool_name="search_grc",
        )

        self.assertIn(
            "next_step_note: search previews are routing only; for later follow-ups like `what does that block look like?`, call describe_block with the stored block_id, not get_grc_context.",
            rendered,
        )
        self.assertNotIn("A detailed summary", rendered)

    def test_execute_tool_rejects_schema_mismatches_before_execution(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "search_grc",
            {"query": "samp_rate", "scope": "session", "unexpected": True},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "tool_call_invalid")
        self.assertEqual(result["validation_errors"][0]["code"], "unexpected_argument")
        self.assertEqual(result["validation_errors"][0]["field"], "unexpected")

    def test_load_grc_rebinds_active_session_context(self) -> None:
        agent, _session = self._load_agent()

        with tempfile.TemporaryDirectory() as tmpdir:
            alt_path = self._write_alt_fixture(Path(tmpdir))

            load_result = agent.execute_tool("load_grc", {"file_path": str(alt_path)})
            alt_search = agent.execute_tool(
                "search_grc",
                {"query": "fresh_clock_value", "scope": "session", "k": 5},
            )
            stale_search = agent.execute_tool(
                "search_grc",
                {"query": "samp_rate", "scope": "session", "k": 5},
            )

        self.assertTrue(load_result["ok"])
        self.assertEqual(load_result["active_session"]["path"], str(alt_path))
        self.assertTrue(alt_search["ok"])
        self.assertTrue(alt_search["results"])
        self.assertEqual(
            alt_search["results"][0]["node_id"], "session:block:fresh_clock_value"
        )
        self.assertEqual(alt_search["active_session"]["path"], str(alt_path))
        self.assertFalse(stale_search["results"])
        self.assertIn(
            "No session matches found for 'samp_rate'.", stale_search["warnings"]
        )
        session_entries = [
            turn for turn in agent.history if turn.get("role") == "session"
        ]
        self.assertGreaterEqual(len(session_entries), 2)
        self.assertEqual(session_entries[-1]["reason"], "load_grc")
        self.assertEqual(session_entries[-1]["content"]["path"], str(alt_path))

    def test_fake_runtime_rejects_invalid_tool_calls_before_execution(self) -> None:
        agent, _session = self._load_agent()

        with redirect_stdout(StringIO()):
            agent.run_step_fake(
                "Search the current graph.",
                [
                    {
                        "tool": "search_grc",
                        "kwargs": {
                            "query": "samp_rate",
                            "scope": "session",
                            "unexpected": True,
                        },
                    }
                ],
            )

        tool_entries = [turn for turn in agent.history if turn.get("role") == "tool"]
        self.assertEqual(len(tool_entries), 1)
        self.assertFalse(tool_entries[0]["content"]["ok"])
        self.assertEqual(tool_entries[0]["content"]["error_type"], "tool_call_invalid")
        self.assertEqual(
            tool_entries[0]["content"]["validation_errors"][0]["code"],
            "unexpected_argument",
        )

    def test_execute_tool_rejects_unknown_tool_directly(self) -> None:
        """execute_tool's internal validation layer must reject unknown tools directly."""
        agent, _session = self._load_agent()

        result = agent.execute_tool("nonexistent_tool_xyz", {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "unknown_tool")
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "get_grc_context",
            {"node_id": "blocks_throttle2_0", "hops": 1, "max_nodes": 20},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "get_grc_context")
        self.assertEqual(result["node_id"], "blocks_throttle2_0")
        self.assertGreaterEqual(len(result["nodes"]), 1)

    def test_describe_block_routes_catalog_lookup(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool("describe_block", {"block_id": "analog_agc_xx"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "describe_block")
        self.assertEqual(result["block_id"], "analog_agc_xx")
        self.assertIn("parameters", result)

    def test_describe_block_normalizes_catalog_prefixed_id(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "describe_block", {"block_id": "catalog:block:analog_agc_xx"}
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["block_id"], "analog_agc_xx")
        self.assertEqual(result["requested_block_id"], "catalog:block:analog_agc_xx")
        self.assertEqual(result["resolved_block_id"], "analog_agc_xx")

    def test_describe_block_normalizes_session_block_instance_name(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "describe_block", {"block_id": "session:block:blocks_throttle2_0"}
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["block_id"], "blocks_throttle2")
        self.assertEqual(
            result["requested_block_id"], "session:block:blocks_throttle2_0"
        )
        self.assertEqual(result["resolved_block_id"], "blocks_throttle2")

    def test_propose_edit_routes_preflight_validation(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "propose_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "48000"},
                }
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "propose_edit")
        self.assertFalse(result["commit_eligible"])
        self.assertEqual(
            result["normalized_operations"][0]["instance_name"], "samp_rate"
        )

    def test_propose_edit_remove_connection_by_connection_id_does_not_mutate(self) -> None:
        agent, session = self._load_agent()

        add_trace = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "update_params",
                        "instance_name": "qtgui_time_sink_x_0",
                        "params": {"nconnections": "2"},
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "blocks_char_to_float_0",
                        "src_port": 0,
                        "dst_block": "qtgui_time_sink_x_0",
                        "dst_port": 1,
                    },
                ]
            },
        )
        self.assertTrue(add_trace["ok"])
        self.assertEqual(len(session.flowgraph.connections), 4)

        result = agent.execute_tool(
            "propose_edit",
            {
                "transaction": [
                    {
                        "op_type": "update_params",
                        "instance_name": "qtgui_time_sink_x_0",
                        "params": {"nconnections": "1"},
                    },
                    {
                        "op_type": "remove_connection",
                        "connection_id": "blocks_char_to_float_0:0->qtgui_time_sink_x_0:1",
                    },
                ]
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(session.flowgraph.connections), 4)
        sink_raw = next(
            entry
            for entry in session.flowgraph.raw_data["blocks"]
            if entry["name"] == "qtgui_time_sink_x_0"
        )
        self.assertEqual(sink_raw["parameters"]["nconnections"], "2")

    def test_apply_edit_remove_connection_by_connection_id_succeeds(self) -> None:
        agent, session = self._load_agent()

        add_trace = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "update_params",
                        "instance_name": "qtgui_time_sink_x_0",
                        "params": {"nconnections": "2"},
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "blocks_char_to_float_0",
                        "src_port": 0,
                        "dst_block": "qtgui_time_sink_x_0",
                        "dst_port": 1,
                    },
                ]
            },
        )
        self.assertTrue(add_trace["ok"])

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "update_params",
                        "instance_name": "qtgui_time_sink_x_0",
                        "params": {"nconnections": "1"},
                    },
                    {
                        "op_type": "remove_connection",
                        "connection_id": "blocks_char_to_float_0:0->qtgui_time_sink_x_0:1",
                    },
                ]
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(session.flowgraph.connections), 3)
        self.assertEqual(
            result["normalized_operations"],
            [
                {
                    "op_type": "update_params",
                    "instance_name": "qtgui_time_sink_x_0",
                    "params": {"nconnections": "1"},
                },
                {
                    "op_type": "remove_connection",
                    "src_block": "blocks_char_to_float_0",
                    "src_port": 0,
                    "dst_block": "qtgui_time_sink_x_0",
                    "dst_port": 1,
                },
            ],
        )

    def test_apply_edit_validates_and_allows_save_without_revalidation(self) -> None:
        agent, session = self._load_agent()

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "48000"},
                }
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "apply_edit")
        self.assertTrue(result["applied"])
        self.assertTrue(session.is_dirty)
        self.assertEqual(result["validation"]["status"], "valid")

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "validated_apply_save.grc"
            save_result = agent.execute_tool("save_graph", {"path": str(save_path)})

            self.assertTrue(save_result["ok"])
            self.assertTrue(save_path.exists())
            self.assertFalse(session.is_dirty)

    def test_save_graph_requires_validation_after_external_dirty_change(self) -> None:
        agent, session = self._load_agent()
        session.set_param("samp_rate", "value", "48000")

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "blocked_save.grc"
            result = agent.execute_tool("save_graph", {"path": str(save_path)})

            self.assertFalse(result["ok"])
            self.assertTrue(result["requires_validation"])
            self.assertTrue(session.is_dirty)
            self.assertFalse(save_path.exists())

    def test_validate_then_save_graph_succeeds_after_external_dirty_change(
        self,
    ) -> None:
        agent, session = self._load_agent()
        session.set_param("samp_rate", "value", "48000")

        validation = agent.execute_tool("validate_graph", {})

        self.assertTrue(validation["ok"])
        self.assertTrue(validation["valid"])
        self.assertEqual(validation["returncode"], 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "validated_save.grc"
            save_result = agent.execute_tool("save_graph", {"path": str(save_path)})

            self.assertTrue(save_result["ok"])
            self.assertTrue(save_path.exists())
            self.assertFalse(session.is_dirty)

    def test_validate_graph_tool_routes_correctly(self) -> None:
        agent, session = self._load_agent()

        result = agent.execute_tool("validate_graph", {})

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "validate_graph")
        self.assertTrue(result["valid"])
        self.assertEqual(result["returncode"], 0)
        self.assertIn("active_session", result)

    def test_validate_graph_grcc_timeout_returns_validation_timeout(self) -> None:
        """When grcc times out, validate_graph must return ok=False, error_type='validation_timeout'."""
        agent, session = self._load_agent()

        def _fake_timeout(raw_data: object) -> tuple[bool, str, str, int]:
            return (False, "", "grcc validation timed out after 30s", -2)

        with mock.patch.object(
            session.__class__, "_run_grcc_validation", side_effect=_fake_timeout
        ):
            result = agent.execute_tool("validate_graph", {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "validation_timeout")
        self.assertEqual(result["tool"], "validate_graph")

    def test_load_grc_tool_missing_file_returns_error(self) -> None:
        agent = GrcAgent()

        result = agent.execute_tool("load_grc", {"file_path": "/nonexistent.grc"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["tool"], "load_grc")
        self.assertIn("error_type", result)

    def test_describe_block_unknown_returns_error(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool(
            "describe_block", {"block_id": "totally_fake_block_xyz"}
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["tool"], "describe_block")

    def test_get_grc_context_unknown_node_returns_candidates(self) -> None:
        agent, _session = self._load_agent()

        result = agent.execute_tool("get_grc_context", {"node_id": "throttle"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "block_not_found")
        self.assertIn("candidate_nodes", result)
        self.assertIn("blocks_throttle2_0", result["candidate_nodes"])
        self.assertIn("Closest session matches", result["hint"])

    def test_health_check_ok_when_retrieval_ready(self) -> None:
        session = FlowgraphSession()
        agent = GrcAgent(session, catalog_root="/some/catalog")

        report = agent.health_check()

        self.assertEqual(report["status"], "ok")
        self.assertFalse(report["session_loaded"])
        self.assertTrue(report["retrieval_ready"])
        self.assertGreater(report["tool_count"], 0)

    def test_health_check_not_ready_without_retrieval(self) -> None:
        agent = GrcAgent()

        report = agent.health_check()

        self.assertEqual(report["status"], "not_ready")
        self.assertFalse(report["session_loaded"])
        self.assertFalse(report["retrieval_ready"])

    def test_health_check_session_loaded_not_required_for_ok(self) -> None:
        """Health check must return 'ok' even without a loaded file, when retrieval is ready."""
        session = FlowgraphSession()
        session.load(self._fixture_path())
        agent = GrcAgent(session, catalog_root="/some/catalog")

        report = agent.health_check()

        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["session_loaded"])

    def test_fake_cli_runtime_uses_phase_six_tool_names(self) -> None:
        from grc_agent.config import default_app_config
        output = StringIO()

        with redirect_stdout(output):
            exit_code = _run_fake_runtime(str(self._fixture_path()), default_app_config())

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Assistant called apply_edit", rendered)
        self.assertNotIn("Assistant called set_variable", rendered)

    def test_compact_history_deduplicates_session_entries(self) -> None:
        agent, _session = self._load_agent()
        agent.history = [
            {"role": "session", "content": {"path": "/a.grc"}},
            {"role": "user", "content": "first question"},
            {"role": "session", "content": {"path": "/b.grc"}},
            {"role": "user", "content": "second question"},
        ]

        agent.compact_history()

        session_entries = [t for t in agent.history if t.get("role") == "session"]
        self.assertEqual(len(session_entries), 1)
        self.assertEqual(session_entries[0]["content"]["path"], "/b.grc")

    def test_compact_history_truncates_old_tool_results(self) -> None:
        agent, _session = self._load_agent()
        big_content = {"ok": True, "tool": "search_grc", "results": ["a"] * 500, "extra": "data"}
        # Tools from the turn before the previous turn are compacted.
        # We need 3 user turns so the tool (between user[0] and user[1]) is "2 turns ago".
        agent.history = [
            {"role": "user", "content": "first question"},
            {"role": "tool", "tool_call_id": "t1", "name": "search_grc", "content": big_content},
            {"role": "user", "content": "second question"},
            {"role": "user", "content": "third question"},
        ]

        agent.compact_history()

        tool_entries = [t for t in agent.history if t.get("role") == "tool"]
        self.assertEqual(len(tool_entries), 1)
        compacted = tool_entries[0]["content"]
        self.assertIn("ok", compacted)
        self.assertIn("tool", compacted)
        self.assertNotIn("results", compacted)
        self.assertNotIn("extra", compacted)

    def test_compact_history_preserves_search_previews(self) -> None:
        agent, _session = self._load_agent()
        agent.history = [
            {"role": "user", "content": "first question"},
            {
                "role": "tool",
                "tool_call_id": "t1",
                "name": "search_grc",
                "content": {
                    "ok": True,
                    "tool": "search_grc",
                    "query": "carrier recovery",
                    "scope": "session",
                    "results": [
                        {
                            "block_id": "digital_costas_loop_cc",
                            "label": "Costas Loop",
                            "summary": "Carrier recovery loop.",
                        }
                    ],
                    "catalog_fallback_preview": [
                        {
                            "block_id": "digital_fll_band_edge_cc",
                            "label": "FLL Band-Edge",
                            "summary": "Frequency recovery.",
                        }
                    ],
                },
            },
            {"role": "user", "content": "second question"},
            {"role": "user", "content": "third question"},
        ]

        agent.compact_history()

        tool_entries = [t for t in agent.history if t.get("role") == "tool"]
        self.assertEqual(len(tool_entries), 1)
        compacted = tool_entries[0]["content"]
        self.assertEqual(compacted["query"], "carrier recovery")
        self.assertEqual(compacted["scope"], "session")
        self.assertEqual(
            compacted["results_preview"][0]["block_id"],
            "digital_costas_loop_cc",
        )
        self.assertEqual(
            compacted["catalog_fallback_preview"][0]["block_id"],
            "digital_fll_band_edge_cc",
        )

    def test_compact_history_preserves_current_turn_tool_results(self) -> None:
        agent, _session = self._load_agent()
        current_content = {"ok": True, "tool": "validate_graph", "valid": True, "full_data": "x" * 200}
        agent.history = [
            {"role": "user", "content": "first question"},
            {"role": "tool", "tool_call_id": "t1", "name": "validate_graph", "content": current_content},
        ]

        agent.compact_history()

        tool_entries = [t for t in agent.history if t.get("role") == "tool"]
        self.assertEqual(len(tool_entries), 1)
        # Only one user turn → this is the current turn → NOT compacted.
        self.assertIn("full_data", tool_entries[0]["content"])

    def test_save_graph_exception_returns_internal_error(self) -> None:
        agent, session = self._load_agent()
        # Mark clean and validated so the save gate passes.
        agent._last_validation_ok = True
        agent._last_validated_state_revision = session.state_revision

        with mock.patch.object(session, "save", side_effect=OSError("disk full")):
            result = agent.execute_tool("save_graph", {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "internal_error")
        self.assertIn("disk full", result["message"])

    def test_new_grc_creates_empty_session(self) -> None:
        agent = GrcAgent()
        result = agent.execute_tool("new_grc", {})
        self.assertTrue(result["ok"])
        self.assertIn("provenance", result)
        self.assertIsNotNone(agent.session.flowgraph)

    def test_new_grc_with_custom_graph_id(self) -> None:
        agent = GrcAgent()
        result = agent.execute_tool("new_grc", {"graph_id": "my_test_graph"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["provenance"]["graph_id"].startswith("grc:"))

    def test_new_grc_rejects_unsupported_profile(self) -> None:
        agent = GrcAgent()
        result = agent.execute_tool("new_grc", {"profile": "audio"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "invalid_request")
        self.assertIn("audio", result["message"])

    def test_apply_edit_add_block_variable(self) -> None:
        agent, _session = self._load_agent()
        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "add_block",
                    "block_type": "variable",
                    "instance_name": "debug_gain",
                    "parameters": {"value": "0"},
                }
            },
        )
        self.assertTrue(result["ok"])

    def test_apply_edit_add_block_rejects_missing_block_type(self) -> None:
        agent, _session = self._load_agent()
        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "add_block",
                    "instance_name": "debug_gain",
                    "parameters": {"value": "0"},
                }
            },
        )
        self.assertFalse(result["ok"])

    def test_apply_edit_add_block_rejects_invalid_block_id(self) -> None:
        agent, _session = self._load_agent()
        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "add_block",
                    "block_type": "nonexistent_block_xyz",
                    "instance_name": "bad_block",
                    "parameters": {"value": "0"},
                }
            },
        )
        self.assertFalse(result["ok"])

    def test_apply_edit_add_block_rejects_duplicate_instance_name(self) -> None:
        agent, _session = self._load_agent()
        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "add_block",
                    "block_type": "variable",
                    "instance_name": "samp_rate",
                    "parameters": {"value": "0"},
                }
            },
        )
        self.assertFalse(result["ok"])

    def test_apply_edit_rejects_block_uid_as_hidden_mutation_target(self) -> None:
        agent, session = self._load_agent()
        assert session.flowgraph is not None
        uid = next(
            block.block_uid
            for block in session.flowgraph.blocks
            if block.instance_name == "samp_rate"
        )
        before_revision = session.state_revision

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "block_uid": uid,
                    "params": {"value": "48000"},
                }
            },
        )

        self.assertFalse(result["ok"])
        self.assertIn("block_uid", str(result))
        self.assertEqual(session.state_revision, before_revision)

    def test_apply_edit_rejects_block_uid_even_with_instance_name(self) -> None:
        agent, session = self._load_agent()
        assert session.flowgraph is not None
        uid = next(
            block.block_uid
            for block in session.flowgraph.blocks
            if block.instance_name == "samp_rate"
        )
        before_revision = session.state_revision

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "block_uid": uid,
                    "params": {"value": "48000"},
                }
            },
        )

        self.assertFalse(result["ok"])
        self.assertIn("block_uid", str(result))
        self.assertEqual(session.state_revision, before_revision)

    def test_apply_edit_duplicate_name_param_edit_creates_executable_clarification(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block("dup"))
        raw_data["blocks"].append(self._detached_import_block("dup", state="disabled"))
        agent, session = self._load_agent_from_raw(raw_data)
        before_revision = session.state_revision

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "dup",
                    "params": {"value": "456"},
                }
            },
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["clarification_required"])
        self.assertEqual(result["state_revision"], before_revision)
        self.assertIsNotNone(agent._pending_clarification)
        options = result["options"]
        self.assertEqual(
            [(option["tool_name"], option["tool_args"]["transaction"]["block_type"]) for option in options],
            [("apply_edit", "variable"), ("apply_edit", "import")],
        )
        self.assertTrue(
            all("block_uid" not in str(option["tool_args"]) for option in options)
        )

        resolved = agent.resolve_pending_clarification("A")

        self.assertEqual(resolved["mode"], "executed")
        self.assertTrue(resolved["tool_result"]["ok"])
        assert session.flowgraph is not None
        variable = next(
            block
            for block in session.flowgraph.blocks
            if block.instance_name == "dup" and block.block_type == "variable"
        )
        imported = next(
            block
            for block in session.flowgraph.blocks
            if block.instance_name == "dup" and block.block_type == "import"
        )
        self.assertEqual(variable.params["parameters"]["value"], "456")
        self.assertEqual(imported.params["parameters"]["imports"], "import math")

    def test_apply_edit_duplicate_name_state_edit_creates_executable_clarification(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block("dup"))
        raw_data["blocks"].append(self._detached_import_block("dup"))
        agent, session = self._load_agent_from_raw(raw_data)

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_states",
                    "instance_name": "dup",
                    "state": "disabled",
                }
            },
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["clarification_required"])
        resolved = agent.resolve_pending_clarification("B")

        self.assertEqual(resolved["mode"], "executed")
        self.assertTrue(resolved["tool_result"]["ok"])
        assert session.flowgraph is not None
        variable = next(
            block
            for block in session.flowgraph.blocks
            if block.instance_name == "dup" and block.block_type == "variable"
        )
        imported = next(
            block
            for block in session.flowgraph.blocks
            if block.instance_name == "dup" and block.block_type == "import"
        )
        self.assertEqual(variable.params["states"]["state"], "enabled")
        self.assertEqual(imported.params["states"]["state"], "disabled")

    def test_same_name_same_type_duplicate_param_edit_creates_uid_target_ref_clarification(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block("dup"))
        second = self._detached_variable_block("dup", state="disabled")
        second["states"]["coordinate"] = [96, 128]
        raw_data["blocks"].append(second)
        agent, session = self._load_agent_from_raw(raw_data)
        before_snapshot = self._graph_identity_snapshot(session)

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "dup",
                    "params": {"value": "456"},
                }
            },
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["clarification_required"])
        self.assertEqual(self._graph_identity_snapshot(session), before_snapshot)
        options = result["options"]
        self.assertEqual(len(options), 2)
        for option in options:
            transaction = option["tool_args"]["transaction"]
            self.assertNotIn("instance_name", transaction)
            self.assertIn("target_ref", transaction)
            self.assertEqual(
                transaction["target_ref"]["base_state_revision"],
                session.state_revision,
            )

        resolved = agent.resolve_pending_clarification("B")

        self.assertEqual(resolved["mode"], "executed")
        self.assertTrue(resolved["tool_result"]["ok"], resolved)
        assert session.flowgraph is not None
        dup_values = [
            block.params["parameters"]["value"]
            for block in session.flowgraph.blocks
            if block.instance_name == "dup" and block.block_type == "variable"
        ]
        self.assertEqual(dup_values, ["123", "456"])

    def test_same_name_same_type_duplicate_param_edit_does_not_mutate_before_clarification(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block("dup"))
        second = self._detached_variable_block("dup", state="disabled")
        second["states"]["coordinate"] = [96, 128]
        raw_data["blocks"].append(second)
        agent, session = self._load_agent_from_raw(raw_data)
        before_snapshot = self._graph_identity_snapshot(session)

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "dup",
                    "params": {"value": "456"},
                }
            },
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["clarification_required"])
        self.assertIsNotNone(agent._pending_clarification)
        self.assertEqual(self._graph_identity_snapshot(session), before_snapshot)

    def test_same_name_same_type_duplicate_state_edit_does_not_mutate_before_clarification(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block("dup"))
        second = self._detached_variable_block("dup", state="disabled")
        second["states"]["coordinate"] = [96, 128]
        raw_data["blocks"].append(second)
        agent, session = self._load_agent_from_raw(raw_data)
        before_snapshot = self._graph_identity_snapshot(session)

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_states",
                    "instance_name": "dup",
                    "state": "disabled",
                }
            },
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["clarification_required"])
        self.assertIsNotNone(agent._pending_clarification)
        self.assertEqual(self._graph_identity_snapshot(session), before_snapshot)

    def test_same_name_same_type_duplicate_state_edit_creates_target_ref_and_mutates_selected_only(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block("dup"))
        second = self._detached_variable_block("dup")
        second["states"]["coordinate"] = [96, 128]
        raw_data["blocks"].append(second)
        agent, session = self._load_agent_from_raw(raw_data)

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_states",
                    "instance_name": "dup",
                    "state": "disabled",
                }
            },
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["clarification_required"])
        options = result["options"]
        self.assertEqual(len(options), 2)
        for option in options:
            transaction = option["tool_args"]["transaction"]
            self.assertNotIn("instance_name", transaction)
            self.assertEqual(transaction["op_type"], "update_states")
            self.assertIn("target_ref", transaction)
            self.assertEqual(
                transaction["target_ref"]["base_state_revision"],
                session.state_revision,
            )

        resolved = agent.resolve_pending_clarification("B")

        self.assertEqual(resolved["mode"], "executed")
        self.assertTrue(resolved["tool_result"]["ok"], resolved)
        assert session.flowgraph is not None
        dup_states = [
            block.params["states"]["state"]
            for block in session.flowgraph.blocks
            if block.instance_name == "dup" and block.block_type == "variable"
        ]
        self.assertEqual(dup_states, ["enabled", "disabled"])

    def test_same_name_same_type_duplicate_remove_block_rejects_without_mutation(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block("dup"))
        second = self._detached_variable_block("dup", state="disabled")
        second["states"]["coordinate"] = [96, 128]
        raw_data["blocks"].append(second)
        agent, session = self._load_agent_from_raw(raw_data)
        before_snapshot = self._graph_identity_snapshot(session)

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "remove_block",
                    "instance_name": "dup",
                }
            },
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["clarification_required"])
        self.assertIsNotNone(agent._pending_clarification)
        self.assertEqual(self._graph_identity_snapshot(session), before_snapshot)

    def test_same_name_same_type_duplicate_param_edit_succeeds_with_valid_block_uid_target_ref(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block("dup"))
        second = self._detached_variable_block("dup", state="disabled")
        second["states"]["coordinate"] = [96, 128]
        raw_data["blocks"].append(second)
        agent, session = self._load_agent_from_raw(raw_data)
        target_ref = self._block_target_ref(session, name="dup", block_type="variable", index=1)

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "target_ref": target_ref,
                    "params": {"value": "456"},
                }
            },
        )

        self.assertTrue(result["ok"], result)
        assert session.flowgraph is not None
        dup_values = [
            block.params["parameters"]["value"]
            for block in session.flowgraph.blocks
            if block.instance_name == "dup" and block.block_type == "variable"
        ]
        self.assertEqual(dup_values, ["123", "456"])

    def test_same_name_same_type_duplicate_state_edit_succeeds_with_valid_block_uid_target_ref(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block("dup"))
        second = self._detached_variable_block("dup", state="disabled")
        second["states"]["coordinate"] = [96, 128]
        raw_data["blocks"].append(second)
        agent, session = self._load_agent_from_raw(raw_data)
        target_ref = self._block_target_ref(session, name="dup", block_type="variable", index=0)

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_states",
                    "target_ref": target_ref,
                    "state": "disabled",
                }
            },
        )

        self.assertTrue(result["ok"], result)
        assert session.flowgraph is not None
        dup_states = [
            block.params["states"]["state"]
            for block in session.flowgraph.blocks
            if block.instance_name == "dup" and block.block_type == "variable"
        ]
        self.assertEqual(dup_states, ["disabled", "disabled"])

    def test_same_name_same_type_duplicate_remove_block_with_uid_target_ref_rejects_without_mutation(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_import_block("dup"))
        second = self._detached_import_block("dup", state="disabled")
        second["states"]["coordinate"] = [96, 128]
        raw_data["blocks"].append(second)
        agent, session = self._load_agent_from_raw(raw_data)
        target_ref = self._block_target_ref(session, name="dup", block_type="import", index=1)

        before_snapshot = self._graph_identity_snapshot(session)
        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "remove_block",
                    "target_ref": target_ref,
                }
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "block_still_referenced")
        self.assertEqual(self._graph_identity_snapshot(session), before_snapshot)

    def test_block_uid_target_ref_rejects_stale_state_revision_without_mutation(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block("dup"))
        second = self._detached_variable_block("dup", state="disabled")
        second["states"]["coordinate"] = [96, 128]
        raw_data["blocks"].append(second)
        agent, session = self._load_agent_from_raw(raw_data)
        target_ref = self._block_target_ref(session, name="dup", block_type="variable", index=1)
        session.set_param("samp_rate", "value", "48000")
        before_snapshot = self._graph_identity_snapshot(session)

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "target_ref": target_ref,
                    "params": {"value": "456"},
                }
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "stale_state_revision")
        self.assertEqual(self._graph_identity_snapshot(session), before_snapshot)

    def test_block_uid_target_ref_rejects_unknown_partial_and_mismatched_identity(self) -> None:
        agent, session = self._load_agent()
        valid_ref = self._block_target_ref(session, name="samp_rate", block_type="variable")
        cases = [
            ({**valid_ref, "block_uid": "block:abc"}, "invalid_block_uid"),
            ({**valid_ref, "block_uid": "block:0000000000000000"}, "block_uid_not_found"),
            ({**valid_ref, "expected_instance_name": "not_samp_rate"}, "block_uid_instance_mismatch"),
            ({**valid_ref, "expected_block_type": "import"}, "block_uid_type_mismatch"),
        ]
        for target_ref, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                before_snapshot = self._graph_identity_snapshot(session)
                result = agent.execute_tool(
                    "apply_edit",
                    {
                        "transaction": {
                            "op_type": "update_params",
                            "target_ref": target_ref,
                            "params": {"value": "48000"},
                        }
                    },
                )

                self.assertFalse(result["ok"], result)
                self.assertEqual(result["errors"][0]["code"], expected_code)
                self.assertEqual(self._graph_identity_snapshot(session), before_snapshot)

    def test_block_uid_target_ref_rejects_unsupported_connection_and_add_block_operations(self) -> None:
        agent, session = self._load_agent()
        target_ref = self._block_target_ref(session, name="samp_rate", block_type="variable")
        cases = [
            {
                "op_type": "add_connection",
                "target_ref": target_ref,
                "src_block": "analog_random_source_x_0",
                "src_port": 0,
                "dst_block": "blocks_throttle2_0",
                "dst_port": 0,
            },
            {
                "op_type": "remove_connection",
                "target_ref": target_ref,
                "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
            },
            {
                "op_type": "add_block",
                "target_ref": target_ref,
                "instance_name": "new_var",
                "block_type": "variable",
                "parameters": {"value": "1"},
            },
        ]
        for transaction in cases:
            with self.subTest(op_type=transaction["op_type"]):
                before_snapshot = self._graph_identity_snapshot(session)
                result = agent.execute_tool("apply_edit", {"transaction": transaction})

                self.assertFalse(result["ok"], result)
                self.assertIn("target_ref", str(result))
                self.assertEqual(self._graph_identity_snapshot(session), before_snapshot)

    def test_propose_edit_with_block_uid_target_ref_does_not_mutate(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block("dup"))
        second = self._detached_variable_block("dup", state="disabled")
        second["states"]["coordinate"] = [96, 128]
        raw_data["blocks"].append(second)
        agent, session = self._load_agent_from_raw(raw_data)
        target_ref = self._block_target_ref(session, name="dup", block_type="variable", index=1)
        before_snapshot = self._graph_identity_snapshot(session)

        result = agent.execute_tool(
            "propose_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "target_ref": target_ref,
                    "params": {"value": "456"},
                }
            },
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(self._graph_identity_snapshot(session), before_snapshot)

    def test_stale_duplicate_name_clarification_selection_is_rejected(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block("dup"))
        raw_data["blocks"].append(self._detached_import_block("dup", state="disabled"))
        agent, session = self._load_agent_from_raw(raw_data)

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "dup",
                    "params": {"value": "456"},
                }
            },
        )
        self.assertTrue(result["clarification_required"])

        session.set_param("samp_rate", "value", "48000")
        before_stale_resolution = self._graph_identity_snapshot(session)
        resolved = agent.resolve_pending_clarification("A")

        self.assertEqual(resolved["mode"], "expired")
        self.assertIsNone(agent._pending_clarification)
        self.assertEqual(self._graph_identity_snapshot(session), before_stale_resolution)

    def test_stale_same_name_same_type_duplicate_clarification_selection_is_rejected(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block("dup"))
        second = self._detached_variable_block("dup")
        second["states"]["coordinate"] = [96, 128]
        raw_data["blocks"].append(second)
        agent, session = self._load_agent_from_raw(raw_data)

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_states",
                    "instance_name": "dup",
                    "state": "disabled",
                }
            },
        )
        self.assertTrue(result["clarification_required"])

        session.set_param("samp_rate", "value", "48000")
        before_stale_resolution = self._graph_identity_snapshot(session)
        resolved = agent.resolve_pending_clarification("B")

        self.assertEqual(resolved["mode"], "expired")
        self.assertIsNone(agent._pending_clarification)
        self.assertEqual(self._graph_identity_snapshot(session), before_stale_resolution)

    def test_preview_only_duplicate_target_request_cannot_apply(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block("dup"))
        second = self._detached_variable_block("dup", state="disabled")
        second["states"]["coordinate"] = [96, 128]
        raw_data["blocks"].append(second)
        agent, session = self._load_agent_from_raw(raw_data)
        before_snapshot = self._graph_identity_snapshot(session)

        agent.init_turn_requirements("Preview changing dup value to 456. Do not apply it.")
        route_error = agent.validate_turn_route(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "dup",
                    "params": {"value": "456"},
                }
            },
        )

        self.assertIsNotNone(route_error)
        self.assertEqual(route_error["error_type"], "route_mismatch")
        preview = agent.execute_tool(
            "propose_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "dup",
                    "params": {"value": "456"},
                }
            },
        )

        self.assertFalse(preview["ok"])
        self.assertEqual(self._graph_identity_snapshot(session), before_snapshot)

    def test_disambiguated_detached_duplicate_remove_block_rejects_name_reference(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block("dup"))
        raw_data["blocks"].append(self._detached_import_block("dup", state="disabled"))
        agent, session = self._load_agent_from_raw(raw_data)
        before_revision = session.state_revision

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "remove_block",
                    "instance_name": "dup",
                    "block_type": "variable",
                }
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "preflight_rejected")
        self.assertEqual(result["errors"][0]["code"], "block_still_referenced")
        self.assertEqual(session.state_revision, before_revision)
        self.assertEqual(
            result["normalized_operations"],
            [
                {
                    "op_type": "remove_block",
                    "instance_name": "dup",
                    "block_type": "variable",
                }
            ],
        )

    def test_remove_connection_wrapper_accepts_exact_endpoint_and_normalizes_to_connection_id(self) -> None:
        agent, session = self._load_agent()
        before_revision = session.state_revision

        with mock.patch.object(
            agent,
            "_remove_connection_by_id",
            return_value={"tool": "remove_connection", "ok": True},
        ) as remove_by_id:
            result = agent.execute_tool(
                "remove_connection",
                {
                    "src_block": "analog_random_source_x_0",
                    "src_port": 0,
                    "dst_block": "blocks_throttle2_0",
                    "dst_port": 0,
                },
            )

        self.assertTrue(result["ok"])
        remove_by_id.assert_called_once_with(
            "analog_random_source_x_0:0->blocks_throttle2_0:0"
        )
        self.assertEqual(session.state_revision, before_revision)
        self.assertEqual(result["tool"], "remove_connection")

    def test_remove_connection_endpoint_with_no_match_fails_without_mutation(self) -> None:
        agent, session = self._load_agent()
        before_revision = session.state_revision

        result = agent.execute_tool(
            "remove_connection",
            {
                "src_block": "analog_random_source_x_0",
                "src_port": 0,
                "dst_block": "qtgui_time_sink_x_0",
                "dst_port": 99,
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "connection_not_found")
        self.assertEqual(session.state_revision, before_revision)

    def test_remove_connection_ambiguous_endpoint_creates_connection_id_clarification(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["connections"].append(
            ["blocks_throttle2_0", "0", "qtgui_time_sink_x_0", "1"]
        )
        agent, session = self._load_agent_from_raw(raw_data)
        before_revision = session.state_revision

        result = agent.execute_tool(
            "remove_connection",
            {"src_block": "blocks_throttle2_0", "src_port": 0},
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["clarification_required"])
        self.assertEqual(result["state_revision"], before_revision)
        self.assertIsNotNone(agent._pending_clarification)
        self.assertEqual(
            [option["tool_args"] for option in result["options"]],
            [
                {"connection_id": "blocks_throttle2_0:0->blocks_char_to_float_0:0"},
                {"connection_id": "blocks_throttle2_0:0->qtgui_time_sink_x_0:1"},
            ],
        )

    def test_apply_edit_atomic_message_rewire_by_connection_id(self) -> None:
        agent, session = self._build_message_rewire_agent()
        before_blocks = [(b.instance_name, b.block_type) for b in session.flowgraph.blocks]
        before_revision = session.state_revision

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "remove_connection",
                        "connection_id": "strobe_0:strobe->debug_0:print",
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "strobe_0",
                        "src_port": "strobe",
                        "dst_block": "debug_1",
                        "dst_port": "print",
                    },
                ]
            },
        )

        self.assertTrue(result["ok"], result.get("message"))
        connections = {
            f"{c.src_block}:{c.src_port}->{c.dst_block}:{c.dst_port}"
            for c in session.flowgraph.connections
        }
        self.assertNotIn("strobe_0:strobe->debug_0:print", connections)
        self.assertIn("strobe_0:strobe->debug_1:print", connections)
        self.assertEqual(len(connections), 1)
        self.assertEqual(
            [(b.instance_name, b.block_type) for b in session.flowgraph.blocks],
            before_blocks,
        )
        self.assertGreater(session.state_revision, before_revision)
        self.assertEqual(
            result["normalized_operations"],
            [
                {
                    "op_type": "remove_connection",
                    "src_block": "strobe_0",
                    "src_port": "strobe",
                    "dst_block": "debug_0",
                    "dst_port": "print",
                },
                {
                    "op_type": "add_connection",
                    "src_block": "strobe_0",
                    "src_port": "strobe",
                    "dst_block": "debug_1",
                    "dst_port": "print",
                },
            ],
        )

    def test_apply_edit_atomic_stream_rewire_by_old_endpoint_fields(self) -> None:
        agent, session = self._load_agent()
        before_blocks = [(b.instance_name, b.block_type) for b in session.flowgraph.blocks]
        before_connections = {
            f"{c.src_block}:{c.src_port}->{c.dst_block}:{c.dst_port}"
            for c in session.flowgraph.connections
        }

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "remove_connection",
                        "src_block": "blocks_throttle2_0",
                        "src_port": 0,
                        "dst_block": "blocks_char_to_float_0",
                        "dst_port": 0,
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "analog_random_source_x_0",
                        "src_port": 0,
                        "dst_block": "blocks_char_to_float_0",
                        "dst_port": 0,
                    },
                ]
            },
        )

        self.assertTrue(result["ok"], result.get("message"))
        connections = {
            f"{c.src_block}:{c.src_port}->{c.dst_block}:{c.dst_port}"
            for c in session.flowgraph.connections
        }
        self.assertEqual(len(connections), len(before_connections))
        self.assertNotIn("blocks_throttle2_0:0->blocks_char_to_float_0:0", connections)
        self.assertIn("analog_random_source_x_0:0->blocks_char_to_float_0:0", connections)
        self.assertEqual(
            [(b.instance_name, b.block_type) for b in session.flowgraph.blocks],
            before_blocks,
        )

    def test_apply_edit_invalid_rewire_new_endpoint_rolls_back_old_connection(self) -> None:
        agent, session = self._build_message_rewire_agent()
        before_revision = session.state_revision
        before_connections = list(session.flowgraph.connections)

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "remove_connection",
                        "connection_id": "strobe_0:strobe->debug_0:print",
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "strobe_0",
                        "src_port": "strobe",
                        "dst_block": "missing_debug",
                        "dst_port": "print",
                    },
                ]
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "preflight_rejected")
        self.assertEqual(session.state_revision, before_revision)
        self.assertEqual(session.flowgraph.connections, before_connections)

    def test_apply_edit_invalid_rewire_gnu_end_state_rolls_back_old_connections(self) -> None:
        agent, session = self._load_agent()
        before_revision = session.state_revision
        before_connections = list(session.flowgraph.connections)

        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "remove_connection",
                        "connection_id": "analog_random_source_x_0:0->blocks_throttle2_0:0",
                    },
                    {
                        "op_type": "remove_connection",
                        "connection_id": "blocks_throttle2_0:0->blocks_char_to_float_0:0",
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "analog_random_source_x_0",
                        "src_port": 0,
                        "dst_block": "blocks_char_to_float_0",
                        "dst_port": 0,
                    },
                ]
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "gnu_validation_failed")
        self.assertEqual(session.state_revision, before_revision)
        self.assertEqual(session.flowgraph.connections, before_connections)

    def test_rewire_connection_wrapper_resolves_old_endpoint_to_atomic_transaction(self) -> None:
        agent, session = self._build_message_rewire_agent()

        result = agent.execute_tool(
            "rewire_connection",
            {
                "old_src_block": "strobe_0",
                "old_src_port": "strobe",
                "old_dst_block": "debug_0",
                "old_dst_port": "print",
                "new_src_block": "strobe_0",
                "new_src_port": "strobe",
                "new_dst_block": "debug_1",
                "new_dst_port": "print",
            },
        )

        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(result["tool"], "rewire_connection")
        self.assertEqual(
            result["normalized_operations"],
            [
                {
                    "op_type": "remove_connection",
                    "src_block": "strobe_0",
                    "src_port": "strobe",
                    "dst_block": "debug_0",
                    "dst_port": "print",
                },
                {
                    "op_type": "add_connection",
                    "src_block": "strobe_0",
                    "src_port": "strobe",
                    "dst_block": "debug_1",
                    "dst_port": "print",
                },
            ],
        )
        connections = {
            f"{c.src_block}:{c.src_port}->{c.dst_block}:{c.dst_port}"
            for c in session.flowgraph.connections
        }
        self.assertEqual(connections, {"strobe_0:strobe->debug_1:print"})

    def test_rewire_connection_ambiguous_old_endpoint_creates_clarification(self) -> None:
        agent, session = self._build_message_rewire_agent()
        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "add_block",
                        "block_type": "pdu_random_pdu",
                        "instance_name": "pdu_0",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "strobe_0",
                        "src_port": "strobe",
                        "dst_block": "pdu_0",
                        "dst_port": "generate",
                    },
                ]
            },
        )
        self.assertTrue(result["ok"], result.get("message"))
        before_revision = session.state_revision

        result = agent.execute_tool(
            "rewire_connection",
            {
                "old_src_block": "strobe_0",
                "old_src_port": "strobe",
                "new_src_block": "strobe_0",
                "new_src_port": "strobe",
                "new_dst_block": "debug_1",
                "new_dst_port": "print_pdu",
            },
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["clarification_required"])
        self.assertEqual(result["kind"], "rewire_connection_disambiguation")
        self.assertEqual(result["state_revision"], before_revision)
        self.assertIsNotNone(agent._pending_clarification)
        self.assertEqual(
            [option["tool_name"] for option in result["options"]],
            ["rewire_connection", "rewire_connection"],
        )
        self.assertEqual(
            [option["tool_args"]["old_connection_id"] for option in result["options"]],
            [
                "strobe_0:strobe->debug_0:print",
                "strobe_0:strobe->pdu_0:generate",
            ],
        )

    def test_rewire_connection_stale_clarification_is_rejected(self) -> None:
        agent, session = self._build_message_rewire_agent()
        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "add_block",
                        "block_type": "pdu_random_pdu",
                        "instance_name": "pdu_0",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "strobe_0",
                        "src_port": "strobe",
                        "dst_block": "pdu_0",
                        "dst_port": "generate",
                    },
                ]
            },
        )
        self.assertTrue(result["ok"], result.get("message"))
        result = agent.execute_tool(
            "rewire_connection",
            {
                "old_src_block": "strobe_0",
                "old_src_port": "strobe",
                "new_src_block": "strobe_0",
                "new_src_port": "strobe",
                "new_dst_block": "debug_1",
                "new_dst_port": "print_pdu",
            },
        )
        self.assertTrue(result["clarification_required"])
        session.set_param("debug_1", "log_level", "debug")

        resolved = agent.resolve_pending_clarification("A")

        self.assertEqual(resolved["mode"], "expired")
        self.assertIsNone(agent._pending_clarification)

    def test_rewire_connection_invalid_new_endpoint_rolls_back_old_connection(self) -> None:
        agent, session = self._build_message_rewire_agent()
        before_revision = session.state_revision
        before_connections = list(session.flowgraph.connections)

        result = agent.execute_tool(
            "rewire_connection",
            {
                "old_connection_id": "strobe_0:strobe->debug_0:print",
                "new_src_block": "strobe_0",
                "new_src_port": "strobe",
                "new_dst_block": "missing_debug",
                "new_dst_port": "print",
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["tool"], "rewire_connection")
        self.assertEqual(result["error_type"], "preflight_rejected")
        self.assertEqual(session.state_revision, before_revision)
        self.assertEqual(session.flowgraph.connections, before_connections)

    def test_rewire_connection_missing_entire_new_side_rejects_without_mutation(self) -> None:
        agent, session = self._build_message_rewire_agent()
        before_revision = session.state_revision
        before_connections = list(session.flowgraph.connections)

        result = agent.execute_tool(
            "rewire_connection",
            {
                "old_connection_id": "strobe_0:strobe->debug_0:print",
                "new_src_block": "strobe_0",
                "new_src_port": "strobe",
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "tool_call_invalid")
        self.assertEqual(
            result["validation_errors"],
            [
                {
                    "code": "missing_required",
                    "field": "new_destination",
                    "message": (
                        "Provide exact fields or at least one bounded hint for "
                        "this new endpoint side."
                    ),
                }
            ],
        )
        self.assertEqual(session.state_revision, before_revision)
        self.assertEqual(session.flowgraph.connections, before_connections)

    def test_rewire_connection_ambiguous_new_source_creates_clarification(self) -> None:
        agent, session = self._build_message_rewire_agent()
        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_message_strobe",
                        "instance_name": "strobe_1",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "pdu_random_pdu",
                        "instance_name": "pdu_0",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "strobe_1",
                        "src_port": "strobe",
                        "dst_block": "pdu_0",
                        "dst_port": "generate",
                    },
                ]
            },
        )
        self.assertTrue(result["ok"], result.get("message"))
        before_revision = session.state_revision
        before_connections = list(session.flowgraph.connections)

        result = agent.execute_tool(
            "rewire_connection",
            {
                "old_connection_id": "strobe_0:strobe->debug_0:print",
                "new_src_port": "strobe",
                "new_dst_block": "debug_1",
                "new_dst_port": "print",
            },
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["clarification_required"])
        self.assertEqual(result["kind"], "rewire_new_endpoint_disambiguation")
        self.assertEqual(result["state_revision"], before_revision)
        self.assertEqual(session.state_revision, before_revision)
        self.assertEqual(session.flowgraph.connections, before_connections)
        self.assertIsNotNone(agent._pending_clarification)
        self.assertEqual(
            [option["tool_args"]["new_src_block"] for option in result["options"]],
            ["strobe_0", "strobe_1"],
        )
        self.assertEqual(
            [option["tool_args"]["old_connection_id"] for option in result["options"]],
            [
                "strobe_0:strobe->debug_0:print",
                "strobe_0:strobe->debug_0:print",
            ],
        )

    def test_rewire_connection_ambiguous_new_destination_uses_selected_endpoint(self) -> None:
        agent, session = self._build_message_rewire_agent()
        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_message_strobe",
                        "instance_name": "strobe_1",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_message_debug",
                        "instance_name": "debug_2",
                        "parameters": {},
                    },
                ]
            },
        )
        self.assertTrue(result["ok"], result.get("message"))

        result = agent.execute_tool(
            "rewire_connection",
            {
                "old_connection_id": "strobe_0:strobe->debug_0:print",
                "new_src_block": "strobe_0",
                "new_src_port": "strobe",
                "new_dst_port": "print",
            },
        )
        self.assertTrue(result["clarification_required"])
        self.assertEqual(
            [option["tool_args"]["new_dst_block"] for option in result["options"]],
            ["debug_1", "debug_2"],
        )

        resolved = agent.resolve_pending_clarification("B")

        self.assertEqual(resolved["mode"], "executed")
        tool_result = resolved["tool_result"]
        self.assertTrue(tool_result["ok"], tool_result.get("message"))
        connections = {
            f"{c.src_block}:{c.src_port}->{c.dst_block}:{c.dst_port}"
            for c in session.flowgraph.connections
        }
        self.assertNotIn("strobe_0:strobe->debug_1:print", connections)
        self.assertIn("strobe_0:strobe->debug_2:print", connections)
        self.assertNotIn("strobe_0:strobe->debug_0:print", connections)

    def test_rewire_connection_message_numeric_port_hints_do_not_create_clarification(self) -> None:
        agent, session = self._build_message_rewire_agent()
        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_message_debug",
                        "instance_name": "debug_2",
                        "parameters": {},
                    },
                ]
            },
        )
        self.assertTrue(result["ok"], result.get("message"))
        before_revision = session.state_revision
        before_connections = list(session.flowgraph.connections)

        result = agent.execute_tool(
            "rewire_connection",
            {
                "old_connection_id": "strobe_0:strobe->debug_0:print",
                "new_src_block": "strobe_0",
                "new_src_port": 0,
                "new_dst_port": 1,
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "tool_call_invalid")
        self.assertNotIn("clarification_required", result)
        self.assertIsNone(agent._pending_clarification)
        self.assertEqual(session.state_revision, before_revision)
        self.assertEqual(session.flowgraph.connections, before_connections)

    def test_rewire_connection_new_endpoint_clarification_rejects_stale_selection(self) -> None:
        agent, session = self._build_message_rewire_agent()
        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_message_strobe",
                        "instance_name": "strobe_1",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "pdu_random_pdu",
                        "instance_name": "pdu_0",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "strobe_1",
                        "src_port": "strobe",
                        "dst_block": "pdu_0",
                        "dst_port": "generate",
                    },
                ]
            },
        )
        self.assertTrue(result["ok"], result.get("message"))
        result = agent.execute_tool(
            "rewire_connection",
            {
                "old_connection_id": "strobe_0:strobe->debug_0:print",
                "new_src_port": "strobe",
                "new_dst_block": "debug_1",
                "new_dst_port": "print",
            },
        )
        self.assertTrue(result["clarification_required"])
        before_connections = list(session.flowgraph.connections)
        session.set_param("debug_1", "log_level", "debug")

        resolved = agent.resolve_pending_clarification("A")

        self.assertEqual(resolved["mode"], "expired")
        self.assertIsNone(agent._pending_clarification)
        self.assertEqual(session.flowgraph.connections, before_connections)

    def test_rewire_connection_too_many_new_endpoint_candidates_does_not_auto_pick(self) -> None:
        agent, session = self._build_message_rewire_agent()
        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_message_strobe",
                        "instance_name": "strobe_1",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_message_strobe",
                        "instance_name": "strobe_2",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_message_strobe",
                        "instance_name": "strobe_3",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "pdu_random_pdu",
                        "instance_name": "pdu_1",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "pdu_random_pdu",
                        "instance_name": "pdu_2",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "pdu_random_pdu",
                        "instance_name": "pdu_3",
                        "parameters": {},
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "strobe_1",
                        "src_port": "strobe",
                        "dst_block": "pdu_1",
                        "dst_port": "generate",
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "strobe_2",
                        "src_port": "strobe",
                        "dst_block": "pdu_2",
                        "dst_port": "generate",
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "strobe_3",
                        "src_port": "strobe",
                        "dst_block": "pdu_3",
                        "dst_port": "generate",
                    },
                ]
            },
        )
        self.assertTrue(result["ok"], result.get("message"))
        before_revision = session.state_revision
        before_connections = list(session.flowgraph.connections)

        result = agent.execute_tool(
            "rewire_connection",
            {
                "old_connection_id": "strobe_0:strobe->debug_0:print",
                "new_src_port": "strobe",
                "new_dst_block": "debug_1",
                "new_dst_port": "print",
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "ambiguous_rewire_endpoint")
        self.assertEqual(session.state_revision, before_revision)
        self.assertEqual(session.flowgraph.connections, before_connections)
        self.assertIsNone(agent._pending_clarification)

    def test_rewire_connection_stream_new_source_clarification_uses_selected_endpoint(self) -> None:
        agent, session = self._load_agent()
        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": [
                    {
                        "op_type": "update_params",
                        "instance_name": "qtgui_time_sink_x_0",
                        "params": {"nconnections": "2"},
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "analog_random_source_x",
                        "instance_name": "analog_random_source_x_1",
                        "parameters": {
                            "type": "byte",
                            "min": "0",
                            "max": "2",
                            "num_samps": "1000",
                            "repeat": "True",
                        },
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_throttle2",
                        "instance_name": "blocks_throttle2_1",
                        "parameters": {
                            "type": "byte",
                            "samples_per_second": "samp_rate",
                            "vlen": "1",
                            "ignoretag": "True",
                            "limit": "auto",
                            "maximum": "0.1",
                        },
                    },
                    {
                        "op_type": "add_block",
                        "block_type": "blocks_char_to_float",
                        "instance_name": "blocks_char_to_float_1",
                        "parameters": {"vlen": "1", "scale": "1"},
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "analog_random_source_x_1",
                        "src_port": 0,
                        "dst_block": "blocks_throttle2_1",
                        "dst_port": 0,
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "blocks_throttle2_1",
                        "src_port": 0,
                        "dst_block": "blocks_char_to_float_1",
                        "dst_port": 0,
                    },
                    {
                        "op_type": "add_connection",
                        "src_block": "blocks_char_to_float_1",
                        "src_port": 0,
                        "dst_block": "qtgui_time_sink_x_0",
                        "dst_port": 1,
                    },
                ]
            },
        )
        self.assertTrue(result["ok"], result.get("message"))

        result = agent.execute_tool(
            "rewire_connection",
            {
                "old_connection_id": "blocks_throttle2_0:0->blocks_char_to_float_0:0",
                "new_src_port": 0,
                "new_dst_block": "blocks_char_to_float_0",
                "new_dst_port": 0,
            },
        )

        self.assertTrue(result["clarification_required"])
        self.assertEqual(result["kind"], "rewire_new_endpoint_disambiguation")
        self.assertEqual(
            [option["tool_args"]["new_src_block"] for option in result["options"]],
            [
                "analog_random_source_x_0",
                "analog_random_source_x_1",
                "blocks_throttle2_1",
            ],
        )

        resolved = agent.resolve_pending_clarification("C")

        self.assertEqual(resolved["mode"], "executed")
        tool_result = resolved["tool_result"]
        self.assertTrue(tool_result["ok"], tool_result.get("message"))
        connections = {
            f"{c.src_block}:{c.src_port}->{c.dst_block}:{c.dst_port}"
            for c in session.flowgraph.connections
        }
        self.assertIn("blocks_throttle2_1:0->blocks_char_to_float_0:0", connections)
        self.assertNotIn("blocks_throttle2_0:0->blocks_char_to_float_0:0", connections)

    def test_state_driven_suggested_next_tools_after_apply_edit(self) -> None:
        agent, _session = self._load_agent()
        result = agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "48000"},
                }
            },
        )
        self.assertTrue(result["ok"])
        self.assertIn("suggested_next_tools", result)
        self.assertIn("validate_graph", result["suggested_next_tools"])

    def test_state_driven_suggested_next_tools_after_validate(self) -> None:
        agent, _session = self._load_agent()
        agent.execute_tool(
            "apply_edit",
            {
                "transaction": {
                    "op_type": "update_params",
                    "instance_name": "samp_rate",
                    "params": {"value": "48000"},
                }
            },
        )
        result = agent.execute_tool("validate_graph", {})
        self.assertTrue(result["ok"])
        self.assertIn("suggested_next_tools", result)
        self.assertIn("save_graph", result["suggested_next_tools"])
