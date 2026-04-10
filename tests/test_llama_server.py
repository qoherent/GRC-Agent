"""Tests for the thin llama.cpp adapter over the narrowed runtime surface."""

from contextlib import redirect_stdout
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
from grc_agent.cli import _run_llama_runtime
from grc_agent.config import load_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import LlamaServerClient, LlamaServerError, run_bounded_llama_turn


class _ScriptedLlamaServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        responses: list[dict[str, object]],
        *,
        model_id: str = "test-llama-model",
        models_payload: dict[str, object] | None = None,
    ) -> None:
        super().__init__(server_address, _ScriptedLlamaHandler)
        self.responses = list(responses)
        self.requests_seen: list[dict[str, object]] = []
        self.model_id = model_id
        self.models_payload = models_payload


class _ScriptedLlamaHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server = self.server
        assert isinstance(server, _ScriptedLlamaServer)
        server.requests_seen.append({"method": "GET", "path": self.path, "payload": None})

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
        server.requests_seen.append({"method": "POST", "path": self.path, "payload": payload})

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

    def _start_server(
        self,
        responses: list[dict[str, object]],
        *,
        model_id: str = "test-llama-model",
        models_payload: dict[str, object] | None = None,
    ) -> _ScriptedLlamaServer:
        server = _ScriptedLlamaServer(
            ("127.0.0.1", 0),
            responses,
            model_id=model_id,
            models_payload=models_payload,
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

    def test_bounded_llama_turn_executes_serial_tool_batch(self) -> None:
        llama_config = self._llama_config()
        server = self._start_server(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "I will update the variable and validate the graph.",
                                "tool_calls": [
                                    {
                                        "id": "call_set_variable",
                                        "type": "function",
                                        "function": {
                                            "name": "set_variable",
                                            "arguments": json.dumps(
                                                {
                                                    "instance_name": "samp_rate",
                                                    "value": "48000",
                                                }
                                            ),
                                        },
                                    },
                                    {
                                        "name": "validate_graph",
                                        "arguments": {},
                                    },
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
                                "content": "The graph is valid with samp_rate set to 48000.",
                            }
                        }
                    ]
                },
            ],
            model_id=llama_config.model,
        )
        agent, session = self._load_agent()
        client = self._client(self._server_url(server))
        client.require_ready()

        result = run_bounded_llama_turn(
            agent,
            client,
            "Please change the samp_rate to 48000 and validate the graph.",
            model=llama_config.model,
            max_steps=2,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], llama_config.model)
        self.assertEqual(result["tool_calls_executed"], 2)
        self.assertEqual(result["tool_rounds_used"], 1)
        self.assertEqual(
            result["assistant_text"],
            "Set samp_rate to 48000 and validated the graph successfully.",
        )

        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None
        variable_block = next(
            block for block in flowgraph.blocks if block.instance_name == "samp_rate"
        )
        self.assertEqual(variable_block.params["parameters"]["value"], "48000")

        tool_entries = [turn for turn in agent.history if turn.get("role") == "tool"]
        self.assertEqual([entry["name"] for entry in tool_entries], ["set_variable", "validate_graph"])
        self.assertTrue(tool_entries[1]["content"]["valid"])

        model_requests = [
            request
            for request in server.requests_seen
            if request["method"] == "GET" and request["path"] == "/v1/models"
        ]
        self.assertEqual(len(model_requests), 1)

        chat_requests = [
            request
            for request in server.requests_seen
            if request["path"] == "/v1/chat/completions"
        ]
        self.assertEqual(len(chat_requests), 2)
        first_payload = chat_requests[0]["payload"]
        self.assertEqual(first_payload["model"], llama_config.model)
        self.assertFalse(first_payload["parallel_tool_calls"])
        self.assertTrue(first_payload["parse_tool_calls"])
        self.assertEqual(first_payload["max_tokens"], llama_config.max_tokens)
        self.assertEqual(first_payload["temperature"], llama_config.temperature)
        self.assertEqual(
            first_payload["chat_template_kwargs"],
            {"enable_thinking": llama_config.enable_thinking},
        )
        self.assertEqual(
            {
                schema["function"]["name"]
                for schema in first_payload["tools"]
            },
            {"summarize_graph", "set_variable", "validate_graph", "save_graph"},
        )
        self.assertTrue(
            any(
                message.get("role") == "tool" and message.get("name") == "validate_graph"
                for message in chat_requests[1]["payload"]["messages"]
            )
        )

    def test_bounded_llama_turn_fails_when_alias_mismatches(self) -> None:
        server = self._start_server([], model_id="server-side-model")
        agent, _session = self._load_agent()
        client = self._client(self._server_url(server))
        client.require_ready()

        with self.assertRaisesRegex(LlamaServerError, "alias mismatch"):
            run_bounded_llama_turn(
                agent,
                client,
                "Summarize the graph.",
                model="configured-model",
                max_steps=2,
            )

        chat_requests = [
            request
            for request in server.requests_seen
            if request["path"] == "/v1/chat/completions"
        ]
        self.assertEqual(chat_requests, [])

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

    def test_bounded_llama_turn_allows_two_tool_rounds_plus_final_answer(self) -> None:
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
                                        "name": "set_variable",
                                        "arguments": json.dumps(
                                            {
                                                "instance_name": "samp_rate",
                                                "value": "48000",
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
                                "tool_calls": [{"name": "validate_graph", "arguments": "{}"}],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "The graph is valid with samp_rate set to 48000.",
                            }
                        }
                    ]
                }
            ],
            model_id=llama_config.model,
        )
        agent, _session = self._load_agent()
        client = self._client(self._server_url(server))
        client.require_ready()

        result = run_bounded_llama_turn(
            agent,
            client,
            "Change the samp_rate variable to 48000 and validate the graph.",
            model=llama_config.model,
            max_steps=2,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool_rounds_used"], 2)
        self.assertEqual(result["tool_calls_executed"], 2)
        self.assertEqual(
            result["assistant_text"],
            "Set samp_rate to 48000 and validated the graph successfully.",
        )

    def test_bounded_llama_turn_reports_tool_round_limit(self) -> None:
        llama_config = self._llama_config()
        server = self._start_server(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [{"name": "summarize_graph", "arguments": "{}"}],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [{"name": "summarize_graph", "arguments": "{}"}],
                            }
                        }
                    ]
                },
            ],
            model_id=llama_config.model,
        )
        agent, _session = self._load_agent()
        client = self._client(self._server_url(server))
        client.require_ready()

        result = run_bounded_llama_turn(
            agent,
            client,
            "Summarize the graph.",
            model=llama_config.model,
            max_steps=1,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["tool_rounds_used"], 1)
        self.assertEqual(
            result["message"],
            "Tool-round limit reached before the model produced a final answer.",
        )
        self.assertEqual(result["tool_calls_executed"], 1)

    def test_bounded_llama_turn_uses_summary_tool_payload_as_final_text(self) -> None:
        llama_config = self._llama_config()
        server = self._start_server(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [{"name": "summarize_graph", "arguments": "{}"}],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "The graph contains 5 blocks and 3 connections.",
                            }
                        }
                    ]
                },
            ],
            model_id=llama_config.model,
        )
        agent, _session = self._load_agent()
        client = self._client(self._server_url(server))
        client.require_ready()

        result = run_bounded_llama_turn(
            agent,
            client,
            "Summarize the graph.",
            model=llama_config.model,
            max_steps=2,
        )

        tool_entry = next(turn for turn in agent.history if turn.get("role") == "tool")
        self.assertEqual(result["assistant_text"], tool_entry["content"]["summary"])
        self.assertEqual(agent.history[-1]["content"], tool_entry["content"]["summary"])

    def test_bounded_llama_turn_finalizes_set_and_validate_from_tool_results(self) -> None:
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
                                        "name": "set_variable",
                                        "arguments": json.dumps(
                                            {"instance_name": "samp_rate", "value": 48000}
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
                                "tool_calls": [{"name": "validate_graph", "arguments": "{}"}],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "The graph has been validated successfully.",
                            }
                        }
                    ]
                },
            ],
            model_id=llama_config.model,
        )
        agent, _session = self._load_agent()
        client = self._client(self._server_url(server))
        client.require_ready()

        result = run_bounded_llama_turn(
            agent,
            client,
            "Change the samp_rate variable to 48000 and validate the graph.",
            model=llama_config.model,
            max_steps=2,
        )

        self.assertEqual(
            result["assistant_text"],
            "Set samp_rate to 48000 and validated the graph successfully.",
        )

    def test_bounded_llama_turn_finalizes_missing_variable_recovery_from_tool_results(self) -> None:
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
                                        "name": "set_variable",
                                        "arguments": json.dumps(
                                            {"instance_name": "does_not_exist", "value": 123}
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
                                "tool_calls": [{"name": "validate_graph", "arguments": "{}"}],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "The graph is valid.",
                            }
                        }
                    ]
                },
            ],
            model_id=llama_config.model,
        )
        agent, _session = self._load_agent()
        client = self._client(self._server_url(server))
        client.require_ready()

        result = run_bounded_llama_turn(
            agent,
            client,
            "Set the variable does_not_exist to 123 and validate the graph.",
            model=llama_config.model,
            max_steps=2,
        )

        self.assertEqual(
            result["assistant_text"],
            "Could not set the requested variable: Variable block not found: does_not_exist. "
            "The graph validated successfully.",
        )

    def test_bounded_llama_turn_blocks_raw_tool_call_text_without_executed_tools(self) -> None:
        llama_config = self._llama_config()
        server = self._start_server(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "summarize_graph{}",
                            }
                        }
                    ]
                }
            ],
            model_id=llama_config.model,
        )
        agent, _session = self._load_agent()
        client = self._client(self._server_url(server))
        client.require_ready()

        result = run_bounded_llama_turn(
            agent,
            client,
            "Add a throttle block and connect it correctly.",
            model=llama_config.model,
            max_steps=2,
        )

        self.assertEqual(
            result["assistant_text"],
            "I could not complete that request with the available tools.",
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
                                "tool_calls": [{"name": "summarize_graph", "arguments": "{}"}],
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
                llama_config.max_steps,
            )

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn(f"Using model {llama_config.model}", rendered)
        self.assertIn("File: random_bit_generator.grc", rendered)
        self.assertIn("Connections: 3", rendered)
        self.assertIn("summarize_graph", rendered)

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
                                "tool_calls": [{"name": "summarize_graph", "arguments": "{}"}],
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
                llama_config.max_steps,
            )

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("File: random_bit_generator.grc", rendered)
        self.assertIn("Connections: 3", rendered)
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
                config.llama.max_steps,
            )

        rendered = output.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("--- Runtime ---", rendered)
        self.assertIn("alias mismatch", rendered)
        self.assertNotIn("Traceback", rendered)


if __name__ == "__main__":
    unittest.main()
