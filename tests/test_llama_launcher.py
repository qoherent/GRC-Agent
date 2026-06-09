"""Tests for the local llama.cpp launcher lifecycle layer."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
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
            model_path=None,
            device="CUDA0",
            gpu_layers=128,
            desired_context_tokens=120000,
            startup_timeout_seconds=5.0,
            max_tokens=256,
            max_tool_rounds=8,
            temperature=0.0,
            enable_thinking=False,
            request_timeout_seconds=2.0,
            log_retention_days=7,
            models_dir=None,
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
                "--ctx-size",
                str(config.desired_context_tokens),
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
            assert first.pid is not None
            cmdline = Path(f"/proc/{first.pid}/cmdline").read_bytes().decode(
                "utf-8",
                errors="ignore",
            )
            self.assertIn("--device\x00CUDA0", cmdline)
            self.assertIn("--gpu-layers\x00128", cmdline)
            self.assertEqual(second.status, "reused")
            self.assertEqual(second.pid, first.pid)

    def test_launcher_uses_local_model_path_when_configured(self) -> None:
        port = reserve_free_port()
        config = LlamaConfig(
            server_url=f"http://127.0.0.1:{port}",
            model="test-llama-model",
            hf_model="stub/model:Q4_K_M",
            model_path="/tmp/text-only-model.gguf",
            device="CUDA0",
            gpu_layers=999,
            desired_context_tokens=120000,
            startup_timeout_seconds=5.0,
            max_tokens=256,
            max_tool_rounds=8,
            temperature=0.0,
            enable_thinking=False,
            request_timeout_seconds=2.0,
            log_retention_days=7,
            models_dir=None,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            write_stub_llama_server(temp_path)
            launcher = LlamaServerLauncher(
                config,
                state_path=temp_path / "launcher-state.json",
                log_dir=temp_path / "logs",
            )
            env = {"PATH": f"{temp_path}:{os.environ['PATH']}"}

            with mock.patch.dict(os.environ, env, clear=False):
                result = launcher.ensure_server_ready()
                self.addCleanup(terminate_pid, result.pid)

            assert result.pid is not None
            cmdline = Path(f"/proc/{result.pid}/cmdline").read_bytes().decode(
                "utf-8",
                errors="ignore",
            )
            self.assertIn("-m\x00/tmp/text-only-model.gguf", cmdline)
            self.assertNotIn("-hf\x00stub/model:Q4_K_M", cmdline)

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


class SwapModelTests(unittest.TestCase):
    """Phase 3: ``LlamaServerLauncher.swap_model`` builds a new config
    and delegates to ``_ensure_server_ready_unlocked`` on a fresh launcher.

    These tests stub the underlying ``_ensure_server_ready_unlocked`` so no real
    ``llama-server`` is spawned. The contract under test is the
    *plumbing*: config is replaced, a new launcher is built, the
    new launcher is the one that produces the result.
    """

    def _config(self, port: int) -> LlamaConfig:
        return LlamaConfig(
            server_url=f"http://127.0.0.1:{port}",
            model="old-model.gguf",
            hf_model="old/repo:old-model.gguf",
            model_path=None,
            device="CUDA0",
            gpu_layers=128,
            desired_context_tokens=120000,
            startup_timeout_seconds=5.0,
            max_tokens=256,
            max_tool_rounds=8,
            temperature=0.0,
            enable_thinking=False,
            request_timeout_seconds=2.0,
            log_retention_days=7,
            models_dir=None,
        )

    def test_swap_replaces_config_and_returns_new_result(self) -> None:
        port = reserve_free_port()
        config = self._config(port)
        old_launcher = LlamaServerLauncher(config)

        fake_result = mock.MagicMock()
        fake_result.model_alias = "new-model.gguf"
        fake_result.status = "started"
        fake_result.server_url = f"http://127.0.0.1:{port}"
        fake_result.provider_config = mock.MagicMock()
        fake_result.health_evidence = {
            "llama_model_ready": True,
        }

        captured: list[LlamaConfig] = []

        def _fake_ensure_server_ready_unlocked(self):  # type: ignore[no-untyped-def]
            captured.append(self.config)
            return fake_result

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            logs = Path(tmp) / "logs"
            with mock.patch.object(
                LlamaServerLauncher,
                "_ensure_server_ready_unlocked",
                _fake_ensure_server_ready_unlocked,
            ), mock.patch.object(
                old_launcher, "state_path", state
            ), mock.patch.object(
                old_launcher, "log_dir", logs
            ):
                result = old_launcher.swap_model(
                    new_hf_repo="new/repo",
                    new_filename="new-model.gguf",
                )
        self.assertIs(result, fake_result)
        self.assertEqual(len(captured), 1)
        new_config = captured[0]
        # Old model fields must be gone; new ones must be present.
        self.assertEqual(new_config.model, "new-model.gguf")
        self.assertEqual(new_config.hf_model, "new/repo:new-model.gguf")
        self.assertIsNone(new_config.model_path)
        # Server URL, device, context window, and timeout must survive.
        self.assertEqual(new_config.server_url, config.server_url)
        self.assertEqual(new_config.device, config.device)
        self.assertEqual(
            new_config.desired_context_tokens,
            config.desired_context_tokens,
        )
        self.assertEqual(
            new_config.startup_timeout_seconds,
            config.startup_timeout_seconds,
        )

    def test_swap_uses_explicit_alias_when_provided(self) -> None:
        port = reserve_free_port()
        config = self._config(port)
        old_launcher = LlamaServerLauncher(config)
        fake_result = mock.MagicMock()
        fake_result.model_alias = "custom-alias"
        fake_result.status = "started"
        fake_result.server_url = f"http://127.0.0.1:{port}"
        fake_result.provider_config = mock.MagicMock()
        fake_result.health_evidence = {}

        captured: list[LlamaConfig] = []

        def _fake_ensure_server_ready_unlocked(self):  # type: ignore[no-untyped-def]
            captured.append(self.config)
            return fake_result

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                LlamaServerLauncher,
                "_ensure_server_ready_unlocked",
                _fake_ensure_server_ready_unlocked,
            ), mock.patch.object(
                old_launcher, "state_path", Path(tmp) / "state.json"
            ), mock.patch.object(
                old_launcher, "log_dir", Path(tmp) / "logs"
            ):
                result = old_launcher.swap_model(
                    new_hf_repo="new/repo",
                    new_filename="new-model.gguf",
                    new_alias="custom-alias",
                )
        self.assertIs(result, fake_result)
        self.assertEqual(captured[0].model, "custom-alias")
        self.assertEqual(captured[0].hf_model, "new/repo:new-model.gguf")

    def test_swap_propagates_launcher_error(self) -> None:
        port = reserve_free_port()
        config = self._config(port)
        old_launcher = LlamaServerLauncher(config)

        def _explode(self):  # type: ignore[no-untyped-def]
            raise LlamaLauncherError("simulated timeout")

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                LlamaServerLauncher,
                "_ensure_server_ready_unlocked",
                _explode,
            ), mock.patch.object(
                old_launcher, "state_path", Path(tmp) / "state.json"
            ), mock.patch.object(
                old_launcher, "log_dir", Path(tmp) / "logs"
            ):
                with self.assertRaises(LlamaLauncherError) as ctx:
                    old_launcher.swap_model(
                        new_hf_repo="new/repo",
                        new_filename="new-model.gguf",
                    )
        self.assertIn("simulated timeout", str(ctx.exception))

    def test_swap_clears_old_cached_state_under_lock(self) -> None:
        """Phase 3 subagent finding F2/F5: the swap must terminate any
        cached backend owned by the OLD launcher so the new launcher
        can bind the port. ``_cleanup_cached_state`` is the
        authoritative reap path; assert the swap calls it on the
        OLD launcher's state.
        """
        port = reserve_free_port()
        config = self._config(port)
        old_launcher = LlamaServerLauncher(config)
        state_path = Path(tempfile.gettempdir()) / "swap_state_test.json"
        if state_path.exists():
            state_path.unlink()

        cleanup_calls: list[Path] = []
        matching_state_calls: list[Path] = []

        def _fake_cleanup(self, state):  # type: ignore[no-untyped-def]
            cleanup_calls.append(self.state_path)
            return None

        def _fake_prepare_matching_state(self):  # type: ignore[no-untyped-def]
            matching_state_calls.append(self.state_path)
            return None

        def _fake_ensure_server_ready_unlocked(self):  # type: ignore[no-untyped-def]
            return mock.MagicMock(
                model_alias="new-model.gguf",
                status="started",
                server_url=self.server_url,
                provider_config=mock.MagicMock(),
                health_evidence={},
            )

        with mock.patch.object(
            LlamaServerLauncher,
            "_ensure_server_ready_unlocked",
            _fake_ensure_server_ready_unlocked,
        ), mock.patch.object(
            LlamaServerLauncher,
            "_cleanup_cached_state",
            _fake_cleanup,
        ), mock.patch.object(
            LlamaServerLauncher,
            "_prepare_matching_state",
            _fake_prepare_matching_state,
        ), mock.patch.object(
            old_launcher, "state_path", state_path
        ), mock.patch.object(
            old_launcher, "log_dir", Path(tempfile.gettempdir()) / "swap_logs"
        ):
            result = old_launcher.swap_model(
                new_hf_repo="new/repo",
                new_filename="new-model.gguf",
            )
        # ``_cleanup_cached_state`` was called exactly once, on the
        # OLD launcher's state path. This is the F2/F5 fix: the old
        # cached backend (if any) is reaped before the new launcher
        # binds the port.
        self.assertEqual(cleanup_calls, [state_path])
        self.assertEqual(matching_state_calls, [state_path])
        self.assertEqual(result.model_alias, "new-model.gguf")
