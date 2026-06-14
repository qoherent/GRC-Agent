"""Command-line entry point for GRC Agent."""

import argparse
import hashlib
import json
import logging
import shlex
import subprocess
import sys
from datetime import UTC
from importlib import metadata
from pathlib import Path
from typing import Any

from grc_agent._payload import ErrorCode, build_error_payload
from grc_agent.agent import GrcAgent
from grc_agent.config import AppConfig, ConfigError, default_app_config, load_app_config
from grc_agent.doctor import (
    build_debug_bundle,
    debug_bundle_summary,
    write_debug_bundle,
)
from grc_agent.doctor import print_doctor_report, run_doctor
from grc_agent.dogfood import (
    VALID_DOGFOOD_SOURCES,
    VALID_FAILURE_CATEGORIES,
    VALID_SEVERITIES,
    VALID_TASK_TYPES,
    record_dogfood_case,
    summarize_dogfood_cases,
)
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.history import GraphHistoryJournal
from grc_agent.config import collect_package_paths
from grc_agent.retrieval import initialize_retrieval
from grc_agent.runtime.clarification import render_clarification_prompt
from grc_agent.runtime.tool_schemas import PUBLIC_TOOL_NAMES
from grc_agent.runtime.model_context import MVP_TOOL_SURFACE
from grc_agent.session import load_grc as load_grc_session
from grc_agent.toolagents_runtime import (
    ToolAgentsLlamaProviderConfig,
    run_bounded_toolagents_turn,
)

logger = logging.getLogger(__name__)


_RETRIEVAL_READY_TOOLS = {"describe_block", "propose_edit", "apply_edit"}
_INSTALLED_GRAPH_ROOTS = (
    Path("/usr/share/gnuradio/examples"),
    Path("/usr/local/share/gnuradio/examples"),
)
AGENTIC_MAX_TOOL_ROUNDS = 24
AGENTIC_REQUEST_TIMEOUT_SECONDS = 180.0


def _build_parser(config: AppConfig | None = None) -> argparse.ArgumentParser:
    llama_config = config.llama if config is not None else None

    parser = argparse.ArgumentParser(
        description="GRC Agent CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging output.",
    )
    parser.add_argument(
        "--config",
        help="Optional path to a TOML config file. Defaults to workspace config when present, then user config, then built-in defaults.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = False

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check environment, config, and retrieval readiness for the packaged app.",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the doctor report as JSON.",
    )
    doctor_parser.add_argument(
        "--skip-retrieval",
        action="store_true",
        help="Skip retrieval readiness checks.",
    )

    subparsers.add_parser(
        "health",
        help="Print a structured agent health check and exit.",
    )

    subparsers.add_parser(
        "release-manifest",
        help="Print a JSON release evidence manifest for the active runtime profile.",
    )

    debug_bundle_parser = subparsers.add_parser(
        "debug-bundle",
        help="Write a redacted JSON support bundle for issue reports.",
    )
    debug_bundle_parser.add_argument(
        "--output",
        required=True,
        help="Path to write the redacted debug bundle JSON.",
    )


    chat_epilog = """
Examples:
  uv run grc-agent chat mygraph.grc --message "Summarize this graph"
  uv run grc-agent chat mygraph.grc --message "Change samp_rate to 48000" --json
  echo "Find an audio sink" | uv run grc-agent chat mygraph.grc --stdin
"""
    chat_parser = subparsers.add_parser(
        "chat",
        help="Run one or more model-backed turns against a loaded graph. "
        "With --message or --stdin, runs a single turn; without it, starts an interactive REPL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=chat_epilog.strip("\n"),
    )
    chat_parser.add_argument(
        "file",
        nargs="?",
        default=None,
        help="Path to a .grc file to load. Use --new to start from an empty graph.",
    )
    chat_parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read the user message from standard input instead of --message.",
    )
    chat_parser.add_argument(
        "--json",
        action="store_true",
        help="Output the final chat result as a JSON object to stdout. Suppresses all other stdout logging.",
    )
    chat_parser.add_argument(
        "--new",
        action="store_true",
        dest="new_graph",
        help="Start from an empty graph instead of loading a file.",
    )
    chat_parser.add_argument(
        "--message",
        required=False,
        help="Run one bounded model turn with this user message. "
        "When omitted, starts an interactive REPL loop.",
    )
    chat_parser.add_argument(
        "--api-key",
        help="Optional API key for server authentication",
    )
    chat_parser.add_argument(
        "--model",
        default=llama_config.model if llama_config is not None else None,
        help="Override the configured model name.",
    )
    chat_parser.add_argument(
        "--agentic",
        action="store_true",
        help=(
            "Use a larger bounded tool budget for exploratory turns. "
            "This does not expose extra tools or bypass validation."
        ),
    )
    chat_parser.add_argument(
        "--max-tool-rounds",
        type=_positive_int_arg,
        help="Override the maximum model tool rounds for this chat session.",
    )

    tool_epilog = """
Examples:
  uv run grc-agent tool summarize_graph --file mygraph.grc
  uv run grc-agent tool apply_edit --args '{"add_blocks": [{"block_id": "analog_sig_source_x", "instance_name": "src"}]}'
"""
    tool_parser = subparsers.add_parser(
        "tool",
        help="Execute one routed runtime tool directly without a model backend.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=tool_epilog.strip("\n"),
    )
    tool_parser.add_argument(
        "tool_name",
        choices=list(PUBLIC_TOOL_NAMES),
        help="Runtime tool name to execute.",
    )
    tool_parser.add_argument(
        "--file",
        help="Optional .grc file to load before executing the tool.",
    )
    tool_parser.add_argument(
        "--args",
        default="{}",
        help="JSON object of tool arguments.",
    )

    model_parser = subparsers.add_parser(
        "model",
        help="Discover, inspect, and swap the local model.",
    )
    model_subparsers = model_parser.add_subparsers(dest="model_command")
    model_subparsers.required = True
    model_list_parser = model_subparsers.add_parser(
        "list",
        help="List every model in the local Ollama instance.",
    )
    model_list_parser.add_argument(
        "--backend",
        choices=["ollama", "openrouter"],
        help="Backend client to list models for (ollama, openrouter). Defaults to active backend.",
    )
    model_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Print discovered models as JSON.",
    )
    model_swap_parser = model_subparsers.add_parser(
        "swap",
        help="Switch clients/models.",
    )
    model_swap_parser.add_argument(
        "--backend",
        choices=["ollama", "openrouter"],
        help="The backend client to switch to.",
    )
    model_swap_parser.add_argument(
        "--model",
        help="Model name (used for 'ollama' or general model override).",
    )
    model_swap_parser.add_argument(
        "--json",
        action="store_true",
        help="Print swap evidence as JSON.",
    )

    dogfood_parser = subparsers.add_parser(
        "dogfood",
        help="Record and summarize structured real-use dogfooding evidence.",
    )
    dogfood_subparsers = dogfood_parser.add_subparsers(dest="dogfood_command")
    dogfood_subparsers.required = True
    dogfood_record_parser = dogfood_subparsers.add_parser(
        "record",
        help="Record one sanitized dogfooding observation as JSONL evidence.",
    )
    dogfood_record_parser.add_argument("prompt", help="User prompt or task attempted.")
    dogfood_record_parser.add_argument(
        "--graph",
        default="",
        help="Graph path or safe identifier. User graph paths are redacted.",
    )
    dogfood_record_parser.add_argument(
        "--source",
        choices=sorted(VALID_DOGFOOD_SOURCES),
        default="manual_review",
        help="Evidence source.",
    )
    dogfood_record_parser.add_argument(
        "--task-type",
        choices=sorted(VALID_TASK_TYPES),
        default="other",
        help="Task category.",
    )
    dogfood_record_parser.add_argument(
        "--failure-category",
        choices=sorted(VALID_FAILURE_CATEGORIES),
        default="no_failure",
        help="Failure category, or no_failure for baseline observations.",
    )
    dogfood_record_parser.add_argument(
        "--severity",
        choices=sorted(VALID_SEVERITIES),
        default="info",
        help="Observed severity.",
    )
    dogfood_record_parser.add_argument("--expected", default="", help="Expected behavior.")
    dogfood_record_parser.add_argument("--actual", default="", help="Actual behavior.")
    dogfood_record_parser.add_argument(
        "--actual-tool",
        action="append",
        default=[],
        dest="actual_tools",
        help="Observed tool name. May be repeated.",
    )
    dogfood_record_parser.add_argument(
        "--graph-delta",
        default="",
        help="Short graph-delta summary, if known.",
    )
    dogfood_record_parser.add_argument(
        "--validation-state",
        default="",
        help="Validation state summary, if known.",
    )
    dogfood_record_parser.add_argument(
        "--save-state",
        default="",
        help="Save/reload state summary, if known.",
    )
    dogfood_record_parser.add_argument(
        "--reproducible",
        action="store_true",
        help="Mark this observation as reproducible.",
    )
    dogfood_record_parser.add_argument("--notes", default="", help="Short notes.")
    dogfood_record_parser.add_argument(
        "--intake-path",
        help="Optional JSONL path. Defaults to reports/dogfood/intake.jsonl.",
    )
    dogfood_record_parser.add_argument(
        "--json",
        action="store_true",
        help="Print recorded observation as JSON.",
    )
    dogfood_report_parser = dogfood_subparsers.add_parser(
        "report",
        help="Summarize and cluster dogfooding observations.",
    )
    dogfood_report_parser.add_argument(
        "--intake-path",
        help="Optional JSONL path. Defaults to reports/dogfood/intake.jsonl.",
    )
    dogfood_report_parser.add_argument(
        "--json",
        action="store_true",
        help="Print dogfooding report as JSON.",
    )

    sessions_parser = subparsers.add_parser(
        "sessions",
        help="List, show, export, and prune local chat sessions.",
    )
    sessions_parser.add_argument(
        "--db",
        help="Override the sessions DB path. Defaults to ~/.grc_agent/sessions.db.",
    )
    sessions_subparsers = sessions_parser.add_subparsers(dest="sessions_command")
    sessions_subparsers.required = True

    sessions_list_parser = sessions_subparsers.add_parser(
        "list", help="List chat sessions, most recent first."
    )
    sessions_list_parser.add_argument(
        "--graph",
        help="Filter by graph path substring.",
    )
    sessions_list_parser.add_argument(
        "--limit", type=int, default=50, help="Maximum number of sessions to return."
    )
    sessions_list_parser.add_argument(
        "--json", action="store_true", help="Print as JSON."
    )

    sessions_show_parser = sessions_subparsers.add_parser(
        "show", help="Print one session's messages."
    )
    sessions_show_parser.add_argument("session_id", type=int)
    sessions_show_parser.add_argument(
        "--json", action="store_true", help="Print as JSON."
    )

    sessions_export_parser = sessions_subparsers.add_parser(
        "export", help="Export one session to a file or stdout."
    )
    sessions_export_parser.add_argument("session_id", type=int)
    sessions_export_parser.add_argument(
        "--format", choices=["md", "json"], default="md"
    )
    sessions_export_parser.add_argument(
        "--out", help="Output path. Default: stdout for json, ./session-<id>.md for md."
    )

    sessions_gc_parser = sessions_subparsers.add_parser(
        "gc", help="Delete old or orphaned chat sessions."
    )
    sessions_gc_parser.add_argument(
        "--older-than-days", type=int, default=180
    )
    sessions_gc_parser.add_argument(
        "--only-orphans",
        action="store_true",
        help="Only delete sessions whose graph file is missing.",
    )
    sessions_gc_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted without removing rows.",
    )
    sessions_gc_parser.add_argument(
        "--json", action="store_true", help="Print as JSON."
    )

    history_parser = subparsers.add_parser(
        "history",
        help="List, inspect, diff, and restore local graph checkpoints.",
    )
    history_parser.add_argument(
        "--journal-path",
        help="Optional history JSONL path. Defaults to .grc_agent/history/journal.jsonl.",
    )
    history_subparsers = history_parser.add_subparsers(dest="history_command")
    history_subparsers.required = True
    history_list_parser = history_subparsers.add_parser(
        "list",
        help="List local accepted checkpoints and failure journal entries.",
    )
    history_list_parser.add_argument(
        "--accepted-only",
        action="store_true",
        help="Show only accepted checkpoints.",
    )
    history_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Print records as JSON.",
    )
    history_show_parser = history_subparsers.add_parser(
        "show",
        help="Show one checkpoint or failure journal entry.",
    )
    history_show_parser.add_argument("id", help="History record ID.")
    history_show_parser.add_argument(
        "--json",
        action="store_true",
        help="Print full record as JSON.",
    )
    history_diff_parser = history_subparsers.add_parser(
        "diff",
        help="Diff two accepted checkpoint snapshots.",
    )
    history_diff_parser.add_argument("id1", help="Older history record ID.")
    history_diff_parser.add_argument("id2", help="Newer history record ID.")
    history_diff_parser.add_argument(
        "--json",
        action="store_true",
        help="Print diff as JSON.",
    )
    history_restore_parser = history_subparsers.add_parser(
        "restore",
        help="Restore one checkpoint to an explicit copy path.",
    )
    history_restore_parser.add_argument("id", help="History record ID.")
    history_restore_parser.add_argument(
        "--to",
        required=True,
        dest="to_path",
        help="Explicit copy path to write. Existing files are refused.",
    )
    history_restore_parser.add_argument(
        "--json",
        action="store_true",
        help="Print restore result as JSON.",
    )

    init_epilog = """
Examples:
  # Interactive: prompts for each value
  uv run grc-agent init

  # Non-interactive: seed with explicit values
  uv run grc-agent init --model llama3.2 --force

  # Print the resolved target path without writing
  uv run grc-agent init --print-target
"""
    init_parser = subparsers.add_parser(
        "init",
        help="Write a starter config.toml to ~/.config/grc_agent/.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=init_epilog.strip("\n"),
    )
    init_parser.add_argument(
        "--model",
        help="Ollama model name (e.g. llama3.2). Defaults to the built-in default.",
    )
    init_parser.add_argument(
        "--config-path",
        help="Override the destination file path. Defaults to ~/.config/grc_agent/config.toml.",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the destination file if it already exists.",
    )
    init_parser.add_argument(
        "--print-target",
        action="store_true",
        help="Print the resolved destination path and exit without writing.",
    )
    init_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the result as JSON.",
    )
    paths_parser = subparsers.add_parser(
        "paths",
        help="Print every filesystem location the package uses (config, history, caches).",
    )
    paths_parser.add_argument(
        "--json",
        action="store_true",
        help="Print all paths as a JSON object.",
    )

    return parser


def _positive_int_arg(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _maybe_translate_legacy_args(argv: list[str]) -> list[str]:
    command_names = {
        "doctor",
        "health",
        "release-manifest",
        "debug-bundle",
        "chat",
        "tool",
        "dogfood",
        "history",
        "init",
        "paths",
    }
    if not argv or any(arg in command_names for arg in argv):
        return argv

    if "--message" in argv:
        prefix: list[str] = []
        remainder = list(argv)
        while remainder:
            head = remainder[0]
            if head in {"--verbose", "-v"}:
                prefix.append(remainder.pop(0))
                continue
            if head == "--config" and len(remainder) >= 2:
                prefix.extend(remainder[:2])
                del remainder[:2]
                continue
            if head.startswith("--config="):
                prefix.append(remainder.pop(0))
                continue
            break
        return [*prefix, "chat", *remainder]

    return argv


def _parse_config_override(argv: list[str]) -> str | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config")
    args, _ = parser.parse_known_args(argv)
    return args.config


class Colors:
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def _colorize(color: str, text: str) -> str:
    """Colorize text with ANSI codes if stdout is a TTY."""
    if sys.stdout.isatty():
        return f"{color}{text}{Colors.RESET}"
    return text


def _parse_tool_call_arguments(raw: Any) -> dict[str, Any]:
    """Coerce a ``tool_call_arguments`` field to a ``dict``.

    ToolAgents stores arguments either as a dict (preferred) or as a
    JSON string. The printer layer previously had this same logic
    inlined; centralising it keeps the typed access consistent.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_tool_call_result(raw: Any) -> dict[str, Any]:
    """Coerce a ``tool_call_result`` field to a ``dict``.

    The runtime serialises tool results as JSON strings; the legacy
    printers used to treat them as dicts directly. Returns an empty
    dict on parse failure so downstream ``.get(...)`` calls stay
    safe.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _print_history(agent: GrcAgent, *, verbose: bool = False) -> None:
    """Render runtime history in a compact CLI-friendly form.

    Iterates the typed :class:`ChatMessage` objects returned by
    :meth:`agent.chat_history.get_messages` and uses attribute
    access on the Pydantic models (no dict shim, no legacy
    ``agent.history`` fallback). The pre-typed-history
    ``role == "session"`` branch is dropped: ``ChatMessageRole``
    has no such member.
    """
    from ToolAgents.data_models.messages import ChatMessageRole

    messages = agent.chat_history.get_messages()

    if verbose:
        print(_colorize(Colors.BOLD + Colors.YELLOW, "\n--- History ---"))
        for message in messages:
            for tc in message.get_tool_calls():
                args = _parse_tool_call_arguments(tc.tool_call_arguments)
                print(
                    f"{_colorize(Colors.BOLD + Colors.GREEN, 'Assistant')} "
                    f"called {_colorize(Colors.CYAN, tc.tool_call_name)}: "
                    f"{json.dumps(args)}"
                )
            for tr in message.get_tool_call_results():
                print(
                    f"{_colorize(Colors.BOLD + Colors.MAGENTA, 'Tool result')} "
                    f"for {tr.tool_call_name}: {tr.tool_call_result}"
                )
            text = message.get_as_text()
            if text:
                print(f"  [{message.role.value}] {text}")
        return

    print(_colorize(Colors.BOLD + Colors.YELLOW, "\n--- History ---"))
    for message in messages:
        if message.role == ChatMessageRole.User:
            text = message.get_as_text()
            if text:
                print(
                    f"  {_colorize(Colors.BOLD + Colors.CYAN, 'User:')} "
                    f"{text[:100]}"
                )
        elif message.role == ChatMessageRole.Assistant:
            for tc in message.get_tool_calls():
                print(
                    f"  {_colorize(Colors.BLUE, 'Tool call:')} "
                    f"{_colorize(Colors.BOLD, tc.tool_call_name)}"
                )
            text = message.get_as_text()
            if text:
                print(
                    f"  {_colorize(Colors.BOLD + Colors.GREEN, 'Assistant:')} "
                    f"{text[:120]}"
                )
        elif message.role == ChatMessageRole.Tool:
            for tr in message.get_tool_call_results():
                name = tr.tool_call_name
                content = _parse_tool_call_result(tr.tool_call_result)
                if content:
                    name = content.get("tool") or name
                ok = content.get("ok") if content else None
                msg = content.get("message", "") if content else ""
                status = (
                    _colorize(Colors.GREEN, "ok")
                    if ok is True
                    else _colorize(Colors.RED, "FAILED")
                    if ok is False
                    else "unknown"
                )
                line = f"  {_colorize(Colors.BOLD, name)}: {status}"
                if ok is False and msg:
                    line += f" — {_colorize(Colors.YELLOW, msg[:80])}"
                print(line)


def _print_turn_operations(agent: GrcAgent, *, start_index: int) -> None:
    """Render concise operation details for the just-completed turn.

    Iterates :class:`ChatMessage` objects from the typed chat
    history; tool calls are read off
    :meth:`ChatMessage.get_tool_calls` and tool results are
    JSON-decoded from :attr:`ToolCallResultContent.tool_call_result`.
    """
    from ToolAgents.data_models.messages import ChatMessageRole

    messages = agent.chat_history.get_messages()
    lines: list[str] = []
    requested: list[str] = []
    for message in messages[start_index:]:
        if message.role == ChatMessageRole.Assistant:
            for tc in message.get_tool_calls():
                args = _parse_tool_call_arguments(tc.tool_call_arguments)
                detail = _tool_detail_from_args(args)
                requested.append(
                    f"{_colorize(Colors.BOLD + Colors.CYAN, tc.tool_call_name)}{detail}"
                )
        elif message.role == ChatMessageRole.Tool:
            for tr in message.get_tool_call_results():
                content = _parse_tool_call_result(tr.tool_call_result)
                if not content:
                    continue
                name = content.get("tool") or tr.tool_call_name
                ok = content.get("ok")
                status = (
                    _colorize(Colors.GREEN, "ok")
                    if ok is True
                    else _colorize(Colors.RED, "FAILED")
                    if ok is False
                    else "unknown"
                )
                detail = _tool_detail_from_args(content)
                validation = _validation_status(content)
                dirty = _dirty_status(content)
                line = f"{_colorize(Colors.BOLD, name)}{detail}: {status}"
                extras = [item for item in (validation, dirty) if item]
                if extras:
                    line += f" ({', '.join(extras)})"
                lines.append(line)
    if not lines and not requested:
        return
    print(_colorize(Colors.BOLD + Colors.CYAN, "\nOperations:"))
    if requested:
        print(f"  requested: {'; '.join(requested)}")
    for line in lines:
        print(f"  {line}")


def _tool_detail_from_args(payload: dict[str, Any]) -> str:
    mode = payload.get("mode")
    operation_summary = payload.get("operation_summary")
    detail = mode or operation_summary
    return f"[{detail}]" if isinstance(detail, str) and detail else ""


def _validation_status(payload: dict[str, Any]) -> str | None:
    validation = payload.get("validation_result") or payload.get("validation")
    if isinstance(validation, dict):
        status = validation.get("status")
        if status is None and "valid" in validation:
            status = "valid" if validation.get("valid") else "invalid"
        if status is not None:
            return f"validation={status}"
    active = payload.get("active_session")
    if isinstance(active, dict):
        validation = active.get("validation")
        if isinstance(validation, dict) and validation.get("status") is not None:
            return f"validation={validation.get('status')}"
    return None


def _dirty_status(payload: dict[str, Any]) -> str | None:
    active = payload.get("active_session")
    if isinstance(active, dict) and "dirty" in active:
        return f"dirty={active.get('dirty')}"
    return None


def _print_active_session(agent: GrcAgent, *, verbose: bool = False) -> None:
    """Render the currently bound session before running the chat loop."""
    active_session = agent.active_session_snapshot()
    print(_colorize(Colors.BOLD + Colors.CYAN, "\n--- Active Session ---"))
    if active_session is None:
        print("No active flowgraph session.")
        return
    validation = active_session["validation"]["status"]
    path = active_session.get("path") or "(new graph)"
    val_color = Colors.GREEN if validation == "valid" else Colors.RED
    val_str = _colorize(val_color, validation)
    dirty_str = _colorize(Colors.YELLOW, "True") if active_session["dirty"] else "False"
    if verbose:
        print(
            f"{_colorize(Colors.BOLD, path)} "
            f"(graph_id={active_session['graph_id']}, "
            f"state_revision={active_session['state_revision']}, "
            f"dirty={dirty_str}, validation={val_str})"
        )
    else:
        print(
            f"{_colorize(Colors.BOLD, path)} "
            f"(graph_id={active_session['graph_id']}, "
            f"dirty={dirty_str}, validation={val_str})"
        )


def _prepare_retrieval() -> tuple[int, str | None]:
    """Run the bounded retrieval startup check and return the resolved catalog root."""
    readiness = initialize_retrieval()
    if not readiness["ok"]:
        print("\n--- Retrieval ---")
        print(readiness["message"])
        return 1, None
    return 0, readiness.get("catalog_root")


def _load_initial_session(file_path: str | None) -> FlowgraphSession:
    session = FlowgraphSession()
    if file_path is not None:
        loaded = load_grc_session(file_path)
        if isinstance(loaded, dict):
            raise CliError(loaded)
        if not loaded.validate():
            logger.warning("Graph loaded with validation failures. Ensure it is fixed before execution.")
        session = loaded
    return session


class CliError(RuntimeError):
    """CLI-friendly structured error."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(str(payload.get("message", "CLI error.")))
        self.payload = payload


def _print_cli_error(payload: dict[str, Any], *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(_colorize(Colors.BOLD + Colors.RED, "\n--- Error ---"))
    print(payload.get("message", "Command failed."))
    error_type = str(payload.get("error_type") or "")
    hint = ""
    if error_type == ErrorCode.FILE_LOAD_ERROR:
        hint = (
            "Hint: verify the .grc path exists and is readable. "
            "Use a copied graph, not an original installed/example file."
        )
    elif error_type == ErrorCode.INVALID_GRC:
        hint = (
            "Hint: this file is not a valid .grc payload for the current loader. "
            "Open it in GNU Radio Companion and save a clean copy."
        )
    elif error_type == ErrorCode.RETRIEVAL_NOT_READY:
        hint = (
            "Hint: run `uv run grc-agent doctor` and ensure GNU Radio catalog "
            "metadata is discoverable."
        )
    elif error_type == ErrorCode.LLAMA_SERVER_MISSING:
        hint = (
            "Hint: ensure Ollama is running or OpenRouter API key is set. "
            "See the README install table. To use Ollama, make sure the "
            "Ollama service is running; to use OpenRouter, set the "
            "OPENROUTER_API_KEY environment variable."
        )
    elif error_type == ErrorCode.GRCC_MISSING:
        hint = (
            "Hint: install GNU Radio 3.10.x via your package manager and "
            "ensure `grcc` is on PATH. See the README install table."
        )
    elif error_type == ErrorCode.MODEL_NOT_FOUND:
        hint = (
            "Hint: set `[llama].model` in your config to an Ollama model name, "
            "or set `[llama].backend` to \"openrouter\" and configure your "
            "OPENROUTER_API_KEY. Use `uv run grc-agent init` to write a starter config."
        )
    if hint:
        print(_colorize(Colors.YELLOW, hint))


def _is_installed_example_path(file_path: str | None) -> bool:
    """Return True when the path resolves under known installed GNU example roots."""
    if not file_path:
        return False
    try:
        resolved = Path(file_path).expanduser().resolve(strict=False)
    except Exception:
        return False
    for root in _INSTALLED_GRAPH_ROOTS:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _reject_if_original_graph_path(file_path: str | None) -> bool:
    """Reject direct edits on installed GNU Radio example paths."""
    if not _is_installed_example_path(file_path):
        return False
    print("\n--- Error ---")
    print("Refusing to open an installed GNU Radio example directly.")
    print(
        "Hint: copy the graph first, then run chat on the copied path. "
        "Example: cp /usr/share/gnuradio/examples/.../file.grc /tmp/work.grc"
    )
    return True


def _parse_tool_kwargs(raw_arguments: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise ValueError("--args must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("--args must decode to a JSON object.")
    return parsed


def _run_llama_runtime(
    file_path: str | None,
    user_message: str | None,
    config: AppConfig,
    server_url: str,
    model: str | None,
    api_key: str | None,
    *,
    agentic: bool = False,
    max_tool_rounds: int | None = None,
    verbose: bool = False,
    json_output: bool = False,
) -> int:
    """Run one or more bounded model-backed turns against the routed runtime."""
    original_stdout = sys.stdout
    if json_output:
        sys.stdout = sys.stderr
    effective_max_tool_rounds = _effective_max_tool_rounds(
        config,
        agentic=agentic,
        requested=max_tool_rounds,
    )
    effective_request_timeout = _effective_request_timeout(config, agentic=agentic)

    # Interactive provider picker on first launch (no prior choice
    # recorded in user preferences). Skipped when stdin is not a
    # TTY (piped input, CI, scripted runs).
    from grc_agent.config import run_cli_setup

    if not run_cli_setup(config=config, is_tty=sys.stdin.isatty()):
        print("Provider selection cancelled; exiting.", flush=True)
        return 0

    # Detect-only Ollama probe. The user is responsible for running
    # ``ollama serve`` and ``ollama pull <model_name>`` themselves;
    # the shared helper (consumed by the GUI's setup widget too)
    # returns a structured status we render verbatim so both entry
    # points surface the exact same wording to the user. No daemon
    # management, no auto-pull, no Popen.
    #
    # When the config has no model name (the default — there is no
    # "configured model" assumption) we transparently fall back to
    # the first installed tag the server reports, mirroring the GUI
    # "Models on this machine" list. The user can still override
    # with ``--model <name>`` on the command line.
    from grc_agent.model_manager import probe_ollama_backend

    if config.llama.backend == "ollama":
        effective_model = model or config.llama.model
        ollama_status = probe_ollama_backend(
            config.llama.server_url,
            effective_model,
        )
        if not ollama_status.server_reachable:
            print(_colorize(Colors.BOLD + Colors.RED, "\n--- Ollama ---"))
            print(ollama_status.hint)
            print(
                _colorize(
                    Colors.YELLOW,
                    "\nHint: GRC Agent does not start the Ollama daemon "
                    "and does not download models. Run the commands above "
                    "in a new terminal, then retry.",
                )
            )
            return 1
        if effective_model and not ollama_status.model_available:
            # The user asked for a tag that isn't installed; warn
            # and fall back to the first installed model instead of
            # 404-ing on the first chat turn.
            print(_colorize(Colors.YELLOW, "\n--- Ollama ---"))
            print(ollama_status.hint)
            if ollama_status.available_models:
                print(
                    _colorize(
                        Colors.YELLOW,
                        f"Falling back to the first installed model: "
                        f"`{ollama_status.available_models[0]}`.",
                    )
                )
                effective_model = ollama_status.available_models[0]
            else:
                print(
                    _colorize(
                        Colors.YELLOW,
                        "No models are installed yet. Use "
                        "`ollama pull <model_name>` to download one.",
                    )
                )
                return 1
        elif not effective_model and ollama_status.available_models:
            # No model supplied anywhere; pick the first installed
            # tag so the chat path has something to send to Ollama.
            effective_model = ollama_status.available_models[0]
            logger.info(
                "ollama_pick_default model=%s", effective_model,
            )
        # Reflect the resolved alias back into the runtime bootstrap
        # by shadowing the local variable; bootstrap_runtime reads
        # ``model_alias`` from this argument.
        model = effective_model

    if file_path is not None:
        if _reject_if_original_graph_path(file_path):
            return 1
        print(f"Loading {file_path}...")
        try:
            session = _load_initial_session(file_path)
        except CliError as exc:
            _print_cli_error(exc.payload)
            return 1
    else:
        print("Starting new empty graph...")
        session = FlowgraphSession.create()
    logger.info("chat_start file=%s message=%s", file_path, user_message[:80] if user_message else None)

    from grc_agent.startup import bootstrap_runtime

    result = bootstrap_runtime(
        config,
        init_retrieval=True,
        api_key=api_key,
        server_url=server_url,
        model_alias=model,
    )

    if not result.retrieval_ok:
        print("\n--- Retrieval ---")
        print(result.errors[0] if result.errors else "Retrieval initialization failed.")
        return 1

    if result.launch_status == "failed":
        logger.error("launcher_failed error=%s", result.errors[-1] if result.errors else "unknown")
        print("\n--- Launcher ---")
        print(result.errors[-1] if result.errors else "Failed to ensure model server.")
        if result.error_type:
            from grc_agent._payload import build_error_payload
            _print_cli_error(
                build_error_payload(
                    error_type=result.error_type,
                    message=result.errors[-1] if result.errors else "Launcher failed.",
                )
            )
        return 1

    agent = GrcAgent(
        session,
        catalog_root=result.catalog_root,
        config=config.agent,
        llama_server_url=result.server_url,
        llama_model=result.model_alias,
        llama_request_timeout_seconds=effective_request_timeout,
    )
    _print_active_session(agent, verbose=verbose)
    if agentic or max_tool_rounds is not None:
        print(
            "Tool budget: "
            f"max_tool_rounds={effective_max_tool_rounds}, "
            f"request_timeout={int(effective_request_timeout)}s"
        )

    provider_config = result.provider_config
    provider_config.timeout_seconds = effective_request_timeout

    if result.launch_status == "started":
        logger.info("server_started url=%s", result.server_url)
        print(
            f"Started model server for {result.model_alias} "
            f"at {result.server_url} (health verified)"
        )
    else:
        logger.info("server_reused url=%s", result.server_url)
        print(
            f"Reusing model server for {result.model_alias} "
            f"at {result.server_url} (health verified)"
        )

    if user_message is not None:
        return _run_single_turn(
            agent,
            provider_config,
            user_message,
            result.model_alias,
            config,
            max_tool_rounds=effective_max_tool_rounds,
            verbose=verbose,
            json_output=json_output,
            original_stdout=original_stdout,
        )

    return _run_repl_loop(
        agent,
        provider_config,
        result.model_alias,
        config,
        max_tool_rounds=effective_max_tool_rounds,
        verbose=verbose,
    )


def _effective_max_tool_rounds(
    config: AppConfig,
    *,
    agentic: bool,
    requested: int | None,
) -> int:
    if requested is not None:
        return requested
    if agentic:
        return AGENTIC_MAX_TOOL_ROUNDS
    return config.llama.max_tool_rounds


def _effective_request_timeout(config: AppConfig, *, agentic: bool) -> float:
    if not agentic:
        return config.llama.request_timeout_seconds
    return max(config.llama.request_timeout_seconds, AGENTIC_REQUEST_TIMEOUT_SECONDS)


def _maybe_render_pending_clarification(agent: GrcAgent) -> bool:
    """If a pending clarification exists, render it and return True."""
    if agent._pending_clarification is None:
        return False
    prompt_text = render_clarification_prompt(agent._pending_clarification)
    print()
    print("--- Clarification required ---")
    print(prompt_text)
    return True


def _run_single_turn(
    agent: GrcAgent,
    provider_config: ToolAgentsLlamaProviderConfig,
    user_message: str,
    model: str | None,
    config: AppConfig | None = None,
    *,
    max_tool_rounds: int | None = None,
    verbose: bool = False,
    json_output: bool = False,
    original_stdout: Any | None = None,
) -> int:
    """Run one bounded model turn and print the result."""
    if config is None:
        config = load_app_config()
    round_limit = (
        config.llama.max_tool_rounds
        if max_tool_rounds is None
        else max_tool_rounds
    )
    try:
        history_start = len(agent.chat_history.get_messages())
        result = run_bounded_toolagents_turn(
            agent,
            provider_config,
            user_message,
            model=model,
            mvp_tool_profile=True,
            max_tool_rounds=round_limit,
        )
    except Exception as exc:
        if json_output and original_stdout is not None:
            sys.stdout = original_stdout
            print(json.dumps({"ok": False, "message": str(exc)}))
        else:
            print("\n--- Runtime ---")
            print(str(exc))
        return 1

    if not json_output:
        print(f"Using model {result.get('model', '?')}")
        if result["ok"]:
            print("\nAssistant:")
            print(result["assistant_text"])
        else:
            print("\n--- Runtime ---")
            print(result["message"])

        # Check for pending clarification after turn completes
        _maybe_render_pending_clarification(agent)

        if verbose:
            _print_history(agent, verbose=True)
        else:
            _print_turn_operations(agent, start_index=history_start)
    else:
        if original_stdout is not None:
            sys.stdout = original_stdout

        from ToolAgents.data_models.messages import ChatMessageRole

        operations = []
        messages = agent.chat_history.get_messages()
        for message in messages[history_start:]:
            if message.role != ChatMessageRole.Assistant:
                continue
            for tc in message.get_tool_calls():
                args = _parse_tool_call_arguments(tc.tool_call_arguments)
                operations.append({"name": tc.tool_call_name, "arguments": args})

        final_state = agent.active_session_snapshot() or {}

        payload = {
            "ok": result["ok"],
            "assistant_text": result.get("assistant_text", ""),
            "message": result.get("message", ""),
            "operations": operations,
            "state_revision": final_state.get("state_revision"),
            "validation_status": final_state.get("validation", {}).get("status"),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))

    return 0 if result["ok"] else 1


def _run_repl_loop(
    agent: GrcAgent,
    provider_config: ToolAgentsLlamaProviderConfig,
    model: str | None,
    config: AppConfig | None = None,
    *,
    max_tool_rounds: int | None = None,
    verbose: bool = False,
) -> int:
    """Run an interactive REPL loop over the current agent and session."""
    if config is None:
        config = load_app_config()
    round_limit = (
        config.llama.max_tool_rounds
        if max_tool_rounds is None
        else max_tool_rounds
    )
    print(_colorize(Colors.BOLD + Colors.CYAN, "\nInteractive chat. Type /save [path], /history, /quit, or /exit.\n"))
    last_exit_code = 0

    while True:
        try:
            user_input = input(_colorize(Colors.BOLD + Colors.BLUE, "You: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in ("/quit", "/exit"):
            break
        if user_input.lower() == "/help":
            print("\nAvailable commands:")
            print("  /save [path]    - Save current graph state (optionally to a specific path)")
            print("  /history        - Show conversation and graph mutation history")
            print("  /help           - Show this help message")
            print("  /quit or /exit  - Exit the session\n")
            continue
        if user_input.lower() == "/history":
            _print_history(agent, verbose=True)
            print()
            continue
        if user_input.lower().startswith("/save"):
            last_exit_code = _run_repl_save_command(agent, user_input)
            print()
            continue

        # If pending clarification exists, resolve before model turn
        if agent._pending_clarification is not None:
            resolved = agent.resolve_pending_clarification(
                user_input,
                model_tool_call=True,
            )
            mode = resolved.get("mode")
            if mode == "executed":
                tool_result = resolved.get("tool_result", {})
                ok = tool_result.get("ok")
                msg = tool_result.get("message", "")
                status_str = _colorize(Colors.GREEN, "OK") if ok else _colorize(Colors.RED, "FAILED")
                print(f"\n{_colorize(Colors.BOLD, f'--- Executed {status_str} ---')}")
                if msg:
                    print(msg)
                _maybe_render_pending_clarification(agent)
                continue
            elif mode == "expired":
                print(_colorize(Colors.BOLD + Colors.RED, "\n--- Expired ---"))
                print(resolved.get("text", "The question is no longer valid."))
                continue
            elif mode == "reminder":
                print(_colorize(Colors.BOLD + Colors.YELLOW, "\n--- Reminder ---"))
                print(resolved.get("text", ""))
                _maybe_render_pending_clarification(agent)
                continue
            elif mode == "custom":
                # Fall through to normal model flow with the custom text
                user_input = resolved.get("custom_hint") or user_input
            # mode == "none" should not happen when _pending_clarification is not None

        try:
            history_start = len(agent.chat_history.get_messages())
            result = run_bounded_toolagents_turn(
                agent,
                provider_config,
                user_input,
                model=model,
                mvp_tool_profile=True,
                max_tool_rounds=round_limit,
            )
        except Exception as exc:
            print(f"\n{_colorize(Colors.BOLD + Colors.RED, '--- Runtime Error ---')}\n{exc}")
            last_exit_code = 1
            continue

        if result["ok"]:
            print(f"\n{_colorize(Colors.BOLD + Colors.GREEN, 'Assistant:')}\n{result['assistant_text']}")
        else:
            print(f"\n{_colorize(Colors.BOLD + Colors.RED, '--- Runtime ---')}\n{result['message']}")
            last_exit_code = 1

        _maybe_render_pending_clarification(agent)
        if verbose:
            _print_history(agent, verbose=True)
        else:
            _print_turn_operations(agent, start_index=history_start)
        print()

    return last_exit_code


def _run_repl_save_command(agent: GrcAgent, user_input: str) -> int:
    """Run a deterministic manual REPL save without asking the model to route it."""
    try:
        parts = shlex.split(user_input)
    except ValueError as exc:
        print("\n--- Save FAILED ---")
        print(f"Could not parse /save command: {exc}")
        return 1
    if not parts or parts[0] != "/save":
        print("\n--- Save FAILED ---")
        print("Usage: /save [path] [--overwrite]")
        return 1

    overwrite = False
    paths: list[str] = []
    for part in parts[1:]:
        if part == "--overwrite":
            overwrite = True
        else:
            paths.append(part)
    if len(paths) > 1:
        print("\n--- Save FAILED ---")
        print("Usage: /save [path] [--overwrite]")
        return 1

    path = paths[0] if paths else None
    kwargs: dict[str, Any] = {"overwrite": overwrite}
    if path is not None:
        kwargs["path"] = path
    result = agent.execute_tool(
        "save_graph",
        kwargs,
        model_tool_call=False,
    )
    ok = result.get("ok") is True
    print(f"\nSave {'OK' if ok else 'FAILED'}")
    message = result.get("message")
    if message:
        print(message)
    saved_path = result.get("path")
    if saved_path:
        print(f"Saved: {saved_path}")
    validation = result.get("validation_result") or result.get("validation")
    if isinstance(validation, dict):
        status = validation.get("status")
        if status is not None:
            print(f"Validation: {status}")
    active = result.get("active_session")
    if isinstance(active, dict) and "dirty" in active:
        print(f"Dirty: {active.get('dirty')}")
    return 0 if ok else 1


def _run_tool_command(
    tool_name: str, tool_kwargs: dict[str, Any], file_path: str | None, config: AppConfig
) -> int:
    """Execute one routed runtime tool directly and print the structured result."""
    try:
        session = _load_initial_session(file_path)
    except CliError as exc:
        print(json.dumps(exc.payload, indent=2, sort_keys=True))
        return 1
    catalog_root: str | None = None
    if tool_name in _RETRIEVAL_READY_TOOLS:
        retrieval_status, catalog_root = _prepare_retrieval()
        if retrieval_status != 0:
            return retrieval_status

    agent = GrcAgent(
        session,
        catalog_root=catalog_root,
        config=config.agent,
        llama_server_url=config.llama.server_url,
        llama_model=config.llama.model,
        llama_request_timeout_seconds=config.llama.request_timeout_seconds,
    )
    result = agent.execute_tool(tool_name, tool_kwargs)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


def _build_health_report(config: AppConfig) -> dict[str, Any]:
    """Return end-to-end runtime readiness for the active config."""
    readiness = initialize_retrieval()
    catalog_root = readiness.get("catalog_root") if readiness.get("ok") else None
    session = FlowgraphSession()
    agent = GrcAgent(
        session,
        catalog_root=catalog_root,
        config=config.agent,
        llama_server_url=config.llama.server_url,
        llama_model=config.llama.model,
        llama_request_timeout_seconds=config.llama.request_timeout_seconds,
    )
    report = agent.health_check()
    status_reasons: list[str] = []
    if not readiness.get("ok"):
        report["retrieval_message"] = readiness.get("message", "Retrieval not ready.")
        status_reasons.append("retrieval_not_ready")
    report["llama_max_tokens"] = config.llama.max_tokens
    report["llama_max_tool_rounds"] = config.llama.max_tool_rounds
    # Provider-agnostic telemetry. "model_ready" reflects whether the
    # agent's tool surface is wired up; "context_verified" stays False
    # because this code path does not actually probe the LLM's context
    # window. "actual_context_tokens" reports the configured max_tokens
    # (it is the requested window, not a server-measured one).
    report["provider_type"] = config.llama.backend
    report["model_ready"] = bool(report.get("agent_core_ready"))
    report["context_verified"] = False
    report["actual_context_tokens"] = config.llama.max_tokens

    if not report.get("agent_core_ready"):
        status_reasons.append("agent_core_not_ready")
    if not report.get("retrieval_ready"):
        status_reasons.append("retrieval_not_ready")
    try:
        report["toolagents_version"] = metadata.version("ToolAgents")
    except metadata.PackageNotFoundError:
        report["toolagents_version"] = None

    unique_reasons = list(dict.fromkeys(status_reasons))
    if "llama_unreachable" in unique_reasons or "agent_core_not_ready" in unique_reasons:
        report["status"] = "not_ready"
    elif unique_reasons:
        report["status"] = "degraded"
    else:
        report["status"] = "ok"
    report["status_reasons"] = unique_reasons
    return report


def _run_health_command(config: AppConfig) -> int:
    """Print a structured agent health check and return 0 when healthy."""
    report = _build_health_report(config)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


def _sha256_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git_output(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            check=True,
            cwd=Path(__file__).resolve().parents[2],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return None
    return completed.stdout.strip()


def _build_release_manifest(config: AppConfig) -> dict[str, Any]:
    """Build a reproducible release evidence manifest for the current runtime."""
    session = FlowgraphSession()
    agent = GrcAgent(session, config=config.agent)
    surface = MVP_TOOL_SURFACE
    model_schemas = agent.get_tool_schemas_for_turn(set(surface.model_tool_names))
    policy_payload = {
        "tool_surface": surface.name,
        "model_tool_names": list(surface.model_tool_names),
        "internal_tool_names": list(surface.internal_tool_names),
        "assistant_text_fallback_enabled": surface.assistant_text_fallback_enabled,
        "default_max_tool_rounds": surface.default_max_tool_rounds,
    }
    dirty_files = (_git_output("status", "--porcelain") or "").splitlines()
    health_report = _build_health_report(config)
    return {
        "ok": True,
        "git": {
            "branch": _git_output("rev-parse", "--abbrev-ref", "HEAD"),
            "commit": _git_output("rev-parse", "HEAD"),
            "dirty": bool(dirty_files),
            "dirty_files": dirty_files,
        },
        "runtime": {
            "model_alias": config.llama.model,
            "server_url": config.llama.server_url,

            "actual_context_tokens": health_report.get("actual_context_tokens"),
            "health_status": health_report.get("status"),
            "health_status_reasons": health_report.get("status_reasons", []),
        },
        "hashes": {
            "prompt_sha256": _sha256_payload(agent.get_system_prompt()),
            "schema_sha256": _sha256_payload(model_schemas),
            "policy_sha256": _sha256_payload(policy_payload),
        },
        "tool_surface": policy_payload,
        "eval_gates": {
            "lint_src_tests": "uv run ruff check src/ tests/",
            "lint_repo": "uv run ruff check",
            "unit": "uv run python -m unittest",
            "docs_answer_eval": "uv run python -m tests.retrieval_eval.grc_docs_answer_eval",
            "doctor": "uv run grc-agent doctor",
            "health": "uv run grc-agent health",
        },
        "fixture_ids": ["tests/data/<canonical_fixture>.grc"],
        "health": health_report,
    }


def _run_release_manifest_command(config: AppConfig) -> int:
    """Print the current release evidence manifest."""
    print(json.dumps(_build_release_manifest(config), indent=2, sort_keys=True))
    return 0


def _run_debug_bundle_command(
    *,
    config: AppConfig,
    config_path: str | None,
    output_path: str,
) -> int:
    doctor_report = run_doctor(
        config_path=config_path,
        check_retrieval=True,
        check_llama=False,
    )
    health_report = _build_health_report(config)
    release_manifest = _build_release_manifest(config)
    repo_root = Path(__file__).resolve().parents[2]
    payload = build_debug_bundle(
        config=config,
        config_path=config_path,
        doctor_report=doctor_report,
        health_report=health_report,
        release_manifest=release_manifest,
        repo_root=repo_root,
    )
    written = write_debug_bundle(output_path, payload)
    print(json.dumps(debug_bundle_summary(payload, written), indent=2, sort_keys=True))
    return 0


def _print_dogfood_payload(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if not payload.get("ok"):
        print(payload.get("message", "Dogfood command failed."))
        return
    if payload.get("tool") == "record_dogfood_case":
        record = payload.get("record", {})
        print(f"Recorded dogfood case: {record.get('task_type', 'other')}")
        print(f"Category: {record.get('failure_category', 'no_failure')}")
        print(f"Path: {payload.get('intake_path', '')}")
        return
    if payload.get("tool") == "summarize_dogfood_cases":
        print(
            f"Dogfood cases: {payload.get('total_records', 0)} records, "
            f"{payload.get('cluster_count', 0)} clusters"
        )
        for cluster in payload.get("clusters", [])[:10]:
            prompts = ", ".join(cluster.get("representative_prompts", [])[:2])
            print(
                f"- {cluster.get('count')}x {cluster.get('cluster_id')} "
                f"[{cluster.get('recommendation')}]: {prompts}"
            )
        for warning in payload.get("warnings", []) or []:
            print(f"Warning: {warning}")


def _print_history_records(records: list[dict[str, Any]], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"ok": True, "records": records}, indent=2, sort_keys=True))
        return
    print(f"History records: {len(records)}")
    for record in records:
        status = "accepted" if record.get("accepted") else "failure"
        print(
            f"- {record.get('id')} [{status}] "
            f"{record.get('tool_name')} {record.get('operation_type')} "
            f"rev={record.get('state_revision')} path={record.get('graph_path')}"
        )


def _print_history_record(record: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(record, indent=2, sort_keys=True))
        return
    print(f"ID: {record.get('id')}")
    print(f"Type: {record.get('record_type')} accepted={record.get('accepted')}")
    print(f"Tool: {record.get('tool_name')} operation={record.get('operation_type')}")
    print(f"Revision: {record.get('state_revision')}")
    print(f"Path: {record.get('graph_path')}")
    if record.get("save_path"):
        print(f"Save path: {record.get('save_path')}")
    print(f"Before: {record.get('before_hash')}")
    print(f"After: {record.get('after_hash')}")
    validation = record.get("validation_result")
    if isinstance(validation, dict):
        print(f"Validation: {validation.get('status')} returncode={validation.get('returncode')}")
    delta = record.get("graph_delta")
    if isinstance(delta, dict):
        print(
            "Delta: "
            f"changed={delta.get('changed')} "
            f"blocks+={len(delta.get('added_blocks', []))} "
            f"blocks-={len(delta.get('removed_blocks', []))} "
            f"blocks~={len(delta.get('changed_blocks', []))} "
            f"connections+={len(delta.get('added_connections', []))} "
            f"connections-={len(delta.get('removed_connections', []))}"
        )


def _print_history_payload(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if not payload.get("ok"):
        print(payload.get("message", "History command failed."))
        if str(payload.get("error_type")) == "restore_target_exists":
            print("Hint: choose a new `--to` path; restore never overwrites existing files.")
        return
    if "graph_delta" in payload:
        delta = payload["graph_delta"]
        print(f"History diff: {payload.get('from')} -> {payload.get('to')}")
        print(json.dumps(delta, indent=2, sort_keys=True))
        text_diff = payload.get("text_diff", [])
        if text_diff:
            print("\n".join(text_diff[:80]))
        return
    if "path" in payload:
        print(f"Restored checkpoint {payload.get('id')} to {payload.get('path')}")
        validation = payload.get("validation", {})
        print(f"Validation: {validation.get('status')} returncode={validation.get('returncode')}")


def _run_history_command(args: argparse.Namespace) -> int:
    journal = GraphHistoryJournal(args.journal_path)
    try:
        if args.history_command == "list":
            records = journal.list_records(accepted_only=args.accepted_only)
            _print_history_records(records, json_output=args.json)
            return 0
        if args.history_command == "show":
            record = journal.get_record(args.id)
            _print_history_record(record, json_output=args.json)
            return 0
        if args.history_command == "diff":
            payload = journal.diff_records(args.id1, args.id2)
            _print_history_payload(payload, json_output=args.json)
            return 0
        if args.history_command == "restore":
            payload = journal.restore_record(args.id, args.to_path)
            _print_history_payload(payload, json_output=args.json)
            return 0 if payload.get("ok") else 1
    except KeyError as exc:
        payload = build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST,
            message=f"History record not found: {exc.args[0]}",
        )
        _print_history_payload(payload, json_output=getattr(args, "json", False))
        return 1
    except Exception as exc:
        payload = build_error_payload(
            error_type=ErrorCode.INTERNAL_ERROR,
            message=str(exc),
        )
        _print_history_payload(payload, json_output=getattr(args, "json", False))
        return 1
    return 2


def _run_sessions_command(args: argparse.Namespace) -> int:
    """Dispatch the ``grc-agent sessions`` subcommands."""
    try:
        if args.sessions_command == "list":
            return _run_sessions_list(args)
        if args.sessions_command == "show":
            return _run_sessions_show(args)
        if args.sessions_command == "export":
            return _run_sessions_export(args)
        if args.sessions_command == "gc":
            return _run_sessions_gc(args)
    except Exception as exc:
        payload = build_error_payload(
            error_type=ErrorCode.INTERNAL_ERROR,
            message=str(exc),
        )
        print(json.dumps(payload, sort_keys=True))
        return 1
    return 2


def _resolve_sessions_db_path(args: argparse.Namespace) -> Path:
    if getattr(args, "db", None):
        return Path(args.db).expanduser()
    from grc_agent.sessions_store import default_sessions_db_path

    return default_sessions_db_path()


def _run_sessions_list(args: argparse.Namespace) -> int:
    from grc_agent.sessions_store import list_sessions_sync

    db = _resolve_sessions_db_path(args)
    sessions = list_sessions_sync(
        db,
        graph_path_substring=getattr(args, "graph", None),
        limit=int(getattr(args, "limit", 50) or 50),
    )
    if args.json:
        payload = {
            "ok": True,
            "count": len(sessions),
            "sessions": [
                {
                    "id": s.id,
                    "graph_path": s.graph_path,
                    "graph_hash": s.graph_hash,
                    "started_at": s.started_at,
                    "ended_at": s.ended_at,
                    "model_alias": s.model_alias,
                    "backend": s.backend,
                    "title": s.title,
                    "message_count": s.message_count,
                }
                for s in sessions
            ],
        }
        print(json.dumps(payload, sort_keys=True))
        return 0
    if not sessions:
        print("No chat sessions found.")
        return 0
    print(f"Found {len(sessions)} session(s):")
    for s in sessions:
        status = "open" if s.ended_at is None else "closed"
        print(
            f"  [{s.id:>6}] {s.started_at}  msgs={s.message_count:>3}  "
            f"status={status:<6}  model={s.model_alias or '(unknown)':<24}  "
            f"graph={s.graph_path}"
        )
        if s.title:
            print(f"          title: {s.title}")
    return 0


def _run_sessions_show(args: argparse.Namespace) -> int:
    from grc_agent.sessions_store import list_messages_sync

    db = _resolve_sessions_db_path(args)
    messages = list_messages_sync(db, int(args.session_id))
    if args.json:
        payload = {
            "ok": True,
            "session_id": int(args.session_id),
            "messages": [
                {
                    "id": m.id,
                    "sequence": m.sequence,
                    "role": m.role,
                    "text": m.text,
                    "payload": m.payload,
                    "created_at": m.created_at,
                }
                for m in messages
            ],
        }
        print(json.dumps(payload, sort_keys=True))
        return 0
    if not messages:
        print(f"No messages for session {args.session_id}.")
        return 0
    for m in messages:
        print(f"--- [{m.sequence}] {m.role} @ {m.created_at} ---")
        if m.text:
            print(m.text)
        if m.payload:
            print(json.dumps(m.payload, indent=2, sort_keys=True))
    return 0


def _run_sessions_export(args: argparse.Namespace) -> int:
    from grc_agent.sessions_store import export_markdown_sync

    db = _resolve_sessions_db_path(args)
    if args.format == "md":
        content = export_markdown_sync(db, int(args.session_id))
    else:
        from grc_agent.sessions_store import list_messages_sync

        messages = list_messages_sync(db, int(args.session_id))
        content = json.dumps(
            {"session_id": int(args.session_id), "messages": [
                {
                    "id": m.id,
                    "sequence": m.sequence,
                    "role": m.role,
                    "text": m.text,
                    "payload": m.payload,
                    "created_at": m.created_at,
                }
                for m in messages
            ]},
            indent=2,
            sort_keys=True,
        )
    if args.out:
        target = Path(args.out).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        print(f"Exported session {args.session_id} to {target}.")
        return 0
    print(content)
    return 0


def _run_sessions_gc(args: argparse.Namespace) -> int:
    """Garbage-collect sessions.

    The actual DB write goes through the writer thread; the CLI
    command is short-lived so we open a temporary store just to
    issue the GC. We use the ``gc()`` API which is itself
    synchronous.
    """
    from grc_agent.sessions_store import list_sessions_sync, session_store_cm

    db = _resolve_sessions_db_path(args)
    if args.dry_run:
        # Dry-run reads the current state and computes what
        # would be deleted without touching the DB.
        sessions = list_sessions_sync(db, limit=10_000)
        only_orphans = bool(getattr(args, "only_orphans", False))
        if only_orphans:
            targets = [s for s in sessions if not Path(s.graph_path).exists()]
        else:
            from datetime import datetime, timedelta

            cutoff_dt = (
                datetime.now(UTC)
                - timedelta(days=int(args.older_than_days))
            )
            # Compare on the parsed datetime so we use the same
            # format as the DB's stored ``started_at`` and avoid
            # the dry-run-vs-real-gc off-by-microsecond bug
            # (subagent M2).
            targets = [
                s for s in sessions
                if datetime.fromisoformat(s.started_at.replace("Z", "+00:00"))
                < cutoff_dt
            ]
        if args.json:
            print(json.dumps(
                {"ok": True, "would_delete": len(targets),
                 "session_ids": [s.id for s in targets]},
                sort_keys=True,
            ))
        else:
            print(f"Would delete {len(targets)} session(s).")
        return 0
    with session_store_cm(db_path=db) as store:
        deleted = store.gc(
            older_than_days=int(args.older_than_days),
            only_orphans=bool(getattr(args, "only_orphans", False)),
        )
    if args.json:
        print(json.dumps({"ok": True, "deleted": deleted}, sort_keys=True))
    else:
        print(f"Deleted {deleted} session(s).")
    return 0


def _run_model_command(args: argparse.Namespace, app_config: AppConfig) -> int:
    """Dispatch the ``grc-agent model`` subcommands."""
    try:
        if args.model_command == "list":
            return _run_model_list(args, app_config)
        if args.model_command == "swap":
            return _run_model_swap(args, app_config)
    except Exception as exc:
        payload = build_error_payload(
            error_type=ErrorCode.INTERNAL_ERROR,
            message=str(exc),
        )
        _print_model_payload(payload, json_output=getattr(args, "json", False))
        return 1
    return 2


def _run_model_list(args: argparse.Namespace, app_config: AppConfig) -> int:
    """List every model the local runtime can load."""
    backend = getattr(args, "backend", None) or app_config.llama.backend
    if backend != "ollama":
        if args.json:
            print(json.dumps({"ok": False, "error": "Model listing is only supported for the 'ollama' backend."}))
        else:
            print("Model listing is only supported for the 'ollama' backend. For other backends, list models using their own APIs/tools.")
        return 1

    server_url = app_config.llama.server_url
    if app_config.llama.backend != "ollama":
        server_url = "http://localhost:11434"
    from grc_agent.model_manager import discover_ollama_models
    models = discover_ollama_models(server_url)
    if args.json:
        payload = {
            "ok": True,
            "models": models,
            "count": len(models),
        }
        print(json.dumps(payload, sort_keys=True))
        return 0
    if not models:
        print("No Ollama models found.")
        print(f"Hint: make sure Ollama is running at {server_url} and models are pulled.")
        return 0
    print(f"Found {len(models)} Ollama model(s):")
    for m in models:
        print(f"  {m}")
    return 0


def _run_model_swap(args: argparse.Namespace, app_config: AppConfig) -> int:
    """Switch client/backend.

    Supports 'ollama' and 'openrouter'.
    """
    backend = getattr(args, "backend", None) or app_config.llama.backend
    if backend not in ("ollama", "openrouter"):
        payload = build_error_payload(
            error_type=ErrorCode.INTERNAL_ERROR,
            message=f"Unsupported backend client: {backend}",
        )
        _print_model_payload(payload, json_output=args.json)
        return 1

    if backend == "ollama":
        new_model = getattr(args, "model", None) or app_config.llama.model
        if backend != app_config.llama.backend and not getattr(args, "model", None):
            if not new_model:
                new_model = "llama3.2"

        # Update grc_agent.toml
        try:
            from grc_agent.config import resolve_config_path, update_toml_config_file
            config_path = resolve_config_path(None)
            if config_path:
                update_toml_config_file(config_path, {
                    "backend": "ollama",
                    "server_url": "http://localhost:11434",
                    "model": new_model,
                })
        except Exception as exc:
            logger.warning("Failed to persist to grc_agent.toml: %s", exc)

        payload = {
            "ok": True,
            "model_alias": new_model,
            "server_url": "http://localhost:11434",
            "status": "ready",
            "health_evidence": {"ollama_ready": True},
            "prefs_persisted": True,
        }
        _print_model_payload(payload, json_output=args.json)
        return 0

    elif backend == "openrouter":
        import os
        env_model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")
        # Update grc_agent.toml
        try:
            from grc_agent.config import resolve_config_path, update_toml_config_file
            config_path = resolve_config_path(None)
            if config_path:
                update_toml_config_file(config_path, {
                    "backend": "openrouter",
                    "server_url": "https://openrouter.ai/api",
                    "model": env_model,
                })
        except Exception as exc:
            logger.warning("Failed to persist to grc_agent.toml: %s", exc)

        payload = {
            "ok": True,
            "model_alias": env_model,
            "server_url": "https://openrouter.ai/api",
            "status": "ready",
            "health_evidence": {"openrouter_ready": True},
            "prefs_persisted": True,
        }
        _print_model_payload(payload, json_output=args.json)
        return 0


def _print_model_payload(payload: dict[str, Any], *, json_output: bool) -> None:
    """Print a model subcommand payload in the requested shape."""
    if json_output:
        print(json.dumps(payload, sort_keys=True))
        return
    if not payload.get("ok"):
        print(f"Error: {payload.get('message', 'unknown error')}")
        return
    if "models" in payload:
        models = payload["models"]
        print(f"Found {len(models)} model(s):")
        for model in models:
            print(f"  {model}")
        return
    if "model_alias" in payload:
        print(f"Model swapped to {payload['model_alias']}")
        print(f"Server: {payload['server_url']} (status: {payload['status']})")
        return
    print(json.dumps(payload, sort_keys=True, indent=2))


def _run_dogfood_command(args: argparse.Namespace) -> int:
    try:
        if args.dogfood_command == "record":
            payload = record_dogfood_case(
                prompt=args.prompt,
                graph=args.graph,
                source=args.source,
                task_type=args.task_type,
                failure_category=args.failure_category,
                severity=args.severity,
                expected=args.expected,
                actual=args.actual,
                actual_tools=args.actual_tools,
                graph_delta=args.graph_delta,
                validation_state=args.validation_state,
                save_state=args.save_state,
                reproducible=args.reproducible,
                notes=args.notes,
                intake_path=args.intake_path,
            )
            _print_dogfood_payload(payload, json_output=args.json)
            return 0 if payload.get("ok") else 1
        if args.dogfood_command == "report":
            payload = summarize_dogfood_cases(intake_path=args.intake_path)
            _print_dogfood_payload(payload, json_output=args.json)
            return 0 if payload.get("ok") else 1
    except Exception as exc:
        payload = build_error_payload(
            error_type=ErrorCode.INTERNAL_ERROR,
            message=str(exc),
        )
        _print_dogfood_payload(payload, json_output=getattr(args, "json", False))
        return 1
    return 2


def _run_doctor_command(
    *,
    config_path: str | None,
    json_output: bool,
    skip_retrieval: bool,
) -> int:
    """Execute the packaged-app doctor checks."""
    report = run_doctor(
        config_path=config_path,
        check_retrieval=not skip_retrieval,
        check_llama=False,
    )
    print_doctor_report(report, json_output=json_output)
    return 0 if report["ok"] else 1


def _render_init_template(
    *,
    model: str | None,
) -> str:
    """Render the starter `grc_agent.toml` body with the supplied values."""
    defaults = default_app_config()
    lines: list[str] = [
        "# GRC Agent user config",
        "# Generated by `grc-agent init`. See the README install table for the full list of",
        "# supported values. Remove or comment out a key to fall back to the built-in",
        "# default.",
        "",
        "[llama]",
        f'server_url = "{_toml_escape(defaults.llama.server_url)}"',
        f'model = "{_toml_escape(model or defaults.llama.model)}"',
        f'backend = "{_toml_escape(defaults.llama.backend)}"',
        f"max_tokens = {defaults.llama.max_tokens}",
        f"max_tool_rounds = {defaults.llama.max_tool_rounds}",
        f"temperature = {defaults.llama.temperature}",
        f"enable_thinking = {str(defaults.llama.enable_thinking).lower()}",
        f"request_timeout_seconds = {defaults.llama.request_timeout_seconds}",
    ]
    return "\n".join(lines) + "\n"


def _toml_escape(value: str) -> str:
    """Escape a string for inclusion inside a TOML double-quoted basic string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _prompt_init_value(label: str, *, default: str, current: str | None) -> str:
    """Prompt the user for one init value when a TTY is attached."""
    if current is not None:
        return current
    prompt_default = current if current is not None else default
    try:
        response = input(f"{label} [{prompt_default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not response:
        return default
    return response


def _run_init_command(args: argparse.Namespace) -> int:
    """Write a starter config to the user's config directory."""
    from grc_agent.config import user_config_path

    target = (
        Path(args.config_path).expanduser()
        if args.config_path
        else user_config_path()
    )

    if args.print_target:
        print(str(target))
        return 0

    existing = target.is_file()
    if existing and not args.force:
        message = (
            f"Refusing to overwrite existing config at {target}. "
            "Pass --force to overwrite, or --config-path to write elsewhere."
        )
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error_type": ErrorCode.INIT_FAILED,
                        "message": message,
                        "target": str(target),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(message)
        return 1

    interactive = (
        sys.stdin.isatty()
        and sys.stdout.isatty()
        and args.model is None
    )

    defaults = default_app_config()
    if interactive:
        print(f"Writing starter config to {target}.")
        print("Press Enter to accept the default shown in brackets.")
        model = _prompt_init_value("Model name", default=defaults.llama.model, current=args.model)
    else:
        model = args.model

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        body = _render_init_template(
            model=model,
        )
        target.write_text(body, encoding="utf-8")
    except OSError as exc:
        message = f"Failed to write {target}: {exc}"
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error_type": ErrorCode.INIT_FAILED,
                        "message": message,
                        "target": str(target),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(message)
        return 1

    payload = {
        "ok": True,
        "target": str(target),
        "wrote": not existing,
        "model": model or defaults.llama.model,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        action = "Updated" if existing else "Wrote"
        print(f"{action} {target}.")
        print("Next: `uv run grc-agent doctor` to verify the environment.")
    return 0


def _run_paths_command(args: argparse.Namespace) -> int:
    """Print every filesystem location the package uses."""
    paths = collect_package_paths()
    if args.json:
        print(json.dumps(paths, indent=2, sort_keys=True))
        return 0
    width = max(len(key) for key in paths)
    print("GRC Agent filesystem locations:")
    for key, value in paths.items():
        print(f"  {key.ljust(width)}  {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    translated_argv = _maybe_translate_legacy_args(raw_argv)
    if any(arg in {"-h", "--help"} for arg in translated_argv):
        parser = _build_parser()
        parser.parse_args(translated_argv)
    config_override = _parse_config_override(translated_argv)
    try:
        app_config = load_app_config(config_override)
    except ConfigError as exc:
        _print_cli_error(
            build_error_payload(
                error_type=ErrorCode.INVALID_REQUEST,
                message=str(exc),
            )
        )
        return 1
    # Overlay user preferences (e.g. the model the GUI last picked)
    # onto the config. Preferences win over ``grc_agent.toml`` for
    # the model field; everything else is preserved.
    try:
        from grc_agent.config import (
            apply_user_preferences_to_llama_config,
            load_user_preferences,
        )

        prefs = load_user_preferences()
        if prefs.last_model.alias:
            app_config = AppConfig(
                llama=apply_user_preferences_to_llama_config(
                    app_config.llama, prefs
                ),
                agent=app_config.agent,
            )
    except Exception as exc:  # noqa: BLE001 - defensive, see preferences loader
        logger.debug("Failed to apply user preferences: %s", exc)
    parser = _build_parser(app_config)
    args = parser.parse_args(translated_argv)

    if args.verbose:
        logging.getLogger("grc_agent").setLevel(logging.DEBUG)

    if args.command is None:
        if sys.stdin.isatty() and sys.stdout.isatty():
            return _run_llama_runtime(
                None,
                None,
                app_config,
                app_config.llama.server_url,
                app_config.llama.model,
                None,
                agentic=False,
                max_tool_rounds=None,
                verbose=args.verbose,
            )
        parser.print_help()
        return 2

    if args.command == "doctor":
        return _run_doctor_command(
            config_path=args.config,
            json_output=args.json,
            skip_retrieval=args.skip_retrieval,
        )

    if args.command == "health":
        return _run_health_command(app_config)

    if args.command == "release-manifest":
        return _run_release_manifest_command(app_config)

    if args.command == "debug-bundle":
        return _run_debug_bundle_command(
            config=app_config,
            config_path=args.config,
            output_path=args.output,
        )

    if args.command == "chat":
        if getattr(args, "new_graph", False):
            file_arg = None
        elif args.file is None:
            parser.error("chat requires a .grc file or --new.")
            return 2
        else:
            file_arg = args.file

        message = args.message
        if getattr(args, "stdin", False):
            message = sys.stdin.read().strip()
            if not message:
                parser.error("--stdin was passed but no data was provided on standard input.")
                return 2

        if message is None and not sys.stdin.isatty():
            parser.error("chat requires --message or --stdin when running non-interactively (not attached to a TTY).")
            return 2

        return _run_llama_runtime(
            file_arg,
            message,
            app_config,
            app_config.llama.server_url,
            app_config.llama.model if args.model is None else args.model,
            args.api_key,
            agentic=args.agentic,
            max_tool_rounds=args.max_tool_rounds,
            verbose=args.verbose,
            json_output=getattr(args, "json", False),
        )

    if args.command == "tool":
        try:
            tool_kwargs = _parse_tool_kwargs(args.args)
        except ValueError as exc:
            parser.error(str(exc))
        return _run_tool_command(args.tool_name, tool_kwargs, args.file, app_config)

    if args.command == "dogfood":
        return _run_dogfood_command(args)

    if args.command == "history":
        return _run_history_command(args)
    if args.command == "sessions":
        return _run_sessions_command(args)
    if args.command == "model":
        return _run_model_command(args, app_config)
    if args.command == "init":
        return _run_init_command(args)
    if args.command == "paths":
        return _run_paths_command(args)

    parser.error("Unknown command.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
