"""Local llama.cpp server startup and readiness management for the CLI."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from shutil import which
from typing import Any, Iterator
from urllib.parse import urlparse

from grc_agent.config import LlamaConfig
from grc_agent.llama_server import LlamaServerClient, LlamaServerError


DEFAULT_STARTUP_POLL_SECONDS = 0.5
_ACTIVE_LAUNCH_PROCESSES: dict[int, subprocess.Popen[Any]] = {}

# Detected once at first use; True if the installed llama.cpp supports --no-mmproj.
_mmproj_state: dict[str, bool | None] = {"supported": None}


def _detect_mmproj_support() -> bool:
    """Check whether the installed llama-server binary supports --no-mmproj."""
    binary = which("llama-server")
    if binary is None:
        return False
    try:
        result = subprocess.run(
            [binary, "-h"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        return "--no-mmproj" in result.stdout or "--no-mmproj" in result.stderr
    except Exception:
        return False


def _get_mmproj_support() -> bool:
    """Return whether the installed llama-server supports --no-mmproj (detected once)."""
    if _mmproj_state["supported"] is None:
        _mmproj_state["supported"] = _detect_mmproj_support()
    return bool(_mmproj_state["supported"])


class LlamaLauncherError(RuntimeError):
    """Raised when the CLI cannot make the llama.cpp backend available."""


@dataclass(frozen=True)
class LlamaLaunchResult:
    """The outcome of ensuring a local llama.cpp server is ready."""

    status: str
    pid: int | None
    server_url: str
    model_alias: str
    client: LlamaServerClient


@dataclass(frozen=True)
class _LauncherState:
    base_url: str
    model_alias: str
    hf_model: str
    pid: int
    log_path: str


class LlamaServerLauncher:
    """Ensure that the configured local llama.cpp backend exists and is ready."""

    def __init__(
        self,
        config: LlamaConfig,
        *,
        server_url: str | None = None,
        model_alias: str | None = None,
        api_key: str | None = None,
        state_path: str | Path | None = None,
        log_dir: str | Path | None = None,
    ) -> None:
        self.config = config
        self.server_url = (server_url or config.server_url).rstrip("/")
        self.model_alias = model_alias or config.model
        self.api_key = api_key
        self.state_path = (
            Path(state_path)
            if state_path is not None
            else Path.home() / ".cache" / "grc_agent" / "llama_launcher_state.json"
        )
        self.lock_path = self.state_path.with_suffix(".lock")
        self.log_dir = (
            Path(log_dir)
            if log_dir is not None
            else Path.home() / ".cache" / "grc_agent" / "llama_launcher_logs"
        )
        self._parsed_url = self._parse_server_url(self.server_url)

    def ensure_server_ready(self) -> LlamaLaunchResult:
        """Reuse a healthy server or launch one locally before the chat turn starts."""
        with self._lock():
            existing_state = self._prepare_matching_state()

            if self._socket_is_open():
                return self._wait_for_existing_backend(existing_state)

            if existing_state is not None:
                return self._wait_for_existing_backend(existing_state)

            process, log_path = self._start_server_process()
            launched_state = _LauncherState(
                base_url=self.server_url,
                model_alias=self.model_alias,
                hf_model=self.config.hf_model,
                pid=process.pid,
                log_path=str(log_path),
            )
            self._write_state(launched_state)
            try:
                launch_result = self._wait_for_ready(
                    launched_process=process,
                    cached_state=launched_state,
                    started=True,
                )
                self._remember_process(process)
                return launch_result
            except Exception:
                self._clear_state()
                self._terminate_process(process)
                raise

    def restart_server_ready(self) -> LlamaLaunchResult:
        """Terminate any matching cached backend and start or reuse a fresh ready backend."""
        with self._lock():
            self._cleanup_cached_state(self._prepare_matching_state())
        return self.ensure_server_ready()

    @contextmanager
    def _lock(self) -> Iterator[None]:
        """Advisory file lock to prevent concurrent CLI startup races."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.lock_path.open("w")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()

    def _wait_for_existing_backend(
        self, cached_state: _LauncherState | None
    ) -> LlamaLaunchResult:
        try:
            return self._wait_for_ready(
                launched_process=None,
                cached_state=cached_state,
                started=False,
            )
        except Exception:
            self._cleanup_cached_state(cached_state)
            raise

    def _start_server_process(self) -> tuple[subprocess.Popen[Any], Path]:
        binary = which("llama-server")
        if binary is None:
            raise LlamaLauncherError("llama-server binary not found on PATH.")

        host = self._parsed_url.hostname
        port = self._parsed_url.port
        if host is None or port is None:
            raise LlamaLauncherError(
                "server_url must include a valid hostname and port."
            )
        if host not in {"127.0.0.1", "localhost"}:
            raise LlamaLauncherError(
                "Automatic llama.cpp startup only supports local server_url hosts."
            )

        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"llama-server-{host.replace('.', '_')}-{port}.log"

        args = [
            binary,
            "-hf",
            self.config.hf_model,
            "--alias",
            self.model_alias,
            "--host",
            host,
            "--port",
            str(port),
            "--ctx-size",
            str(self.config.desired_context_tokens),
            "--device",
            self.config.device,
            "--gpu-layers",
            str(self.config.gpu_layers),
            "--jinja",
        ]
        if _get_mmproj_support():
            args.append("--no-mmproj")

        with log_path.open("ab") as log_file:
            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        return process, log_path

    def _wait_for_ready(
        self,
        *,
        launched_process: subprocess.Popen[Any] | None,
        cached_state: _LauncherState | None,
        started: bool,
    ) -> LlamaLaunchResult:
        deadline = time.monotonic() + self.config.startup_timeout_seconds
        client = self._client()
        last_error = "server is not reachable yet"

        while time.monotonic() < deadline:
            if launched_process is not None and launched_process.poll() is not None:
                raise LlamaLauncherError(
                    "llama.cpp server exited before readiness "
                    f"(exit code {launched_process.returncode}).\n"
                    f"Log tail:\n{self._read_log_tail(cached_state)}"
                )
            if (
                launched_process is None
                and cached_state is not None
                and not self._pid_is_alive(cached_state.pid)
                and not self._socket_is_open()
            ):
                raise LlamaLauncherError(
                    "Cached llama.cpp process exited before readiness.\n"
                    f"Log tail:\n{self._read_log_tail(cached_state)}"
                )

            if self._socket_is_open():
                try:
                    client.require_ready()
                    client.require_model_alias(self.model_alias)
                    return LlamaLaunchResult(
                        status="started" if started else "reused",
                        pid=cached_state.pid if cached_state is not None else None,
                        server_url=self.server_url,
                        model_alias=self.model_alias,
                        client=client,
                    )
                except LlamaServerError as exc:
                    last_error = str(exc)
                    if "alias mismatch" in last_error:
                        raise LlamaLauncherError(last_error) from exc

            time.sleep(DEFAULT_STARTUP_POLL_SECONDS)

        raise LlamaLauncherError(
            f"Timed out waiting for llama.cpp server at {self.server_url} "
            f"to become ready for alias '{self.model_alias}'. Last error: {last_error}.\n"
            f"Log tail:\n{self._read_log_tail(cached_state)}"
        )

    def _client(self) -> LlamaServerClient:
        return LlamaServerClient(
            base_url=self.server_url,
            api_key=self.api_key,
            timeout_seconds=self.config.request_timeout_seconds,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            enable_thinking=self.config.enable_thinking,
        )

    def _socket_is_open(self) -> bool:
        host = self._parsed_url.hostname
        port = self._parsed_url.port
        if host is None or port is None:
            return False
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            return False

    def _load_matching_state(self) -> _LauncherState | None:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except json.JSONDecodeError:
            self._clear_state()
            return None

        if not isinstance(payload, dict):
            self._clear_state()
            return None

        try:
            state = _LauncherState(
                base_url=str(payload["base_url"]),
                model_alias=str(payload["model_alias"]),
                hf_model=str(payload["hf_model"]),
                pid=int(payload["pid"]),
                log_path=str(payload["log_path"]),
            )
        except (KeyError, TypeError, ValueError):
            self._clear_state()
            return None

        if (
            state.base_url != self.server_url
            or state.model_alias != self.model_alias
            or state.hf_model != self.config.hf_model
        ):
            return None
        return state

    def _prepare_matching_state(self) -> _LauncherState | None:
        state = self._load_matching_state()
        if state is None:
            return None
        if not self._state_process_matches(state):
            self._clear_state()
            return None
        return state

    def _write_state(self, state: _LauncherState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "base_url": state.base_url,
                    "model_alias": state.model_alias,
                    "hf_model": state.hf_model,
                    "pid": state.pid,
                    "log_path": state.log_path,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _clear_state(self) -> None:
        try:
            self.state_path.unlink()
        except FileNotFoundError:
            return

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        process = _ACTIVE_LAUNCH_PROCESSES.get(pid)
        if process is not None and process.poll() is not None:
            _ACTIVE_LAUNCH_PROCESSES.pop(pid, None)
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            _ACTIVE_LAUNCH_PROCESSES.pop(pid, None)
            return False
        return True

    def _state_process_matches(self, state: _LauncherState) -> bool:
        if not self._pid_is_alive(state.pid):
            return False

        try:
            raw_cmdline = Path(f"/proc/{state.pid}/cmdline").read_bytes()
        except OSError:
            return False

        cmdline = [
            part.decode("utf-8", errors="replace")
            for part in raw_cmdline.split(b"\0")
            if part
        ]
        if not cmdline:
            return False
        if not any(Path(argument).name == "llama-server" for argument in cmdline[:2]):
            return False
        return (
            self._argument_value(cmdline, "-hf") == state.hf_model
            and self._argument_value(cmdline, "--alias") == state.model_alias
            and self._argument_value(cmdline, "--host") == self._parsed_url.hostname
            and self._argument_value(cmdline, "--port") == str(self._parsed_url.port)
            and ("--no-mmproj" in cmdline) == _get_mmproj_support()
        )

    @staticmethod
    def _terminate_process(process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            _ACTIVE_LAUNCH_PROCESSES.pop(process.pid, None)
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        _ACTIVE_LAUNCH_PROCESSES.pop(process.pid, None)

    @staticmethod
    def _parse_server_url(server_url: str):
        parsed = urlparse(server_url)
        if parsed.scheme != "http":
            raise LlamaLauncherError(
                "Automatic llama.cpp startup only supports http server_url values."
            )
        if parsed.hostname is None:
            raise LlamaLauncherError("server_url must include a hostname.")
        if (
            parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise LlamaLauncherError(
                "Automatic llama.cpp startup requires a plain base server_url without path or query."
            )
        port = parsed.port or 80
        return parsed._replace(netloc=f"{parsed.hostname}:{port}")

    @staticmethod
    def _read_log_tail(state: _LauncherState | None, max_lines: int = 20) -> str:
        if state is None:
            return "<no launcher log available>"
        log_path = Path(state.log_path)
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            return "<launcher log file missing>"
        if not lines:
            return "<launcher log is empty>"
        return "\n".join(lines[-max_lines:])

    @staticmethod
    def _remember_process(process: subprocess.Popen[Any]) -> None:
        _ACTIVE_LAUNCH_PROCESSES[process.pid] = process

    def _cleanup_cached_state(self, state: _LauncherState | None) -> None:
        if state is None:
            return
        pid = state.pid
        if self._state_process_matches(state):
            self._terminate_pid(pid)
        self._clear_state()

    @staticmethod
    def _terminate_pid(pid: int, timeout_seconds: float = 5.0) -> None:
        tracked_process = _ACTIVE_LAUNCH_PROCESSES.get(pid)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            if tracked_process is not None:
                try:
                    tracked_process.wait(timeout=0.1)
                except Exception:
                    pass
            _ACTIVE_LAUNCH_PROCESSES.pop(pid, None)
            return

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                if tracked_process is not None:
                    try:
                        tracked_process.wait(timeout=0.1)
                    except Exception:
                        pass
                _ACTIVE_LAUNCH_PROCESSES.pop(pid, None)
                return
            time.sleep(0.05)

        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            if tracked_process is not None:
                try:
                    tracked_process.wait(timeout=0.1)
                except Exception:
                    pass
            _ACTIVE_LAUNCH_PROCESSES.pop(pid, None)
            return
        hard_deadline = time.monotonic() + 1.0
        while time.monotonic() < hard_deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.05)
        if tracked_process is not None:
            try:
                tracked_process.wait(timeout=0.1)
            except Exception:
                pass
        _ACTIVE_LAUNCH_PROCESSES.pop(pid, None)

    @staticmethod
    def _argument_value(arguments: list[str], flag: str) -> str | None:
        for index, argument in enumerate(arguments[:-1]):
            if argument == flag:
                return arguments[index + 1]
        return None
