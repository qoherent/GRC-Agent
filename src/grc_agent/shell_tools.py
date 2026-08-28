"""Shell command execution scoped to the active project directory.

The harness ``Shell`` toolset (pydantic_ai_harness 0.23) provides the
execution machinery: synchronous and background commands, output tail
truncation, timeouts, process-group cleanup at run end, and a
best-effort command denylist. This module subclasses it for the three
things a native GRC app needs on top:

- **Dynamic cwd.** Commands run in the configured project directory (the
  same late-binding providers ``fs_tools`` uses), re-resolved at every
  spawn — a tab switch or project change between two tool calls is
  followed automatically. With no project directory and no saved
  flowgraph, the exec tools gate with the same ``ModelRetry`` the
  filesystem tools use instead of spawning anywhere arbitrary.

- **Human-in-the-loop approval.** ``run_command`` and ``start_command``
  are physical-world side effects (builds, RF hardware CLIs, arbitrary
  programs) and are re-registered with ``requires_approval=True`` — the
  same native deferred-tool mechanism ``change_graph`` uses, resolved by
  the sidebar's approval cards. ``check_command``/``stop_command`` stay
  un-gated: they only observe and clean up the agent's own background
  processes.

- **Environment scrubbing derived from the provider catalog**, not a
  hand-picked list: every API-key variable the app's provider catalog
  knows (plus ``OLLAMA_CLOUD_API_KEY`` and the harness's LLM-key glob
  patterns) is stripped from spawned subprocess environments. The
  harness's own ``LLM_API_KEY_ENV_PATTERNS`` misses both Ollama keys
  and groq/mistral/cohere/xai — this app's default provider family.

Policy shape (deliberately NOT an allowlist): the GR engineer's command
surface is not enumerable — a dozen SDR vendor CLI families, project
scripts, build toolchains — so risk is managed by consent (approval
cards showing the literal command) plus the harness's destructive-command
denylist, not by forecasting command names. ``GRC_SHELL_DENIED_COMMANDS``
(``.env``, comma-separated; empty disables) and ``GRC_SHELL_TIMEOUT``
(seconds, default 600) are the user-tunable knobs.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT
from pydantic_ai_harness.shell import LLM_API_KEY_ENV_PATTERNS
from pydantic_ai_harness.shell._capability import _DEFAULT_DENIED_COMMANDS
from pydantic_ai_harness.shell._toolset import ShellToolset, _recoverable

from grc_agent.fs_tools import (
    _NO_ACTIVE_GRAPH_MSG,
    active_grc_path,
    active_project_dir,
)

_log = logging.getLogger(__name__)

# Private-but-stable harness constants: the destructive-command defaults (the
# capability's __post_init__ identity trick makes them awkward to reuse) and
# the ShellToolset itself. No public equivalent as of pydantic_ai_harness
# 0.23 (`shell.__all__` is {Shell, ShellToolset, LLM_API_KEY_ENV_PATTERNS} —
# only the denylist tuple is missing). Re-checked on every harness bump, same
# deliberate coupling as fs_tools' filesystem privates.

# Placeholder cwd used only while no project directory is set. Never a real
# directory, so nothing could spawn there even if the gate below were bypassed.
_UNSAVED_CWD = Path("/grc-agent-unsaved-root")

# The two harness tools that execute commands (approval-gated); check/stop
# only observe and clean up the agent's own background processes.
_EXEC_TOOL_NAMES = ("run_command", "start_command")


def derive_env_deny_patterns() -> tuple[str, ...]:
    """Env-var deny patterns for spawned subprocesses, derived from the app's
    own provider catalog — not hand-picked.

    Every API-key variable ``ui.providers.PROVIDER_API_KEY`` knows, plus
    ``OLLAMA_CLOUD_API_KEY`` (read directly by the model builders), plus the
    harness's LLM-key glob patterns (which also cover gateway/pydantic creds
    and the coarse OPENAI_*/GOOGLE_* prefixes). A provider added to the
    catalog is covered automatically.
    """
    from grc_agent.ui.providers import PROVIDER_API_KEY

    names = {v for v in PROVIDER_API_KEY.values() if v}
    names.add("OLLAMA_CLOUD_API_KEY")
    return tuple(sorted(names)) + tuple(LLM_API_KEY_ENV_PATTERNS)


def default_denied_commands() -> list[str]:
    """The harness destructive-command denylist, unless the user overrides it
    via ``GRC_SHELL_DENIED_COMMANDS`` (comma-separated; empty string
    disables name filtering entirely — their explicit choice)."""
    from grc_agent.settings import get_env_value

    raw = get_env_value("GRC_SHELL_DENIED_COMMANDS")
    if raw is None:
        return list(_DEFAULT_DENIED_COMMANDS)
    return [name.strip() for name in raw.split(",") if name.strip()]


def default_timeout() -> float:
    """Per-command timeout in seconds (``GRC_SHELL_TIMEOUT``, default 600 —
    builds are slow; the model can still pass ``timeout_seconds`` per call)."""
    from grc_agent.settings import get_env_value

    raw = get_env_value("GRC_SHELL_TIMEOUT")
    try:
        return float(raw) if raw else 600.0
    except ValueError:
        return 600.0


def resolve_shell_cwd() -> Path:
    """The project directory every command spawns in, re-resolved per spawn.

    Same precedence as the filesystem sandbox root: the explicitly selected
    project directory, else the active flowgraph's folder. Falls back to the
    placeholder (never a real directory) when neither exists — the exec-tool
    gate turns that into the same actionable error the fs tools give.
    """
    proj = active_project_dir()
    if proj is not None:
        return proj
    grc = active_grc_path()
    if grc is not None:
        return grc.parent
    return _UNSAVED_CWD


class GrcShellToolset(ShellToolset[AgentDepsT]):
    """Harness shell toolset with a dynamic project-dir cwd and approval-gated
    command execution.

    ``_cwd`` is a property over the fs_tools providers (the same
    swallow-the-setter pattern ``GrcFileSystemToolset._root`` uses), so every
    spawn resolves the project directory at call time. ``for_run`` MUST be
    overridden: the harness implementation builds a plain ``ShellToolset``
    from stored fields, which would silently drop both the dynamic cwd and
    the approval flags on every run (verified live against 0.23.0).
    """

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        allowed_commands: Sequence[str] = (),
        denied_commands: Sequence[str] | None = None,
        denied_operators: Sequence[str] = (),
        default_timeout: float = 600.0,
        max_output_chars: int = 50_000,
        persist_cwd: bool = False,
        allow_interactive: bool = False,
        env: dict[str, str] | None = None,
        denied_env_patterns: Sequence[str] = (),
        timeout_tasks: dict[str, asyncio.Task[None]] | None = None,
    ) -> None:
        if denied_commands is None:
            denied_commands = list(_DEFAULT_DENIED_COMMANDS)
        self._timeout_tasks = timeout_tasks if timeout_tasks is not None else {}
        super().__init__(
            cwd=cwd if cwd is not None else _UNSAVED_CWD,
            allowed_commands=list(allowed_commands),
            denied_commands=list(denied_commands),
            denied_operators=list(denied_operators),
            default_timeout=default_timeout,
            max_output_chars=max_output_chars,
            persist_cwd=persist_cwd,
            allow_interactive=allow_interactive,
            env=env,
            denied_env_patterns=list(denied_env_patterns),
        )
        # Re-flag the exec tools AFTER the parent registered them: approval is
        # a registration-time resolution in pydantic-ai, and the harness
        # registers them un-gated. Mutating the Tool dataclass field is the
        # only route that preserves the harness docstrings/metadata (del +
        # re-add would need to replicate both). Also applied to every for_run
        # instance, which flows through this same constructor.
        self._apply_exec_approval()

    @_recoverable
    async def start_command(
        self,
        command: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Start a long-running command in the background (e.g. captures, servers).

        Do not use this to run the active flowgraph — use run_flowgraph instead.

        Args:
            command: The shell command to run in the background.
            timeout_seconds: Optional maximum seconds to keep running before auto-stopping.
        """
        res = await super().start_command(command)
        if timeout_seconds is not None and timeout_seconds > 0 and "ID: " in res:
            cmd_id = res.split("ID: ")[1].strip().splitlines()[0]

            async def _auto_stop() -> None:
                try:
                    await asyncio.sleep(timeout_seconds)
                    if cmd_id in self._background:
                        await self.stop_command(cmd_id)
                except asyncio.CancelledError:
                    pass
                finally:
                    self._timeout_tasks.pop(cmd_id, None)

            task = asyncio.create_task(_auto_stop())
            self._timeout_tasks[cmd_id] = task
        return res

    @_recoverable
    async def stop_command(self, command_id: str) -> str:
        """Stop a running background command started with start_command by its command ID."""
        task = self._timeout_tasks.pop(command_id, None)
        if task and not task.done():
            task.cancel()
        return await super().stop_command(command_id)

    def _apply_exec_approval(self) -> None:
        for name in ("run_command", "start_command", "check_command", "stop_command"):
            tool = self.tools.get(name)
            if tool is not None:
                if name in _EXEC_TOOL_NAMES:
                    tool.requires_approval = True
                if name == "run_command":
                    tool.description = (
                        "Execute a shell command in the project directory (e.g. build toolchains, "
                        "SDR utilities, standalone scripts, data analysis). Do not use this to run the "
                        "active flowgraph — use run_flowgraph so GRC generates the latest code."
                    )
                    props = tool.function_schema.json_schema.setdefault("properties", {})
                    td = props.get("timeout_seconds")
                    if isinstance(td, dict) and "description" in td:
                        # State the RESOLVED default, never a hardcoded number:
                        # GRC_SHELL_TIMEOUT is user-tunable via .env and the
                        # model-facing schema must not lie about the value.
                        td["description"] = (
                            "Maximum seconds to wait (default: GRC_SHELL_TIMEOUT, "
                            f"{default_timeout():g}s)."
                        )
                elif name == "start_command":
                    tool.description = (
                        "Start a long-running command in the background (e.g. captures, servers). "
                        "Do not use this to run the active flowgraph — use run_flowgraph instead."
                    )
                    props = tool.function_schema.json_schema.setdefault("properties", {})
                    props["timeout_seconds"] = {
                        "type": "number",
                        "description": "Optional maximum duration in seconds before the background process is automatically stopped.",
                    }
                elif name == "check_command":
                    tool.description = (
                        "Check the status and recent output of a running background command started with start_command."
                    )
                elif name == "stop_command":
                    tool.description = (
                        "Stop a running background command started with start_command by its command ID."
                    )

    # -- dynamic project root (fs_tools._root pattern) ----------------------

    @property
    def _cwd(self) -> Path:
        return resolve_shell_cwd()

    @_cwd.setter
    def _cwd(self, value: Path) -> None:  # noqa: ARG002
        """Swallow the parent's static assignment — the dynamic root wins."""

    async def for_run(self, ctx: Any) -> GrcShellToolset[AgentDepsT]:  # noqa: ARG002
        """Fresh instance per run (harness contract) that keeps THIS class —
        the dynamic cwd, the exec-tool approval flags, and the gating all
        survive; the base implementation would return a plain ShellToolset."""
        return GrcShellToolset(
            allowed_commands=self._allowed_commands,
            denied_commands=self._denied_commands,
            denied_operators=self._denied_operators,
            default_timeout=self._default_timeout,
            max_output_chars=self._max_output_chars,
            persist_cwd=self._persist_cwd,
            allow_interactive=self._allow_interactive,
            env=self._env,
            denied_env_patterns=self._denied_env_patterns,
            timeout_tasks=self._timeout_tasks,
        )

    def _check_command(self, command: str) -> None:
        """Gate on a configured project directory, then the harness checks.

        Raised as PermissionError so the harness's own ``_recoverable``
        decorator converts it into a ModelRetry — the same error surface as
        every other domain tool.
        """
        if self._cwd == _UNSAVED_CWD:
            raise PermissionError(_NO_ACTIVE_GRAPH_MSG)
        super()._check_command(command)


@dataclass
class GrcShell(AbstractCapability[AgentDepsT]):
    """Shell execution capability for the executor agent.

    Denylist mode by design (see module docstring): the harness's destructive
    defaults stay denied and everything an engineer needs stays available;
    the user can tighten or loosen via ``GRC_SHELL_DENIED_COMMANDS``.
    """

    denied_commands: Sequence[str] = field(default_factory=default_denied_commands)
    default_timeout: float = field(default_factory=default_timeout)
    max_output_chars: int = 50_000
    denied_env_patterns: Sequence[str] = field(default_factory=derive_env_deny_patterns)

    def get_toolset(self) -> GrcShellToolset[AgentDepsT]:
        return GrcShellToolset[AgentDepsT](
            denied_commands=self.denied_commands,
            default_timeout=self.default_timeout,
            max_output_chars=self.max_output_chars,
            denied_env_patterns=self.denied_env_patterns,
        )
