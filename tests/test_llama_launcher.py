"""Tests for the local llama.cpp launcher lifecycle layer."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import urlparse

from grc_agent.config import LlamaConfig
from grc_agent.llama_launcher import LlamaLauncherError, LlamaServerLauncher

from tests.llama_launcher_support import (
    reserve_free_port,
    terminate_pid,
    write_stub_llama_server,
)


class LlamaServerLauncherTests(unittest.TestCase):
    """Exercise the real subprocess launcher path with a PATH-local stub binary."""

    def _config(self, port: int, model: str = "test-llama-model") -> LlamaConfig:
        return LlamaConfig(
            server_url=f"http://127.0.0.1:{port}",
            model=model,
            hf_model="stub/model:Q4_K_M",
            desired_context_tokens=120000,
            startup_timeout_seconds=5.0,
            max_tokens=256,
            max_tool_rounds=8,
            temperature=0.0,
            enable_thinking=False,
            request_timeout_seconds=2.0,
        )

    def _start_external_stub_server(
        self, temp_path: Path, config: LlamaConfig
    ) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [
                str(temp_path / "llama-server"),
                "-hf",
                config.hf_model,
                "--alias",
                config.model,
                "--host",
                "127.0.0.1",
                "--port",
                str(urlparse(config.server_url).port),
                "--jinja",
                "--no-mmproj",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.addCleanup(process.wait, 5)
        self.addCleanup(terminate_pid, process.pid)
        deadline = time.monotonic() + 5.0
        port = urlparse(config.server_url).port
        assert port is not None
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return process
            except OSError:
                time.sleep(0.05)
        self.fail("Stub llama-server did not bind the expected launcher test port.")

    def test_launcher_starts_server_from_closed_port_and_reuses_it(self) -> None:
        port = reserve_free_port()
        config = self._config(port)

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            write_stub_llama_server(temp_path)
            launcher = LlamaServerLauncher(
                config,
                state_path=temp_path / "launcher-state.json",
                log_dir=temp_path / "logs",
            )
            env = {
                "PATH": f"{temp_path}:{os.environ['PATH']}",
                "GRC_AGENT_TEST_LAUNCH_DELAY": "0.2",
            }

            with mock.patch.dict(os.environ, env, clear=False):
                first = launcher.ensure_server_ready()
                self.addCleanup(terminate_pid, first.pid)
                second = launcher.ensure_server_ready()

            self.assertEqual(first.status, "started")
            self.assertIsInstance(first.pid, int)
            self.assertEqual(second.status, "reused")
            self.assertEqual(second.pid, first.pid)

    def test_launcher_fails_with_explicit_alias_mismatch(self) -> None:
        port = reserve_free_port()
        config = self._config(port, model="expected-alias")

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            write_stub_llama_server(temp_path)
            launcher = LlamaServerLauncher(
                config,
                state_path=temp_path / "launcher-state.json",
                log_dir=temp_path / "logs",
            )
            env = {
                "PATH": f"{temp_path}:{os.environ['PATH']}",
                "GRC_AGENT_TEST_LAUNCH_ALIAS": "wrong-alias",
            }

            with mock.patch.dict(os.environ, env, clear=False):
                with self.assertRaisesRegex(LlamaLauncherError, "alias mismatch"):
                    launcher.ensure_server_ready()

    def test_launcher_fails_when_process_exits_before_readiness(self) -> None:
        port = reserve_free_port()
        config = self._config(port)

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            write_stub_llama_server(temp_path)
            launcher = LlamaServerLauncher(
                config,
                state_path=temp_path / "launcher-state.json",
                log_dir=temp_path / "logs",
            )
            env = {
                "PATH": f"{temp_path}:{os.environ['PATH']}",
                "GRC_AGENT_TEST_LAUNCH_MODE": "exit-immediately",
            }

            with mock.patch.dict(os.environ, env, clear=False):
                with self.assertRaisesRegex(
                    LlamaLauncherError, "exited before readiness"
                ):
                    launcher.ensure_server_ready()

    def test_launcher_clears_stale_state_before_reusing_healthy_backend(self) -> None:
        port = reserve_free_port()
        config = self._config(port)

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            write_stub_llama_server(temp_path)
            state_path = temp_path / "launcher-state.json"
            launcher = LlamaServerLauncher(
                config,
                state_path=state_path,
                log_dir=temp_path / "logs",
            )
            env = {
                "PATH": f"{temp_path}:{os.environ['PATH']}",
            }

            with mock.patch.dict(os.environ, env, clear=False):
                self._start_external_stub_server(temp_path, config)
                state_path.write_text(
                    json.dumps(
                        {
                            "base_url": config.server_url,
                            "model_alias": config.model,
                            "hf_model": config.hf_model,
                            "pid": 999999,
                            "log_path": str(temp_path / "logs" / "stale.log"),
                        }
                    ),
                    encoding="utf-8",
                )

                result = launcher.ensure_server_ready()

            self.assertEqual(result.status, "reused")
            self.assertIsNone(result.pid)
            self.assertFalse(state_path.exists())

    def test_launcher_clears_malformed_state_before_reusing_healthy_backend(
        self,
    ) -> None:
        port = reserve_free_port()
        config = self._config(port)

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            write_stub_llama_server(temp_path)
            state_path = temp_path / "launcher-state.json"
            launcher = LlamaServerLauncher(
                config,
                state_path=state_path,
                log_dir=temp_path / "logs",
            )
            env = {
                "PATH": f"{temp_path}:{os.environ['PATH']}",
            }

            with mock.patch.dict(os.environ, env, clear=False):
                self._start_external_stub_server(temp_path, config)
                state_path.write_text("{not-json", encoding="utf-8")

                result = launcher.ensure_server_ready()

            self.assertEqual(result.status, "reused")
            self.assertIsNone(result.pid)
            self.assertFalse(state_path.exists())

    def test_launcher_clears_mismatched_process_state_before_reuse(self) -> None:
        port = reserve_free_port()
        config = self._config(port)

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            write_stub_llama_server(temp_path)
            state_path = temp_path / "launcher-state.json"
            launcher = LlamaServerLauncher(
                config,
                state_path=state_path,
                log_dir=temp_path / "logs",
            )
            env = {
                "PATH": f"{temp_path}:{os.environ['PATH']}",
            }

            with mock.patch.dict(os.environ, env, clear=False):
                self._start_external_stub_server(temp_path, config)
                mismatched_process = subprocess.Popen(
                    ["sleep", "30"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self.addCleanup(mismatched_process.wait, 5)
                self.addCleanup(terminate_pid, mismatched_process.pid)
                state_path.write_text(
                    json.dumps(
                        {
                            "base_url": config.server_url,
                            "model_alias": config.model,
                            "hf_model": config.hf_model,
                            "pid": mismatched_process.pid,
                            "log_path": str(temp_path / "logs" / "mismatch.log"),
                        }
                    ),
                    encoding="utf-8",
                )

                result = launcher.ensure_server_ready()

            self.assertEqual(result.status, "reused")
            self.assertIsNone(result.pid)
            self.assertFalse(state_path.exists())
            self.assertIsNone(mismatched_process.poll())
