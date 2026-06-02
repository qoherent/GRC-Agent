"""Command-line entry point for GRC Agent."""

import argparse
import hashlib
from importlib import metadata
import json
import logging
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

from grc_agent._payload import ErrorCode, build_error_payload
from grc_agent.agent import GrcAgent
from grc_agent.runtime.clarification import render_clarification_prompt
from grc_agent.runtime.tool_schemas import PUBLIC_TOOL_NAMES
from grc_agent.config import AppConfig, ConfigError, load_app_config
from grc_agent.debug_bundle import (
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

from grc_agent.llama_probe import (
    LlamaServerError,
    extract_enabled_builtin_tools,
    extract_model_context_limit,
)
from grc_agent.retrieval import initialize_retrieval
from grc_agent.retrieval.vector import (
    build_vector_index,
    DEFAULT_EMBEDDING_MODEL,
    prune_vector_collections,
    propose_vector_metadata,
    record_vector_miss,
    semantic_search_grc,
    VALID_MISS_CATEGORIES,
    VALID_MISS_SOURCES,
    summarize_vector_misses,
    vector_index_stats,
)
from grc_agent.runtime.tool_surface import MVP_TOOL_SURFACE
from grc_agent.session.load import load_grc as load_grc_session
from grc_agent.toolagents_runtime import (
    ToolAgentsLlamaProviderConfig,
    run_bounded_toolagents_turn,
)

logger = logging.getLogger(__name__)


FAKE_USER_MESSAGE = "Please change the samp_rate to 48000 and validate the graph."
FAKE_ACTIONS = [
    {"text": "I'll do that right away."},
    {
        "tool": "change_graph",
        "kwargs": {
            "update_variables": [
                {
                    "instance_name": "samp_rate",
                    "value": "48000",
                }
            ]
        },
    },
]

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
    doctor_parser.add_argument(
        "--start-llama",
        action="store_true",
        help="Opt into starting/reusing llama.cpp as part of doctor checks.",
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
    debug_bundle_parser.add_argument(
        "--vector-index-dir",
        help="Optional local vector index directory to inspect.",
    )

    fake_parser = subparsers.add_parser(
        "fake",
        help="Run a deterministic fake-model step through the runtime.",
    )
    fake_parser.add_argument("file", help="Path to a .grc file to load.")

    chat_epilog = """
Examples:
  grc-agent chat mygraph.grc --message "Summarize this graph"
  grc-agent chat mygraph.grc --message "Change samp_rate to 48000" --json
  echo "Find an audio sink" | grc-agent chat mygraph.grc --stdin
"""
    chat_parser = subparsers.add_parser(
        "chat",
        help="Run one or more llama.cpp-backed turns against a loaded graph. "
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
        help="Run one bounded llama.cpp turn with this user message. "
        "When omitted, starts an interactive REPL loop.",
    )
    chat_parser.add_argument(
        "--llama-server-url",
        default=llama_config.server_url if llama_config is not None else None,
        help="Base URL for a llama.cpp HTTP server. Defaults to grc_agent.toml.",
    )
    chat_parser.add_argument(
        "--model",
        default=llama_config.model if llama_config is not None else None,
        help="llama.cpp model id. Defaults to the configured value in grc_agent.toml.",
    )
    chat_parser.add_argument(
        "--api-key",
        help="Optional API key for llama.cpp server authentication",
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
  grc-agent tool summarize_graph --file mygraph.grc
  grc-agent tool apply_edit --args '{"add_blocks": [{"block_id": "analog_sig_source_x", "instance_name": "src"}]}'
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

    vector_parser = subparsers.add_parser(
        "vector",
        help="Build and query the local read-only vector retrieval index.",
    )
    vector_subparsers = vector_parser.add_subparsers(dest="vector_command")
    vector_subparsers.required = True
    vector_build_parser = vector_subparsers.add_parser(
        "build",
        help="Build the local Qdrant/FastEmbed vector index.",
    )
    vector_build_parser.add_argument("--index-dir", help="Optional local Qdrant index directory.")
    vector_build_parser.add_argument("--catalog-root", help="Optional GNU Radio catalog root.")
    vector_build_parser.add_argument(
        "--embedding-model",
        default=DEFAULT_EMBEDDING_MODEL,
        help=(
            "FastEmbed model to download/cache locally and use for the index. "
            f"Default: {DEFAULT_EMBEDDING_MODEL}."
        ),
    )
    vector_build_parser.add_argument(
        "--docs-only",
        action="store_true",
        help="Build a non-release docs-only index when GNU Radio catalog metadata is unavailable.",
    )
    vector_build_parser.add_argument(
        "--json",
        action="store_true",
        help="Print build report as JSON.",
    )
    vector_stats_parser = vector_subparsers.add_parser(
        "stats",
        help="Print local vector index stats.",
    )
    vector_stats_parser.add_argument("--index-dir", help="Optional local Qdrant index directory.")
    vector_stats_parser.add_argument(
        "--json",
        action="store_true",
        help="Print stats as JSON.",
    )
    vector_gc_parser = vector_subparsers.add_parser(
        "gc",
        help="Garbage-collect old local vector collections after release evidence is captured.",
    )
    vector_gc_parser.add_argument("--index-dir", help="Optional local Qdrant index directory.")
    vector_gc_parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete stale collections. Without this flag, only print a dry-run report.",
    )
    vector_gc_parser.add_argument(
        "--json",
        action="store_true",
        help="Print garbage-collection report as JSON.",
    )
    vector_search_parser = vector_subparsers.add_parser(
        "search",
        help="Search the local read-only vector index.",
    )
    vector_search_parser.add_argument("query", help="Semantic query text.")
    vector_search_parser.add_argument("--index-dir", help="Optional local Qdrant index directory.")
    vector_search_parser.add_argument(
        "--scope",
        choices=["all", "catalog", "manual", "tutorial"],
        default="all",
        help="Record scope to search.",
    )
    vector_search_parser.add_argument(
        "--k",
        type=int,
        default=5,
        help="Maximum number of results.",
    )
    vector_search_parser.add_argument(
        "--embedding-model",
        default=None,
        help=(
            "FastEmbed model to use for the query. Defaults to the model recorded "
            "in the active vector index manifest."
        ),
    )
    vector_search_parser.add_argument(
        "--json",
        action="store_true",
        help="Print search payload as JSON.",
    )
    vector_miss_parser = vector_subparsers.add_parser(
        "record-miss",
        aliases=["miss"],
        help="Record a sanitized real-user vector retrieval miss as JSONL evidence.",
    )
    vector_miss_parser.add_argument("query", help="User query that missed expected retrieval.")
    vector_miss_parser.add_argument(
        "--expected-block",
        action="append",
        default=[],
        dest="expected_block_ids",
        help="Expected canonical block ID. May be repeated.",
    )
    vector_miss_parser.add_argument(
        "--actual-top-id",
        action="append",
        default=[],
        dest="actual_top_ids",
        help="Actual top vector result ID. May be repeated.",
    )
    vector_miss_parser.add_argument(
        "--observed-top-id",
        action="append",
        dest="observed_top_ids",
        help="Deprecated alias for --actual-top-id.",
    )
    vector_miss_parser.add_argument(
        "--scope",
        choices=["all", "catalog", "manual", "tutorial"],
        default="all",
        help="Search scope where the miss occurred.",
    )
    vector_miss_parser.add_argument(
        "--category",
        choices=sorted(VALID_MISS_CATEGORIES),
        default="untriaged",
        help="Initial triage category.",
    )
    vector_miss_parser.add_argument(
        "--source",
        choices=sorted(VALID_MISS_SOURCES),
        default="real_user",
        help="Evidence source.",
    )
    vector_miss_parser.add_argument(
        "--notes",
        default="",
        help="Short human note. Do not include graph mutation recipes.",
    )
    vector_miss_parser.add_argument(
        "--intake-path",
        help="Optional JSONL path. Defaults to reports/retrieval/real_user_misses.jsonl.",
    )
    vector_miss_parser.add_argument(
        "--json",
        action="store_true",
        help="Print recorded miss payload as JSON.",
    )
    vector_misses_parser = vector_subparsers.add_parser(
        "list-misses",
        aliases=["misses"],
        help="Summarize and deduplicate recorded vector retrieval misses.",
    )
    vector_misses_parser.add_argument(
        "--intake-path",
        help="Optional JSONL path. Defaults to reports/retrieval/real_user_misses.jsonl.",
    )
    vector_misses_parser.add_argument(
        "--json",
        action="store_true",
        help="Print miss summary as JSON.",
    )
    vector_proposals_parser = vector_subparsers.add_parser(
        "list-proposals",
        aliases=["proposals"],
        help="Generate a human-review metadata proposal report from miss clusters.",
    )
    vector_proposals_parser.add_argument(
        "--intake-path",
        help="Optional JSONL path. Defaults to reports/retrieval/real_user_misses.jsonl.",
    )
    vector_proposals_parser.add_argument(
        "--json",
        action="store_true",
        help="Print proposal report as JSON.",
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
        "fake",
        "chat",
        "tool",
        "vector",
        "dogfood",
        "history",
    }
    if not argv or any(arg in command_names for arg in argv):
        return argv

    if "--fake" in argv:
        translated = [arg for arg in argv if arg != "--fake"]
        return ["fake", *translated]

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


def _print_history(agent: GrcAgent, *, verbose: bool = False) -> None:
    """Render runtime history in a compact CLI-friendly form."""
    if verbose:
        print("\n--- History ---")
        for turn in agent.history:
            if turn.get("role") == "session" and isinstance(turn.get("content"), dict):
                printable_turn = dict(turn)
                printable_turn["content"] = json.dumps(turn["content"], sort_keys=True)
                print(printable_turn)
                continue
            if turn.get("role") == "assistant" and turn.get("tool_calls"):
                for tc in turn["tool_calls"]:
                    fn = tc.get("function") or {}
                    name = tc.get("name") or fn.get("name") or "?"
                    args = tc.get("arguments") if isinstance(tc.get("arguments"), dict) else fn.get("arguments", {})
                    print(f"Assistant called {name}: {json.dumps(args)}")
                continue
            if turn.get("role") == "tool" and isinstance(turn.get("content"), dict):
                printable_turn = dict(turn)
                printable_turn["content"] = json.dumps(turn["content"], sort_keys=True)
                print(printable_turn)
                continue
            print(turn)
        return
    print("\n--- History ---")
    for turn in agent.history:
        if turn.get("role") == "session":
            continue
        if turn.get("role") == "assistant" and turn.get("tool_calls"):
            for tc in turn["tool_calls"]:
                fn = tc.get("function") or {}
                name = tc.get("name") or fn.get("name") or "?"
                print(f"  Tool call: {name}")
            continue
        if turn.get("role") == "tool" and isinstance(turn.get("content"), dict):
            content = turn["content"]
            ok = content.get("ok")
            name = content.get("tool") or turn.get("name") or "?"
            status = "ok" if ok else "FAILED"
            msg = content.get("message", "")
            line = f"  {name}: {status}"
            if not ok and msg:
                line += f" — {msg[:80]}"
            print(line)
            continue
        role = turn.get("role", "")
        text = turn.get("content", "")
        if role == "user" and isinstance(text, str):
            print(f"  User: {text[:100]}")
        elif role == "assistant" and isinstance(text, str) and text:
            print(f"  Assistant: {text[:120]}")


def _print_turn_operations(agent: GrcAgent, *, start_index: int) -> None:
    """Render concise operation details for the just-completed turn."""
    lines: list[str] = []
    requested: list[str] = []
    for turn in agent.history[start_index:]:
        if turn.get("role") == "assistant" and turn.get("tool_calls"):
            for tool_call in turn["tool_calls"]:
                fn = tool_call.get("function") or {}
                name = tool_call.get("name") or fn.get("name") or "?"
                args = tool_call.get("arguments")
                if not isinstance(args, dict):
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                detail = _tool_detail_from_args(args if isinstance(args, dict) else {})
                requested.append(f"{name}{detail}")
        if turn.get("role") != "tool" or not isinstance(turn.get("content"), dict):
            continue
        content = turn["content"]
        name = content.get("tool") or turn.get("name") or "?"
        ok = content.get("ok")
        status = "ok" if ok is True else "FAILED" if ok is False else "unknown"
        detail = _tool_detail_from_args(content)
        validation = _validation_status(content)
        dirty = _dirty_status(content)
        line = f"{name}{detail}: {status}"
        extras = [item for item in (validation, dirty) if item]
        if extras:
            line += f" ({', '.join(extras)})"
        lines.append(line)
    if not lines and not requested:
        return
    print("\nOperations:")
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
    print("\n--- Active Session ---")
    if active_session is None:
        print("No active flowgraph session.")
        return
    validation = active_session["validation"]["status"]
    path = active_session.get("path") or "(new graph)"
    if verbose:
        print(
            f"{path} "
            f"(graph_id={active_session['graph_id']}, "
            f"state_revision={active_session['state_revision']}, "
            f"dirty={active_session['dirty']}, validation={validation})"
        )
    else:
        print(
            f"{path} "
            f"(graph_id={active_session['graph_id']}, "
            f"dirty={active_session['dirty']}, validation={validation})"
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
            raise CliError(
                {
                    "ok": False,
                    "message": "Refusing to load graph because validation failed.",
                    "error_type": (
                        ErrorCode.VALIDATION_TIMEOUT
                        if loaded.last_validation_returncode == -2
                        else ErrorCode.GNU_VALIDATION_FAILED
                    ),
                    "validation": loaded.validation_state(),
                }
            )
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
    print("\n--- Error ---")
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
    if hint:
        print(hint)


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
        "Hint: copy the graph first, then run chat/fake on the copied path. "
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


def _run_fake_runtime(file_path: str, config: AppConfig) -> int:
    """Exercise the routed runtime contract with deterministic fake actions."""
    if _reject_if_original_graph_path(file_path):
        return 1
    print(f"Loading {file_path}...")
    try:
        session = _load_initial_session(file_path)
    except CliError as exc:
        _print_cli_error(exc.payload)
        return 1
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
    _print_active_session(agent, verbose=True)

    print("--- System Prompt ---")
    print(agent.get_system_prompt())
    print("---------------------\n")

    agent.run_step_fake(FAKE_USER_MESSAGE, FAKE_ACTIONS)

    _print_history(agent, verbose=True)

    return 0


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
    """Run one or more bounded llama.cpp-backed turns against the routed runtime."""
    original_stdout = sys.stdout
    if json_output:
        sys.stdout = sys.stderr
    effective_max_tool_rounds = _effective_max_tool_rounds(
        config,
        agentic=agentic,
        requested=max_tool_rounds,
    )
    effective_request_timeout = _effective_request_timeout(config, agentic=agentic)
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
        start_llama=True,
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
        print(result.errors[-1] if result.errors else "Failed to ensure llama.cpp server.")
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
            f"Started llama.cpp server for {result.model_alias} "
            f"at {result.server_url} (health verified)"
        )
    else:
        logger.info("server_reused url=%s", result.server_url)
        print(
            f"Reusing llama.cpp server for {result.model_alias} "
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
    """Run one bounded llama turn and print the result."""
    if config is None:
        config = load_app_config()
    round_limit = (
        config.llama.max_tool_rounds
        if max_tool_rounds is None
        else max_tool_rounds
    )
    try:
        history_start = len(agent.history)
        result = run_bounded_toolagents_turn(
            agent,
            provider_config,
            user_message,
            model=model,
            mvp_tool_profile=True,
            max_tool_rounds=round_limit,
        )
    except LlamaServerError as exc:
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

        operations = []
        for turn in agent.history[history_start:]:
            if turn.get("role") == "assistant" and turn.get("tool_calls"):
                for tc in turn["tool_calls"]:
                    fn = tc.get("function") or {}
                    name = tc.get("name") or fn.get("name") or "?"
                    args = tc.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    if not isinstance(args, dict):
                        args = fn.get("arguments", {})
                    operations.append({"name": name, "arguments": args})

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
    print("\nInteractive chat. Type /save [path], /history, /quit, or /exit.\n")
    last_exit_code = 0

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in ("/quit", "/exit"):
            break
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
                print(f"\n--- Executed {'OK' if ok else 'FAILED'} ---")
                if msg:
                    print(msg)
                _maybe_render_pending_clarification(agent)
                continue
            elif mode == "expired":
                print("\n--- Expired ---")
                print(resolved.get("text", "The question is no longer valid."))
                continue
            elif mode == "reminder":
                print("\n--- Reminder ---")
                print(resolved.get("text", ""))
                _maybe_render_pending_clarification(agent)
                continue
            elif mode == "custom":
                # Fall through to normal model flow with the custom text
                user_input = resolved.get("custom_hint") or user_input
            # mode == "none" should not happen when _pending_clarification is not None

        try:
            history_start = len(agent.history)
            result = run_bounded_toolagents_turn(
                agent,
                provider_config,
                user_input,
                model=model,
                mvp_tool_profile=True,
                max_tool_rounds=round_limit,
            )
        except LlamaServerError as exc:
            print(f"\n--- Runtime Error ---\n{exc}")
            last_exit_code = 1
            continue

        if result["ok"]:
            print(f"\nAssistant:\n{result['assistant_text']}")
        else:
            print(f"\n--- Runtime ---\n{result['message']}")
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
    report["llama_desired_context_tokens"] = config.llama.desired_context_tokens
    report["llama_device"] = config.llama.device
    report["llama_gpu_layers"] = config.llama.gpu_layers
    report["llama_max_tokens"] = config.llama.max_tokens
    report["llama_max_tool_rounds"] = config.llama.max_tool_rounds
    report["llama_model_ready"] = False
    report["llama_context_verified"] = False
    try:
        from grc_agent.llama_probe import LlamaHealthProbe

        probe = LlamaHealthProbe(
            base_url=config.llama.server_url,
            timeout_seconds=min(config.llama.request_timeout_seconds, 5.0),
        )
        probe.require_ready()
        probe.require_model_alias(config.llama.model)
        props = probe.get_server_properties()
        actual_context = extract_model_context_limit(props)
        report["llama_actual_context_tokens"] = actual_context
        report["llama_build_info"] = props.get("build_info")
        report["llama_chat_template_caps"] = props.get("chat_template_caps")
        builtin_tools = sorted(extract_enabled_builtin_tools(props))
        report["llama_builtin_tools_enabled"] = builtin_tools
        report["llama_builtin_tools_disabled"] = not builtin_tools
        report["llama_model_ready"] = True
        report["llama_context_verified"] = actual_context is not None
        if builtin_tools:
            status_reasons.append("llama_builtin_tools_enabled")
        if actual_context is None:
            status_reasons.append("llama_context_unknown")
        elif actual_context < config.llama.desired_context_tokens:
            status_reasons.append("llama_context_below_desired")
    except Exception as exc:
        report["llama_actual_context_tokens"] = None
        report["llama_props_error"] = str(exc)
        status_reasons.append("llama_unreachable")

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
            "device": config.llama.device,
            "gpu_layers": config.llama.gpu_layers,
            "desired_context_tokens": config.llama.desired_context_tokens,
            "actual_context_tokens": health_report.get("llama_actual_context_tokens"),
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
            "vector_regression": "uv run python -m tests.retrieval_eval.vector_regression",
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
    vector_index_dir: str | None,
) -> int:
    """Write a redacted debug bundle and print a compact summary."""

    doctor_report = run_doctor(
        config_path=config_path,
        check_retrieval=True,
        check_llama=False,
    )
    health_report = _build_health_report(config)
    release_manifest = _build_release_manifest(config)
    try:
        vector_stats = vector_index_stats(index_dir=vector_index_dir)
    except Exception as exc:
        vector_stats = build_error_payload(
            error_type=ErrorCode.INTERNAL_ERROR,
            message=str(exc),
        )
    repo_root = Path(__file__).resolve().parents[2]
    payload = build_debug_bundle(
        config=config,
        config_path=config_path,
        doctor_report=doctor_report,
        health_report=health_report,
        release_manifest=release_manifest,
        vector_stats=vector_stats,
        repo_root=repo_root,
    )
    written = write_debug_bundle(output_path, payload)
    print(json.dumps(debug_bundle_summary(payload, written), indent=2, sort_keys=True))
    return 0


def _print_vector_payload(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if not payload.get("ok"):
        print(payload.get("message", "Vector command failed."))
        if str(payload.get("error_type")) == "missing_index":
            print("Hint: build the local index first with `uv run grc-agent vector build`.")
        return
    if "would_delete_collections" in payload:
        mode = "dry run" if payload.get("dry_run") else "applied"
        print(f"Vector GC {mode}: {len(payload.get('would_delete_collections', []))} old collections")
        if payload.get("retention_policy"):
            print(f"Retention: {payload.get('retention_policy')}")
        if payload.get("active_collection"):
            print(f"Active: {payload.get('active_collection')}")
        if payload.get("previous_collection"):
            print(f"Previous: {payload.get('previous_collection')}")
        for name in payload.get("would_delete_collections", []):
            prefix = "Would delete" if payload.get("dry_run") else "Deleted"
            print(f"{prefix}: {name}")
        return
    if payload.get("tool") == "record_vector_miss":
        record = payload.get("record", {})
        print(f"Recorded vector miss: {record.get('query', '')}")
        print(f"Path: {payload.get('intake_path', '')}")
        if record.get("category"):
            print(f"Category: {record.get('category')}")
        return
    if payload.get("tool") == "summarize_vector_misses":
        print(
            f"Vector misses: {payload.get('total_records', 0)} records, "
            f"{payload.get('cluster_count', 0)} clusters"
        )
        for cluster in payload.get("clusters", [])[:10]:
            queries = ", ".join(cluster.get("queries", [])[:3])
            expected = ", ".join(cluster.get("expected_block_ids", [])) or "unknown"
            print(
                f"- {cluster.get('count')}x {expected}: {queries} "
                f"[{cluster.get('recommended_action')}]"
            )
        for warning in payload.get("warnings", []) or []:
            print(f"Warning: {warning}")
        return
    if payload.get("tool") == "propose_vector_metadata":
        print(f"Metadata proposal candidates: {payload.get('candidate_count', 0)}")
        for candidate in payload.get("candidates", [])[:10]:
            print(
                f"- {candidate.get('proposed_block')}: "
                f"{candidate.get('proposed_stable_capability_phrase')}"
            )
        blocked = payload.get("blocked_clusters", [])
        if blocked:
            print(f"Blocked clusters: {len(blocked)}")
        for warning in payload.get("warnings", []) or []:
            print(f"Warning: {warning}")
        return
    if "results" in payload:
        print(f"Vector search: {payload.get('query', '')}")
        for index, result in enumerate(payload.get("results", []), start=1):
            print(
                f"\n{index}. {result.get('title')} "
                f"({result.get('source_type')}, score={result.get('vector_score_raw')})"
            )
            if result.get("canonical_block_id"):
                print(f"Block: {result.get('canonical_block_id')}")
            print(result.get("excerpt", ""))
            provenance = result.get("provenance", {})
            if isinstance(provenance, dict):
                print(f"Source: {provenance.get('path')}")
        for warning in payload.get("warnings", []) or []:
            print(f"Warning: {warning}")
        return
    print(
        f"Vector index {payload.get('collection_alias', '')}: "
        f"{payload.get('record_count', payload.get('points_count', 0))} records"
    )
    records_by_source_type = payload.get("records_by_source_type")
    if isinstance(records_by_source_type, dict):
        for source_type, count in sorted(records_by_source_type.items()):
            print(f"  {source_type}: {count}")


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


def _run_vector_command(args: argparse.Namespace) -> int:
    try:
        if args.vector_command == "build":
            payload = build_vector_index(
                index_dir=args.index_dir,
                catalog_root=args.catalog_root,
                docs_only=args.docs_only,
                embedding_model=args.embedding_model,
            )
            _print_vector_payload(payload, json_output=args.json)
            return 0
        if args.vector_command == "stats":
            payload = vector_index_stats(index_dir=args.index_dir)
            _print_vector_payload(payload, json_output=args.json)
            return 0 if payload.get("ok") else 1
        if args.vector_command == "gc":
            payload = prune_vector_collections(
                index_dir=args.index_dir,
                dry_run=not args.apply,
            )
            _print_vector_payload(payload, json_output=args.json)
            return 0 if payload.get("ok") else 1
        if args.vector_command == "search":
            payload = semantic_search_grc(
                args.query,
                scope=args.scope,
                k=args.k,
                index_dir=args.index_dir,
                embedding_model=args.embedding_model,
            )
            _print_vector_payload(payload, json_output=args.json)
            return 0 if payload.get("ok") else 1
        if args.vector_command == "miss":
            actual_top_ids = args.actual_top_ids or args.observed_top_ids or []
            payload = record_vector_miss(
                args.query,
                expected_block_ids=args.expected_block_ids,
                actual_top_ids=actual_top_ids,
                scope=args.scope,
                category=args.category,
                source=args.source,
                notes=args.notes,
                intake_path=args.intake_path,
            )
            _print_vector_payload(payload, json_output=args.json)
            return 0 if payload.get("ok") else 1
        if args.vector_command == "misses":
            payload = summarize_vector_misses(intake_path=args.intake_path)
            _print_vector_payload(payload, json_output=args.json)
            return 0 if payload.get("ok") else 1
        if args.vector_command == "proposals":
            payload = propose_vector_metadata(intake_path=args.intake_path)
            _print_vector_payload(payload, json_output=args.json)
            return 0 if payload.get("ok") else 1
    except Exception as exc:
        payload = build_error_payload(
            error_type=ErrorCode.INTERNAL_ERROR,
            message=str(exc),
        )
        _print_vector_payload(payload, json_output=getattr(args, "json", False))
        return 1
    return 2


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
    check_llama: bool,
) -> int:
    """Execute the packaged-app doctor checks."""
    report = run_doctor(
        config_path=config_path,
        check_retrieval=not skip_retrieval,
        check_llama=check_llama,
    )
    print_doctor_report(report, json_output=json_output)
    return 0 if report["ok"] else 1


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
            check_llama=args.start_llama,
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
            vector_index_dir=args.vector_index_dir,
        )

    if args.command == "fake":
        return _run_fake_runtime(args.file, app_config)

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
            app_config.llama.server_url
            if args.llama_server_url is None
            else args.llama_server_url,
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

    if args.command == "vector":
        return _run_vector_command(args)

    if args.command == "dogfood":
        return _run_dogfood_command(args)

    if args.command == "history":
        return _run_history_command(args)

    parser.error("Unknown command.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
