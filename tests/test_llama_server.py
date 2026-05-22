"""Tests for the thin llama.cpp adapter over the narrowed runtime surface."""

from contextlib import redirect_stdout
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
from pathlib import Path
import time
from threading import Thread
import unittest
from unittest import mock
from urllib import error

from grc_agent.agent import GrcAgent
from grc_agent.cli import _run_health_command, _run_llama_runtime
from grc_agent.config import load_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import (
    LlamaServerClient,
    LlamaServerError,
    _looks_like_tool_call_text,
    run_bounded_llama_turn,
)
from grc_agent.recovery import RECOVERABLE_MISSING_ARGUMENTS, RecoveryDecision
from grc_agent.runtime.tool_schemas import MVP_MODEL_TOOL_NAMES


class _ScriptedLlamaServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        responses: list[dict[str, object]],
        *,
        model_id: str = "test-llama-model",
        models_payload: dict[str, object] | None = None,
        props_payload: dict[str, object] | None = None,
    ) -> None:
        super().__init__(server_address, _ScriptedLlamaHandler)
        self.responses = list(responses)
        self.requests_seen: list[dict[str, object]] = []
        self.model_id = model_id
        self.models_payload = models_payload
        self.props_payload = props_payload


class _ScriptedLlamaHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server = self.server
        assert isinstance(server, _ScriptedLlamaServer)
        server.requests_seen.append(
            {"method": "GET", "path": self.path, "payload": None}
        )

        if self.path in {"/health", "/v1/health"}:
            self._write_json(200, {"status": "ok"})
            return

        if self.path == "/v1/models":
            payload = server.models_payload
            if payload is None:
                payload = {
                    "object": "list",
                    "data": [
                        {
                            "id": server.model_id,
                            "object": "model",
                            "meta": None,
                        }
                    ],
                }
            self._write_json(200, payload)
            return

        if self.path == "/props":
            payload = server.props_payload
            if payload is None:
                payload = {"chat_template_tool_use": "test-template"}
            self._write_json(200, payload)
            return

        self._write_json(
            404,
            {"error": {"code": 404, "message": "Not found", "type": "not_found_error"}},
        )

    def do_POST(self) -> None:
        server = self.server
        assert isinstance(server, _ScriptedLlamaServer)

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_payload = self.rfile.read(content_length).decode("utf-8")
        payload = json.loads(raw_payload) if raw_payload else None
        server.requests_seen.append(
            {"method": "POST", "path": self.path, "payload": payload}
        )

        if self.path == "/v1/chat/completions":
            if not server.responses:
                self._write_json(
                    500,
                    {
                        "error": {
                            "code": 500,
                            "message": "No scripted response remaining",
                            "type": "internal_error",
                        }
                    },
                )
                return

            self._write_json(200, server.responses.pop(0))
            return

        self._write_json(
            404,
            {"error": {"code": 404, "message": "Not found", "type": "not_found_error"}},
        )

    def log_message(self, format: str, *args: object) -> None:
        return

    def _write_json(self, status_code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            return


class _HangingServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], delay_seconds: float) -> None:
        super().__init__(server_address, _HangingHandler)
        self.delay_seconds = delay_seconds


class _HangingHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server = self.server
        assert isinstance(server, _HangingServer)
        if self.path == "/health":
            time.sleep(server.delay_seconds)
            self._write_json(200, {"status": "ok"})
            return

        self._write_json(
            404,
            {"error": {"code": 404, "message": "Not found", "type": "not_found_error"}},
        )

    def log_message(self, format: str, *args: object) -> None:
        return

    def _write_json(self, status_code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            return


class _BodyResponse:
    """Minimal context-manager response object for patched urlopen calls."""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def __enter__(self) -> "_BodyResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


class _ReadTimeoutResponse(_BodyResponse):
    """Minimal response object that times out while reading the body."""

    def __init__(self) -> None:
        super().__init__("")

    def read(self) -> bytes:
        raise TimeoutError("timed out")


class LlamaServerAdapterTests(unittest.TestCase):
    """Tests for the bounded llama.cpp adapter loop."""

    def _llama_config(self):
        return load_app_config().llama

    def _fixture_path(self) -> Path:
        test_directory = Path(__file__).resolve().parent
        return test_directory / "data" / "random_bit_generator.grc"

    def _load_agent(self) -> tuple[GrcAgent, FlowgraphSession]:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        return GrcAgent(session), session


    def _write_alt_fixture(self, directory: Path) -> Path:
        alt_path = directory / "random_bit_generator_alt.grc"
        alt_path.write_text(
            self._fixture_path()
            .read_text(encoding="utf-8")
            .replace("samp_rate", "fresh_clock_value"),
            encoding="utf-8",
        )
        return alt_path

    def _start_server(
        self,
        responses: list[dict[str, object]],
        *,
        model_id: str = "test-llama-model",
        models_payload: dict[str, object] | None = None,
        props_payload: dict[str, object] | None = None,
    ) -> _ScriptedLlamaServer:
        server = _ScriptedLlamaServer(
            ("127.0.0.1", 0),
            responses,
            model_id=model_id,
            models_payload=models_payload,
            props_payload=props_payload,
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1.0)
        return server

    def _server_url(self, server: ThreadingHTTPServer) -> str:
        host, port = server.server_address
        return f"http://{host}:{port}"

    def _start_hanging_server(self, delay_seconds: float) -> _HangingServer:
        server = _HangingServer(("127.0.0.1", 0), delay_seconds)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1.0)
        return server

    def _client(self, server_url: str) -> LlamaServerClient:
        llama_config = self._llama_config()
        return LlamaServerClient(
            server_url,
            timeout_seconds=llama_config.request_timeout_seconds,
            max_tokens=llama_config.max_tokens,
            temperature=llama_config.temperature,
            enable_thinking=llama_config.enable_thinking,
        )





    def test_get_model_id_requires_single_entry(self) -> None:
        server = self._start_server(
            [],
            models_payload={
                "object": "list",
                "data": [
                    {"id": "model-a", "object": "model"},
                    {"id": "model-b", "object": "model"},
                ],
            },
        )
        client = self._client(self._server_url(server))

        with self.assertRaisesRegex(LlamaServerError, "exactly one model entry"):
            client.get_model_id()

    def test_get_model_id_allows_missing_meta_while_loading(self) -> None:
        expected_model = self._llama_config().model
        server = self._start_server(
            [],
            models_payload={
                "object": "list",
                "data": [
                    {
                        "id": expected_model,
                        "object": "model",
                        "meta": None,
                    }
                ],
            },
        )
        client = self._client(self._server_url(server))

        self.assertEqual(client.get_model_id(), expected_model)
        client.require_model_alias(expected_model)

    def test_get_server_properties_reads_props_endpoint(self) -> None:
        server = self._start_server(
            [],
            props_payload={
                "chat_template_tool_use": "tool-template",
                "tool_call_parser": "native",
            },
        )
        client = self._client(self._server_url(server))

        props = client.get_server_properties()

        self.assertEqual(props["chat_template_tool_use"], "tool-template")
        self.assertEqual(props["tool_call_parser"], "native")

    def test_request_json_wraps_connect_timeout(self) -> None:
        client = self._client("http://127.0.0.1:65530")

        with mock.patch(
            "grc_agent.llama_server.request.urlopen",
            side_effect=error.URLError(TimeoutError("timed out")),
        ):
            with self.assertRaisesRegex(LlamaServerError, "Timed out connecting"):
                client.require_ready()

    def test_request_json_wraps_read_timeout(self) -> None:
        client = self._client("http://127.0.0.1:8080")

        with mock.patch(
            "grc_agent.llama_server.request.urlopen",
            return_value=_ReadTimeoutResponse(),
        ):
            with self.assertRaisesRegex(LlamaServerError, "Timed out waiting"):
                client.require_ready()

    def test_request_json_rejects_invalid_json(self) -> None:
        client = self._client("http://127.0.0.1:8080")

        with mock.patch(
            "grc_agent.llama_server.request.urlopen",
            return_value=_BodyResponse("not-json"),
        ):
            with self.assertRaisesRegex(LlamaServerError, "non-JSON response"):
                client.require_ready()

    def test_require_ready_times_out_against_real_hanging_server(self) -> None:
        server = self._start_hanging_server(delay_seconds=0.2)
        client = LlamaServerClient(
            self._server_url(server),
            timeout_seconds=0.05,
            max_tokens=self._llama_config().max_tokens,
            temperature=self._llama_config().temperature,
            enable_thinking=self._llama_config().enable_thinking,
        )

        with self.assertRaisesRegex(LlamaServerError, "Timed out"):
            client.require_ready()


    def test_parse_assistant_message_recovers_plain_text_tool_stub(self) -> None:
        client = self._client("http://127.0.0.1:1")
        content, tool_calls = client.parse_assistant_message(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": 'describe_block(block_id="qtgui_time_sink_x")\n<eos>\n<eos>',
                        }
                    }
                ]
            }
        )

        self.assertIsNone(content)
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0].name, "describe_block")
        self.assertEqual(
            tool_calls[0].arguments,
            {"block_id": "qtgui_time_sink_x"},
        )

    def test_parse_assistant_message_recovers_plain_text_transaction_stub(self) -> None:
        client = self._client("http://127.0.0.1:1")
        content, tool_calls = client.parse_assistant_message(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": (
                                '{"instance_name": "samp_rate", "op_type": "update_params", '
                                '"params": {"value": "44100"}}'
                            ),
                        }
                    }
                ]
            },
            fallback_transaction_checker=GrcAgent.looks_like_transaction_payload,
        )

        self.assertIsNone(content)
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0].name, "apply_edit")
        self.assertEqual(
            tool_calls[0].arguments,
            {
                "transaction": {
                    "instance_name": "samp_rate",
                    "op_type": "update_params",
                    "params": {"value": "44100"},
                }
            },
        )

    def test_parse_assistant_message_recovers_transaction_then_save_stub(self) -> None:
        client = self._client("http://127.0.0.1:1")
        content, tool_calls = client.parse_assistant_message(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": (
                                '{"transaction": {"op_type": "update_params", '
                                '"instance_name": "samp_rate", "params": {"value": "22050"}}}\n'
                                "save_graph()"
                            ),
                        }
                    }
                ]
            },
            fallback_transaction_checker=GrcAgent.looks_like_transaction_payload,
        )

        self.assertIsNone(content)
        self.assertEqual([tool_call.name for tool_call in tool_calls], ["apply_edit", "save_graph"])
        self.assertEqual(
            tool_calls[0].arguments,
            {
                "transaction": {
                    "instance_name": "samp_rate",
                    "op_type": "update_params",
                    "params": {"value": "22050"},
                }
            },
        )
        self.assertEqual(tool_calls[1].arguments, {})

    def test_parse_assistant_message_repairs_unclosed_transaction_stub(self) -> None:
        client = self._client("http://127.0.0.1:1")
        content, tool_calls = client.parse_assistant_message(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": (
                                '{"transaction": [{"op_type": "update_params", '
                                '"instance_name": "qtgui_time_sink_x_0", '
                                '"params": {"srate": "samp_rate/2"}]}'
                            ),
                        }
                    }
                ]
            },
            fallback_transaction_checker=GrcAgent.looks_like_transaction_payload,
        )

        self.assertIsNone(content)
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0].name, "apply_edit")
        self.assertEqual(
            tool_calls[0].arguments,
            {
                "transaction": [
                    {
                        "instance_name": "qtgui_time_sink_x_0",
                        "op_type": "update_params",
                        "params": {"srate": "samp_rate/2"},
                    }
                ]
            },
        )

    def test_parse_assistant_message_repairs_unclosed_native_tool_arguments(self) -> None:
        client = self._client("http://127.0.0.1:1")
        content, tool_calls = client.parse_assistant_message(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "apply_edit",
                                        "arguments": (
                                            '{"transaction": {"op_type": "remove_connection", '
                                            '"src_block": "analog_random_source_x_0", '
                                            '"src_port": 0, '
                                            '"dst_block": "blocks_throttle2_0", '
                                            '"dst_port": 0'
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )

        self.assertIsNone(content)
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(
            tool_calls[0].arguments,
            {
                "transaction": {
                    "op_type": "remove_connection",
                    "src_block": "analog_random_source_x_0",
                    "src_port": 0,
                    "dst_block": "blocks_throttle2_0",
                    "dst_port": 0,
                }
            },
        )

    def test_parse_assistant_message_preserves_unrepairable_native_tool_name(self) -> None:
        client = self._client("http://127.0.0.1:1")
        content, tool_calls = client.parse_assistant_message(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "apply_edit",
                                        "arguments": "{not valid json",
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )

        self.assertIsNone(content)
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0].name, "apply_edit")
        self.assertEqual(
            tool_calls[0].arguments,
            {"__invalid_json_arguments__": "{not valid json"},
        )










    def test_assistant_text_that_looks_like_rewire_does_not_mutate(self) -> None:
        llama_config = self._llama_config()
        server = self._start_server(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": (
                                    "rewire connection_id "
                                    "blocks_throttle2_0:0->blocks_char_to_float_0:0 "
                                    "to analog_random_source_x_0:0->blocks_char_to_float_0:0"
                                ),
                            }
                        }
                    ]
                }
            ],
            model_id=llama_config.model,
        )
        agent, session = self._load_agent()
        before_revision = session.state_revision
        before_connections = list(session.flowgraph.connections)
        client = self._client(self._server_url(server))
        client.require_ready()

        result = run_bounded_llama_turn(
            agent,
            client,
            "Say hello.",
            model=llama_config.model,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool_calls_executed"], 0)
        self.assertEqual(session.state_revision, before_revision)
        self.assertEqual(session.flowgraph.connections, before_connections)




    def test_failed_apply_edit_stops_the_current_tool_batch(self) -> None:
        agent = GrcAgent()
        self.assertTrue(
            agent.should_stop_batch_after_result(
                "apply_edit",
                {"ok": False, "error_type": "preflight_rejected"},
            )
        )
        self.assertFalse(
            agent.should_stop_batch_after_result(
                "propose_edit",
                {"ok": False, "error_type": "tool_call_invalid"},
            )
        )












    def test_cli_llama_runtime_uses_adapter_path(self) -> None:
        config = load_app_config()
        llama_config = config.llama
        server = self._start_server(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "name": "inspect_graph",
                                        "arguments": json.dumps({"view": "overview", "targets": [], "params": []}),
                                    }
                                ],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "The graph has 5 blocks and 3 connections.",
                            }
                        }
                    ]
                },
            ],
            model_id=llama_config.model,
        )
        output = StringIO()

        with redirect_stdout(output):
            exit_code = _run_llama_runtime(
                str(self._fixture_path()),
                "Summarize the graph.",
                config,
                self._server_url(server),
                llama_config.model,
                None,
            )

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn(f"Using model {llama_config.model}", rendered)
        self.assertIn("inspect_graph: ok", rendered)
        chat_requests = [
            request
            for request in server.requests_seen
            if request["path"] == "/v1/chat/completions"
        ]
        self.assertGreaterEqual(len(chat_requests), 1)
        first_tools = {
            schema["function"]["name"]
            for schema in chat_requests[0]["payload"]["tools"]
        }
        self.assertEqual(first_tools, set(MVP_MODEL_TOOL_NAMES))

    def test_health_command_reports_not_ready_when_llama_unreachable(self) -> None:
        config = load_app_config()
        config = replace(
            config,
            llama=replace(config.llama, server_url="http://127.0.0.1:1"),
        )
        output = StringIO()

        with mock.patch(
            "grc_agent.cli.initialize_retrieval",
            return_value={
                "ok": True,
                "catalog_root": "/tmp/catalog",
                "catalog_files": 1,
            },
        ), redirect_stdout(output):
            exit_code = _run_health_command(config)

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["status"], "not_ready")
        self.assertFalse(payload["llama_model_ready"])
        self.assertFalse(payload["llama_context_verified"])
        self.assertIn("llama_unreachable", payload["status_reasons"])

    def test_health_command_reports_ok_when_llama_context_verified(self) -> None:
        config = load_app_config()
        server = self._start_server(
            [],
            model_id=config.llama.model,
            props_payload={
                "default_generation_settings": {
                    "n_ctx": config.llama.desired_context_tokens
                }
            },
        )
        config = replace(
            config,
            llama=replace(config.llama, server_url=self._server_url(server)),
        )
        output = StringIO()

        with mock.patch(
            "grc_agent.cli.initialize_retrieval",
            return_value={
                "ok": True,
                "catalog_root": "/tmp/catalog",
                "catalog_files": 1,
            },
        ), redirect_stdout(output):
            exit_code = _run_health_command(config)

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["llama_model_ready"])
        self.assertTrue(payload["llama_context_verified"])
        self.assertEqual(payload["status_reasons"], [])


    def test_cli_llama_runtime_strips_leading_control_tokens(self) -> None:
        config = load_app_config()
        llama_config = config.llama
        server = self._start_server(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "name": "inspect_graph",
                                        "arguments": json.dumps({"view": "overview", "targets": [], "params": []}),
                                    }
                                ],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "<eos><eos><eos>The graph has 5 blocks and 3 connections.",
                            }
                        }
                    ]
                },
            ],
            model_id=llama_config.model,
        )
        output = StringIO()

        with redirect_stdout(output):
            exit_code = _run_llama_runtime(
                str(self._fixture_path()),
                "Summarize the graph.",
                config,
                self._server_url(server),
                llama_config.model,
                None,
            )

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("inspect_graph: ok", rendered)
        self.assertNotIn("<eos>", rendered)

        chat_requests = [
            request
            for request in server.requests_seen
            if request["path"] == "/v1/chat/completions"
        ]
        self.assertEqual(chat_requests[0]["payload"]["model"], llama_config.model)

    def test_cli_llama_runtime_reports_adapter_failures_cleanly(self) -> None:
        config = load_app_config()
        output = StringIO()
        server = self._start_server([], model_id="unexpected-alias")

        with redirect_stdout(output):
            exit_code = _run_llama_runtime(
                str(self._fixture_path()),
                "Summarize the graph.",
                config,
                self._server_url(server),
                config.llama.model,
                None,
            )

        rendered = output.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("--- Launcher ---", rendered)
        self.assertIn("alias mismatch", rendered)
        self.assertNotIn("Traceback", rendered)

    def test_cli_llama_runtime_feeds_validation_errors_back(self) -> None:
        config = load_app_config()
        output = StringIO()
        server = self._start_server(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "name": "search_blocks",
                                        "arguments": json.dumps(
                                            {
                                                "query": "samp_rate",
                                                "unexpected": True,
                                            }
                                        ),
                                    }
                                ],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Validation error received.",
                            }
                        }
                    ]
                },
            ],
            model_id=config.llama.model,
        )

        with redirect_stdout(output):
            exit_code = _run_llama_runtime(
                str(self._fixture_path()),
                "Search the current graph for samp_rate.",
                config,
                self._server_url(server),
                config.llama.model,
                None,
            )

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Validation error received.", rendered)
        self.assertIn("FAILED", rendered)
        self.assertNotIn("Traceback", rendered)


    def test_mvp_recovery_retry_intersection_does_not_reexpose_legacy_tools(self) -> None:
        llama_config = self._llama_config()
        server = self._start_server(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "name": "apply_edit",
                                        "arguments": json.dumps(
                                            {
                                                "transaction": {
                                                    "op_type": "update_params",
                                                    "instance_name": "samp_rate",
                                                    "params": {"value": "48000"},
                                                }
                                            }
                                        ),
                                    }
                                ],
                            }
                        }
                    ]
                }
            ],
            model_id=llama_config.model,
        )
        agent, session = self._load_agent()
        before_revision = session.state_revision
        client = self._client(self._server_url(server))
        client.require_ready()

        with mock.patch(
            "grc_agent.llama_server.classify_tool_result_for_recovery",
            return_value=RecoveryDecision(
                recovery_class=RECOVERABLE_MISSING_ARGUMENTS,
                recoverable=True,
                allowed_tools=("summarize_graph", "apply_edit"),
                max_mutation_retries=1,
                prompt="Retry once with corrected args.",
                reason="forced test recovery path",
            ),
        ):
            result = run_bounded_llama_turn(
                agent,
                client,
                "Set samp_rate.",
                model=llama_config.model,
                mvp_tool_profile=True,
            )

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["tool_calls_executed"], 0)
        self.assertEqual(session.state_revision, before_revision)
        self.assertIn("not allowed for this turn", result["assistant_text"])
        chat_requests = [
            request
            for request in server.requests_seen
            if request["path"] == "/v1/chat/completions"
        ]
        self.assertEqual(len(chat_requests), 1)

    def test_mvp_fallback_text_parser_does_not_execute_apply_edit(self) -> None:
        llama_config = self._llama_config()
        server = self._start_server(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": (
                                    'apply_edit(transaction={"op_type":"update_params",'
                                    '"instance_name":"samp_rate","params":{"value":"48000"}})'
                                ),
                            }
                        }
                    ]
                }
            ],
            model_id=llama_config.model,
        )
        agent, session = self._load_agent()
        before_revision = session.state_revision
        client = self._client(self._server_url(server))
        client.require_ready()

        result = run_bounded_llama_turn(
            agent,
            client,
            "Set samp_rate to 48000.",
            model=llama_config.model,
            mvp_tool_profile=True,
            track_turn_requirements=False,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool_calls_executed"], 0)
        self.assertEqual(session.state_revision, before_revision)
        tool_entries = [turn for turn in agent.history if turn.get("role") == "tool"]
        self.assertEqual(tool_entries, [])

    def test_mvp_fallback_text_parser_does_not_execute_legacy_mutation_tools(self) -> None:
        llama_config = self._llama_config()
        server = self._start_server(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": (
                                    "remove_connection(connection_id='a:0->b:0')\n"
                                    "rewire_connection(old_connection_id='a:0->b:0', new_src_block='a', "
                                    "new_src_port=0, new_dst_block='c', new_dst_port=0)\n"
                                    "save_graph()"
                                ),
                            }
                        }
                    ]
                }
            ],
            model_id=llama_config.model,
        )
        agent, session = self._load_agent()
        before_revision = session.state_revision
        client = self._client(self._server_url(server))
        client.require_ready()

        result = run_bounded_llama_turn(
            agent,
            client,
            "Disconnect and save graph.",
            model=llama_config.model,
            mvp_tool_profile=True,
            track_turn_requirements=False,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool_calls_executed"], 0)
        self.assertEqual(session.state_revision, before_revision)
        tool_entries = [turn for turn in agent.history if turn.get("role") == "tool"]
        self.assertEqual(tool_entries, [])
class LooksLikeToolCallTextTests(unittest.TestCase):
    """Unit tests for the _looks_like_tool_call_text helper."""

    def test_empty_string_returns_false(self) -> None:
        self.assertFalse(_looks_like_tool_call_text(""))

    def test_whitespace_only_returns_false(self) -> None:
        self.assertFalse(_looks_like_tool_call_text("   \n"))

    def test_plain_text_returns_false(self) -> None:
        self.assertFalse(_looks_like_tool_call_text("The samp_rate is 32000."))

    def test_function_call_stub_returns_true(self) -> None:
        self.assertTrue(_looks_like_tool_call_text("validate_graph{}"))

    def test_function_call_with_parens_returns_true(self) -> None:
        self.assertTrue(_looks_like_tool_call_text("validate_graph()"))

    def test_json_with_name_key_returns_true(self) -> None:
        self.assertTrue(
            _looks_like_tool_call_text(json.dumps({"name": "validate_graph", "arguments": {}}))
        )

    def test_json_with_function_key_returns_true(self) -> None:
        self.assertTrue(
            _looks_like_tool_call_text(
                json.dumps({"function": {"name": "validate_graph"}, "id": "c1"})
            )
        )

    def test_json_without_name_or_function_key_returns_false(self) -> None:
        self.assertFalse(_looks_like_tool_call_text(json.dumps({"key": "value"})))

    def test_json_array_returns_false(self) -> None:
        self.assertFalse(_looks_like_tool_call_text(json.dumps([1, 2, 3])))


if __name__ == "__main__":
    unittest.main()
