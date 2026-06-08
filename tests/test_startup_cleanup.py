"""Tests for startup cache cleanup for CLI and GUI."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

from grc_agent.config import LlamaConfig
from grc_agent.llama_launcher import LlamaServerLauncher, _mmproj_state
from grc_agent_gui.app import _GUI_TEMP_DIR_MIN_AGE_SECONDS, _prune_orphan_temp_dirs

from tests.llama_launcher_support import (
    reserve_free_port,
    terminate_pid,
    write_stub_llama_server,
)


class StartupCleanupTests(unittest.TestCase):
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
        return process

    def test_launcher_reaps_orphan_pid_with_matching_cmdline(self) -> None:
        port = reserve_free_port()
        config = self._config(port)

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            write_stub_llama_server(temp_path)

            # Start a stub server that runs in the background
            process = self._start_external_stub_server(temp_path, config)

            state_path = temp_path / "launcher-state.json"
            launcher = LlamaServerLauncher(
                config,
                state_path=state_path,
                log_dir=temp_path / "logs",
            )

            # Write a state file pointing to the running stub, but
            # set launcher_pid to a different PID (simulating orphan).
            state_path.write_text(
                json.dumps(
                    {
                        "base_url": config.server_url,
                        "model_alias": config.model,
                        "hf_model": config.hf_model,
                        "pid": process.pid,
                        "log_path": str(temp_path / "logs" / "stale.log"),
                        "launcher_pid": process.pid + 1,  # different from current PID
                    }
                ),
                encoding="utf-8",
            )

            # Verify the process is alive before reaping
            self.assertEqual(process.poll(), None)

            # Reset mmproj support detection state and patch PATH so it detects the stub
            _mmproj_state["supported"] = None
            env = {"PATH": f"{temp_path}:{os.environ['PATH']}"}
            with mock.patch.dict(os.environ, env, clear=False):
                # Reap orphans
                terminated = launcher.reap_orphan_pids()

            # Verify process is terminated and state is cleared
            self.assertIn(process.pid, terminated)
            self.assertFalse(state_path.exists())

            # Give a small window for the process to exit
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertNotEqual(process.poll(), None)

    def test_launcher_does_not_reap_unrelated_llama_server(self) -> None:
        port1 = reserve_free_port()
        port2 = reserve_free_port()

        config1 = self._config(port1, model="model-1")
        config2 = self._config(port2, model="model-2")

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            write_stub_llama_server(temp_path)

            # Start stub server 1
            process1 = self._start_external_stub_server(temp_path, config1)

            state_path = temp_path / "launcher-state.json"

            # Launcher 2 config
            launcher2 = LlamaServerLauncher(
                config2,
                state_path=state_path,
                log_dir=temp_path / "logs",
            )

            # Write a state file pointing to process1
            state_path.write_text(
                json.dumps(
                    {
                        "base_url": config1.server_url,
                        "model_alias": config1.model,
                        "hf_model": config1.hf_model,
                        "pid": process1.pid,
                        "log_path": str(temp_path / "logs" / "stale.log"),
                        "launcher_pid": process1.pid + 1,
                    }
                ),
                encoding="utf-8",
            )

            # Reset mmproj support detection state and patch PATH so it detects the stub
            _mmproj_state["supported"] = None
            env = {"PATH": f"{temp_path}:{os.environ['PATH']}"}
            with mock.patch.dict(os.environ, env, clear=False):
                # Call reap on launcher2 (mismatched config)
                terminated = launcher2.reap_orphan_pids()

            # Verify process1 is not reaped and state is left alone
            self.assertEqual(terminated, [])
            self.assertEqual(process1.poll(), None)
            self.assertTrue(state_path.exists())

    def test_launcher_prunes_logs_older_than_retention_days(self) -> None:
        port = reserve_free_port()
        config = self._config(port)

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            log_dir = temp_path / "logs"
            log_dir.mkdir(parents=True)

            launcher = LlamaServerLauncher(
                config,
                state_path=temp_path / "state.json",
                log_dir=log_dir,
            )

            recent_log = log_dir / "recent.log"
            recent_log.touch()

            old_log = log_dir / "old.log"
            old_log.touch()
            t_old = time.time() - (10 * 86400)
            os.utime(old_log, (t_old, t_old))

            removed = launcher.prune_old_logs(retention_days=7)

            self.assertIn(old_log, removed)
            self.assertNotIn(recent_log, removed)
            self.assertFalse(old_log.exists())
            self.assertTrue(recent_log.exists())

    def test_launcher_log_retention_zero_keeps_forever(self) -> None:
        port = reserve_free_port()
        config = self._config(port)

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            log_dir = temp_path / "logs"
            log_dir.mkdir(parents=True)

            launcher = LlamaServerLauncher(
                config,
                state_path=temp_path / "state.json",
                log_dir=log_dir,
            )

            old_log = log_dir / "old.log"
            old_log.touch()
            t_old = time.time() - (10 * 86400)
            os.utime(old_log, (t_old, t_old))

            removed = launcher.prune_old_logs(retention_days=0)

            self.assertEqual(removed, [])
            self.assertTrue(old_log.exists())

    def test_gui_prune_orphan_temp_dirs_removes_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("tempfile.gettempdir", return_value=tmpdir):
                # Create a stale temp dir
                stale_dir = Path(tmpdir) / "grc_agent_run_stale"
                stale_dir.mkdir()
                t_stale = time.time() - (_GUI_TEMP_DIR_MIN_AGE_SECONDS + 10)
                os.utime(stale_dir, (t_stale, t_stale))

                # Create a recent temp dir
                recent_dir = Path(tmpdir) / "grc_agent_run_recent"
                recent_dir.mkdir()
                t_recent = time.time() - 300
                os.utime(recent_dir, (t_recent, t_recent))

                # Create a non-matching dir
                other_dir = Path(tmpdir) / "other_run_stale"
                other_dir.mkdir()
                os.utime(other_dir, (t_stale, t_stale))

                removed = _prune_orphan_temp_dirs()

                self.assertIn(str(stale_dir), removed)
                self.assertNotIn(str(recent_dir), removed)
                self.assertNotIn(str(other_dir), removed)

                self.assertFalse(stale_dir.exists())
                self.assertTrue(recent_dir.exists())
                self.assertTrue(other_dir.exists())

    def test_gui_prune_orphan_temp_dirs_skips_recent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch("tempfile.gettempdir", return_value=tmpdir):
                # Create a recent temp dir
                recent_dir = Path(tmpdir) / "grc_agent_run_recent"
                recent_dir.mkdir()
                t_recent = time.time() - (_GUI_TEMP_DIR_MIN_AGE_SECONDS - 10)
                os.utime(recent_dir, (t_recent, t_recent))

                removed = _prune_orphan_temp_dirs()

                self.assertNotIn(str(recent_dir), removed)
                self.assertTrue(recent_dir.exists())
