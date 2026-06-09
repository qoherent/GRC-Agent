"""Local llama.cpp server startup and readiness management for the CLI."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import signal
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Any
from urllib.parse import urlparse

from grc_agent.config import LlamaConfig
from grc_agent.llama_probe import LlamaHealthProbe, LlamaServerError
from grc_agent.toolagents_runtime import ToolAgentsLlamaProviderConfig

logger = logging.getLogger(__name__)


DEFAULT_STARTUP_POLL_SECONDS = 0.5
# Default on-disk locations for the launcher state and log files. Exposed as
# module constants so `grc-agent paths` and other tools can surface the exact
# paths without re-deriving them.
DEFAULT_LLAMA_STATE_PATH = Path.home() / ".cache" / "grc_agent" / "llama_launcher_state.json"
DEFAULT_LLAMA_LOG_DIR = Path.home() / ".cache" / "grc_agent" / "llama_launcher_logs"
_ACTIVE_LAUNCH_PROCESSES: dict[int, subprocess.Popen[Any]] = {}

# Detected once at first use; True if the installed llama.cpp supports --no-mmproj.
_mmproj_state: dict[str, bool | None] = {"supported": None}
# Detected once at first use; True if the installed llama.cpp supports --flash-attn.
_flash_attn_state: dict[str, bool | None] = {"supported": None}


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
    except Exception as exc:
        logger.debug("_detect_mmproj_support subprocess failed: %s", exc)
        return False


def _get_mmproj_support() -> bool:
    """Return whether the installed llama-server supports --no-mmproj (detected once)."""
    if _mmproj_state["supported"] is None:
        _mmproj_state["supported"] = _detect_mmproj_support()
    return bool(_mmproj_state["supported"])


def _detect_flash_attn_support() -> bool:
    """Check whether the installed llama-server binary supports --flash-attn."""
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
        return "--flash-attn" in result.stdout or "--flash-attn" in result.stderr
    except Exception as exc:
        logger.debug("_detect_flash_attn_support subprocess failed: %s", exc)
        return False


def _get_flash_attn_support() -> bool:
    """Return whether the installed llama-server supports --flash-attn (detected once)."""
    if _flash_attn_state["supported"] is None:
        _flash_attn_state["supported"] = _detect_flash_attn_support()
    return bool(_flash_attn_state["supported"])


class LlamaLauncherError(RuntimeError):
    """Raised when the CLI cannot make the llama.cpp backend available."""


@dataclass(frozen=True)
class LlamaLaunchResult:
    """The outcome of ensuring a local llama.cpp server is ready."""

    status: str
    pid: int | None
    server_url: str
    model_alias: str
    provider_config: ToolAgentsLlamaProviderConfig
    health_evidence: dict[str, Any]


@dataclass(frozen=True)
class _LauncherState:
    base_url: str
    model_alias: str
    hf_model: str
    model_path: str | None
    pid: int
    log_path: str
    # The PID of the Python launcher process that wrote this state
    # file. When a *different* launcher reads the state, this field
    # marks the cache as stale-by-lifecycle even if the cmdline still
    # matches. Field defaults to 0 in the dataclass for backward
    # compatibility with state files written by older versions.
    launcher_pid: int = 0


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
            return self._ensure_server_ready_unlocked()

    def _ensure_server_ready_unlocked(self) -> LlamaLaunchResult:
        """Core ready/start logic executed under the file lock."""
        # Reap any `llama-server` PID we left behind from a previous
        # crashed/killed CLI/GUI invocation. This must happen *before*
        # we look at the cached state file so a defunct state file
        # does not block a fresh launch.
        self.reap_orphan_pids()
        # Bounded retention on the launcher log directory. Cheap
        # when the dir is empty or all files are recent.
        self.prune_old_logs()
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
            model_path=self.config.model_path,
            pid=process.pid,
            log_path=str(log_path),
            launcher_pid=os.getpid(),
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

    def swap_model(
        self,
        *,
        new_hf_repo: str,
        new_filename: str,
        new_alias: str | None = None,
    ) -> LlamaLaunchResult:
        """Restart the local llama.cpp server with a different model.

        Phase 3 of the model-selector rollout. Performs the swap in
        three steps, all under the file lock:

        1. Terminate any cached backend owned by the OLD launcher
           (``self``). ``_cleanup_cached_state`` uses the cached
           state file as the authoritative record of the PID we
           started, so a swap never reaps a server owned by
           another launcher/user.
        2. Build a new :class:`LlamaConfig` whose ``hf_model`` and
           ``model`` point at the requested file. ``model_path`` is
           cleared because swap is HF-token-driven; a
           ``model_path``-driven swap is a separate code path the
           CLI/GUI do not exercise in Phase 3.
        3. Run :meth:`_ensure_server_ready_unlocked` on a fresh launcher for
           the new config. The new launcher writes its own state
           file and waits for readiness.

        Args:
            new_hf_repo: Hugging Face repo slug, e.g.
                ``"unsloth/Qwen3.5-2B-GGUF"``.
            new_filename: GGUF filename inside the repo, e.g.
                ``"Qwen3.5-2B-UD-Q4_K_XL.gguf"``.
            new_alias: Optional override for the ``--alias`` flag.
                Defaults to the bare ``new_filename``.

        Returns:
            A :class:`LlamaLaunchResult` with the new provider_config
            and health evidence.

        Raises:
            LlamaLauncherError: if the launcher could not terminate
                the existing server, start the new one, or reach
                readiness within ``startup_timeout_seconds``. The
                cached state is cleared on any failure so a
                subsequent :meth:`ensure_server_ready` does not
                see a half-written record.
        """
        import dataclasses

        hf_model_token = f"{new_hf_repo}:{new_filename}"
        alias = (new_alias or new_filename).strip() or new_filename
        new_config = dataclasses.replace(
            self.config,
            hf_model=hf_model_token,
            model=alias,
            # Swap is HF-token-driven. A model_path-driven swap is
            # out of scope for Phase 3; preserving the old model_path
            # would re-load the original file on the next
            # ``ensure_server_ready`` and silently undo the swap.
            model_path=None,
        )
        new_launcher = LlamaServerLauncher(
            new_config,
            server_url=self.server_url,
            model_alias=alias,
            api_key=self.api_key,
            state_path=self.state_path,
            log_dir=self.log_dir,
        )
        with self._lock():
            # Terminate the old backend under the lock so a parallel
            # ``ensure_server_ready`` call cannot race against our
            # port handover. ``_cleanup_cached_state`` is a no-op
            # when the cached state is empty or owned by another
            # launcher, so this is safe under multi-user CI.
            self._cleanup_cached_state(self._prepare_matching_state())
            # Use ``_ensure_server_ready_unlocked`` (not ``ensure_server_ready``)
            # since we already hold the lock on the shared path. The old cached state
            # was just cleared, so a fresh launch is forced.
            return new_launcher._ensure_server_ready_unlocked()

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

        device_val = self.config.device
        if device_val.upper() == "CPU":
            device_val = "none"

        model_arg_name, model_arg_value = self._model_argument()
        args: list[str] = [
            binary,
            model_arg_name,
            model_arg_value,
            "--alias",
            self.model_alias,
            "--host",
            host,
            "--port",
            str(port),
            "--ctx-size",
            str(self.config.desired_context_tokens),
            "--jinja",
        ]
        if device_val.upper() != "AUTO":
            args.extend(["--device", device_val])
        if _get_flash_attn_support():
            args.extend(["--flash-attn", "auto"])
        gpu_layers = self.config.gpu_layers
        if gpu_layers > 0 and gpu_layers < 999:
            args.extend(["--gpu-layers", str(gpu_layers)])
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
        probe = self._probe()
        last_error = "server is not reachable yet"
        printed_download_msg = False
        last_download_msg = None

        while time.monotonic() < deadline:
            if launched_process is not None and launched_process.poll() is not None:
                if printed_download_msg:
                    print()
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
                if printed_download_msg:
                    print()
                raise LlamaLauncherError(
                    "Cached llama.cpp process exited before readiness.\n"
                    f"Log tail:\n{self._read_log_tail(cached_state)}"
                )

            if self._socket_is_open():
                try:
                    probe.require_ready()
                    probe.require_model_alias(self.model_alias)
                    props = probe.get_server_properties()
                    from grc_agent.llama_probe import extract_model_context_limit

                    actual_context = extract_model_context_limit(props)
                    if actual_context is None:
                        raise LlamaServerError(
                            "llama.cpp server context is unknown from /props."
                        )
                    if printed_download_msg:
                        print(" [Done]", flush=True)
                    return LlamaLaunchResult(
                        status="started" if started else "reused",
                        pid=cached_state.pid if cached_state is not None else None,
                        server_url=self.server_url,
                        model_alias=self.model_alias,
                        provider_config=self._provider_config(),
                        health_evidence={
                            "llama_server_url": self.server_url,
                            "llama_model": self.model_alias,
                            "llama_actual_context_tokens": actual_context,
                            "llama_context_verified": True,
                            "llama_model_ready": True,
                        },
                    )
                except LlamaServerError as exc:
                    last_error = str(exc)
                    if "alias mismatch" in last_error:
                        if printed_download_msg:
                            print()
                        raise LlamaLauncherError(last_error) from exc

            # If the socket is not open, check if there is an active Hugging Face download
            if not self._socket_is_open():
                progress = self._get_active_download_progress()
                if progress:
                    msg = f"\rDownloading model from Hugging Face... ({progress})"
                    if msg != last_download_msg:
                        print(msg, end="", flush=True)
                        last_download_msg = msg
                        printed_download_msg = True

            time.sleep(DEFAULT_STARTUP_POLL_SECONDS)

        if printed_download_msg:
            print()
        raise LlamaLauncherError(
            f"Timed out waiting for llama.cpp server at {self.server_url} "
            f"to become ready for alias '{self.model_alias}'. Last error: {last_error}.\n"
            f"Log tail:\n{self._read_log_tail(cached_state)}"
        )

    def _get_active_download_progress(self) -> str | None:
        try:
            hf_hub = Path.home() / ".cache" / "huggingface" / "hub"
            if not hf_hub.is_dir():
                return None
            download_files = list(hf_hub.glob("**/*.downloadInProgress"))
            if not download_files:
                return None
            total_bytes = sum(f.stat().st_size for f in download_files if f.is_file())
            if total_bytes == 0:
                return "starting download..."
            gb_size = total_bytes / (1024 * 1024 * 1024)
            return f"{gb_size:.2f} GB"
        except Exception:
            return None

    def _probe(self) -> LlamaHealthProbe:
        return LlamaHealthProbe(
            base_url=self.server_url,
            api_key=self.api_key,
            timeout_seconds=self.config.request_timeout_seconds,
        )

    def _provider_config(self) -> ToolAgentsLlamaProviderConfig:
        return ToolAgentsLlamaProviderConfig(
            base_url=self.server_url,
            model=self.model_alias,
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
        except (FileNotFoundError, OSError):
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
                model_path=(
                    str(payload["model_path"])
                    if payload.get("model_path") is not None
                    else None
                ),
                pid=int(payload["pid"]),
                log_path=str(payload["log_path"]),
                launcher_pid=int(payload.get("launcher_pid", 0)),
            )
        except (KeyError, TypeError, ValueError):
            self._clear_state()
            return None

        if (
            state.base_url != self.server_url
            or state.model_alias != self.model_alias
            or state.hf_model != self.config.hf_model
            or state.model_path != self.config.model_path
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
                    "model_path": state.model_path,
                    "pid": state.pid,
                    "log_path": state.log_path,
                    # Stamp the writing launcher's PID so a later
                    # launcher can detect that the prior writer is
                    # gone (i.e. the cached backend is an orphan).
                    "launcher_pid": os.getpid(),
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
        if not any(Path(argument).name == "llama-server" for argument in cmdline[:3]):
            return False
        model_arg_name, model_arg_value = self._state_model_argument(state)
        return (
            self._argument_value(cmdline, model_arg_name) == model_arg_value
            and self._argument_value(cmdline, "--alias") == state.model_alias
            and self._argument_value(cmdline, "--host") == self._parsed_url.hostname
            and self._argument_value(cmdline, "--port") == str(self._parsed_url.port)
            and ("--no-mmproj" in cmdline) == _get_mmproj_support()
        )

    def _model_argument(self) -> tuple[str, str]:
        if self.config.model_path is not None:
            return "-m", self.config.model_path
        return "-hf", self.config.hf_model

    @staticmethod
    def _state_model_argument(state: _LauncherState) -> tuple[str, str]:
        if state.model_path is not None:
            return "-m", state.model_path
        return "-hf", state.hf_model

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
                    logger.debug("terminate_pid wait failed pid=%s", pid, exc_info=True)
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
                        logger.debug(
                            "pre-sigterm wait failed pid=%s", pid, exc_info=True
                        )
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
                    logger.debug("terminate_pid wait failed pid=%s", pid, exc_info=True)
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
                logger.debug("post-sigkill wait failed pid=%s", pid, exc_info=True)
        _ACTIVE_LAUNCH_PROCESSES.pop(pid, None)

    def reap_orphan_pids(self) -> list[int]:
        """Terminate any `llama-server` PID we previously launched but no
        longer own. Called at the top of :meth:`ensure_server_ready` so a
        killed parent process does not leave the model server holding the
        port and GPU memory.

        Discriminator: the cached state file's ``launcher_pid`` field. If
        it does not match our current PID, the previous launcher is gone
        and the cached backend is an orphan by lifecycle, regardless of
        whether ``_ACTIVE_LAUNCH_PROCESSES`` happens to contain the PID.

        We intentionally do **not** scan ``/proc`` for unrelated
        ``llama-server`` processes. The cached state file is the
        authoritative record of "PIDs this launcher started"; without
        a cached record, a matching PID in ``/proc`` could equally well
        belong to a parallel launcher (e.g. another user, a remote
        tool, or — in tests — a stub binary we did not start). Reaping
        such PIDs would be a destructive surprise.

        The cached state file is cleared only when we actually
        terminated an orphan — a "still current" cache is left alone
        so a subsequent :meth:`_prepare_matching_state` finds it and
        returns ``reused``.
        """
        terminated: list[int] = []
        current_pid = os.getpid()
        state = self._load_matching_state()
        if state is None:
            return terminated
        if not self._pid_is_alive(state.pid):
            # Cached state points at a dead PID. Clear the stale
            # state file so a fresh launch can write a new one.
            self._clear_state()
            return terminated
        same_launcher = state.launcher_pid == current_pid
        tracked_here = state.pid in _ACTIVE_LAUNCH_PROCESSES
        if same_launcher and tracked_here:
            # The launcher is still alive and tracked in this
            # Python process; the cached state is current. Do not
            # reap, and leave the state file alone for reuse.
            logger.debug(
                "reap_orphan_pids: skipping live tracked pid=%s", state.pid
            )
            return terminated
        if self._state_process_matches(state):
            # The cached PID belongs to a previous launcher
            # process that is now gone. The cmdline still
            # matches what we would launch, so the orphan is
            # ours to reap. After termination, clear the state
            # file so the next prepare_matching_state falls
            # through to a fresh launch.
            self._terminate_pid(state.pid)
            terminated.append(state.pid)
            self._clear_state()
        # else: cmdline no longer matches what we would launch
        # (model/host/port changed). Leave the state file alone;
        # _prepare_matching_state will reject it as non-matching
        # and clear it on its own.
        if terminated:
            logger.info("reap_orphan_pids terminated=%s", terminated)
        return terminated

    def prune_old_logs(self, *, retention_days: int | None = None) -> list[Path]:
        """Delete files in :attr:`log_dir` whose mtime is older than
        ``retention_days`` days. Default is
        ``self.config.log_retention_days``; pass an explicit value to
        override (used by tests).

        A retention of ``0`` keeps everything forever. The function
        silently skips files it cannot stat or unlink, logging at
        DEBUG.
        """
        days = (
            retention_days
            if retention_days is not None
            else self.config.log_retention_days
        )
        if days <= 0:
            return []
        cutoff = time.time() - (days * 86400)
        removed: list[Path] = []
        try:
            entries = list(self.log_dir.iterdir())
        except FileNotFoundError:
            return removed
        except OSError as exc:
            logger.debug("prune_old_logs: iterdir failed on %s: %s", self.log_dir, exc)
            return removed
        for entry in entries:
            if not entry.is_file():
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError as exc:
                logger.debug("prune_old_logs: stat failed on %s: %s", entry, exc)
                continue
            if mtime >= cutoff:
                continue
            try:
                entry.unlink()
                removed.append(entry)
            except OSError as exc:
                logger.debug("prune_old_logs: unlink failed on %s: %s", entry, exc)
        if removed:
            logger.info("prune_old_logs removed=%d retention_days=%d", len(removed), days)
        return removed

    @staticmethod
    def _argument_value(arguments: list[str], flag: str) -> str | None:
        for index, argument in enumerate(arguments[:-1]):
            if argument == flag:
                return arguments[index + 1]
        return None
