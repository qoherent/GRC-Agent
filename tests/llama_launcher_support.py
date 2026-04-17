"""Shared helpers for real subprocess-based llama launcher tests."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import socket
import subprocess
import time


_STUB_LLAMA_SERVER = """#!/usr/bin/env python3
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import time


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("-hf")
    parser.add_argument("--alias", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--jinja", action="store_true")
    return parser


class _Server(ThreadingHTTPServer):
    def __init__(self, address, alias):
        super().__init__(address, _Handler)
        self.alias = alias
        self.alias_override = os.environ.get("GRC_AGENT_TEST_LAUNCH_ALIAS")
        self.health_delay = float(os.environ.get("GRC_AGENT_TEST_LAUNCH_DELAY", "0.0"))
        self.start_time = time.monotonic()
        self.chat_count = 0


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        server = self.server
        assert isinstance(server, _Server)

        if self.path == "/health":
            if time.monotonic() - server.start_time < server.health_delay:
                self._write_json(503, {"status": "loading"})
                return
            self._write_json(200, {"status": "ok"})
            return

        if self.path == "/v1/models":
            alias = server.alias_override or server.alias
            self._write_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": alias,
                            "object": "model",
                            "meta": None,
                        }
                    ],
                },
            )
            return

        self._write_json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        server = self.server
        assert isinstance(server, _Server)
        if self.path != "/v1/chat/completions":
            self._write_json(404, {"error": {"message": "not found"}})
            return

        server.chat_count += 1
        if server.chat_count == 1:
            payload = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [{"name": "summarize_graph", "arguments": "{}"}],
                        }
                    }
                ]
            }
        else:
            payload = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "The graph summary is ready.",
                        }
                    }
                ]
            }
        self._write_json(200, payload)

    def log_message(self, format, *args):
        return

    def _write_json(self, status_code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    args = _parser().parse_args()
    if os.environ.get("GRC_AGENT_TEST_LAUNCH_MODE") == "exit-immediately":
        raise SystemExit(9)

    server = _Server((args.host, args.port), args.alias)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
"""


def reserve_free_port() -> int:
    """Reserve one local TCP port for a launcher test server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_stub_llama_server(directory: Path) -> Path:
    """Create a PATH-visible test `llama-server` binary."""
    binary_path = directory / "llama-server"
    binary_path.write_text(_STUB_LLAMA_SERVER, encoding="utf-8")
    binary_path.chmod(0o755)
    return binary_path


def terminate_pid(pid: int | None, timeout_seconds: float = 5.0) -> None:
    """Terminate one spawned launcher test process by pid."""
    if pid is None:
        return
    tracked_processes = None
    tracked_process = None
    try:
        from grc_agent.llama_launcher import _ACTIVE_LAUNCH_PROCESSES

        tracked_processes = _ACTIVE_LAUNCH_PROCESSES
        tracked_process = _ACTIVE_LAUNCH_PROCESSES.get(pid)
    except Exception:
        tracked_processes = None
        tracked_process = None
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        if tracked_process is not None:
            try:
                tracked_process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        if tracked_processes is not None:
            tracked_processes.pop(pid, None)
        return

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            if tracked_process is not None:
                try:
                    tracked_process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
            if tracked_processes is not None:
                tracked_processes.pop(pid, None)
            return
        time.sleep(0.05)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        if tracked_process is not None:
            try:
                tracked_process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        if tracked_processes is not None:
            tracked_processes.pop(pid, None)
        return
    hard_deadline = time.monotonic() + 1.0
    while time.monotonic() < hard_deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    if tracked_process is not None:
        try:
            tracked_process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            pass
    if tracked_processes is not None:
        tracked_processes.pop(pid, None)
