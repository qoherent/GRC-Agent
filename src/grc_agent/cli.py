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
from grc_agent.model_manager import (
    cached_model_to_dict,
    discover_cached_models,
    list_system_specs,
    system_specs_to_dict,
)
from grc_agent.retrieval import initialize_retrieval
from grc_agent.retrieval.vector import (
    DEFAULT_EMBEDDING_MODEL,
    VALID_MISS_CATEGORIES,
    VALID_MISS_SOURCES,
    build_vector_index,
    propose_vector_metadata,
    prune_vector_collections,
    record_vector_miss,
    semantic_search_grc,
    summarize_vector_misses,
    vector_index_stats,
)
from grc_agent.runtime.clarification import render_clarification_prompt
from grc_agent.runtime.tool_schemas import PUBLIC_TOOL_NAMES
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
  uv run grc-agent chat mygraph.grc --message "Summarize this graph"
  uv run grc-agent chat mygraph.grc --message "Change samp_rate to 48000" --json
  echo "Find an audio sink" | uv run grc-agent chat mygraph.grc --stdin
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

    model_parser = subparsers.add_parser(
        "model",
        help="Discover, inspect, and swap the local llama.cpp model.",
    )
    model_subparsers = model_parser.add_subparsers(dest="model_command")
    model_subparsers.required = True
    model_list_parser = model_subparsers.add_parser(
        "list",
        help="List every .gguf file in the local Hugging Face cache (and any configured models_dir).",
    )
    model_list_parser.add_argument(
        "--hf-cache",
        help="Override the HF cache root. Defaults to ~/.cache/huggingface/hub/.",
    )
    model_list_parser.add_argument(
        "--models-dir",
        help="Override the local models directory. Defaults to [llama].models_dir from grc_agent.toml.",
    )
    model_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Print discovered models as JSON.",
    )
    model_specs_parser = model_subparsers.add_parser(
        "specs",
        help="Print local machine VRAM/GPU/RAM/CPU specs.",
    )
    model_specs_parser.add_argument(
        "--json",
        action="store_true",
        help="Print specs as JSON.",
    )
    model_swap_parser = model_subparsers.add_parser(
        "swap",
        help="Restart llama.cpp with a different model file.",
    )
    model_swap_parser.add_argument(
        "--hf-repo",
        required=True,
        help="Hugging Face repo, e.g. 'unsloth/Qwen3.5-2B-GGUF'.",
    )
    model_swap_parser.add_argument(
        "--filename",
        required=True,
        help="GGUF filename inside the repo, e.g. 'Qwen3.5-2B-UD-Q4_K_XL.gguf'.",
    )
    model_swap_parser.add_argument(
        "--alias",
        help=(
            "llama.cpp --alias override. Defaults to the resolved GGUF basename. "
            "Pass an explicit alias when swapping between two files that share "
            "the same filename across different repos."
        ),
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
  uv run grc-agent init --model-path ~/models/qwen.gguf --device CUDA0 --force

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
        help="llama.cpp model alias (e.g. my-model.gguf). Defaults to the built-in default.",
    )
    init_parser.add_argument(
        "--hf-model",
        help="Hugging Face repo id to auto-download from (e.g. unsloth/Qwen3.5-2B-GGUF:Q4_K_XL).",
    )
    init_parser.add_argument(
        "--model-path",
        help="Absolute path to a local GGUF model file. Leave empty to rely on --hf-model.",
    )
    init_parser.add_argument(
        "--server-url",
        help="llama.cpp HTTP base URL (default: http://127.0.0.1:8080).",
    )
    init_parser.add_argument(
        "--device",
        help="Accelerator device (CUDA0, Metal, Vulkan0, CPU). Default: CPU (auto-detect).",
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
    init_parser.add_argument(
        "--log-retention-days",
        type=int,
        default=None,
        help="Launcher log retention in days. 0 keeps logs forever. Default: 7.",
    )

    paths_parser = subparsers.add_parser(
        "paths",
        help="Print every filesystem location the package uses (config, history, vector index, caches).",
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
        "fake",
        "chat",
        "tool",
        "vector",
        "dogfood",
        "history",
        "init",
        "paths",
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


class Colors:
    HEADER = "\033[95m"
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


def _print_history(agent: GrcAgent, *, verbose: bool = False) -> None:
    """Render runtime history in a compact CLI-friendly form."""
    if verbose:
        print(_colorize(Colors.BOLD + Colors.YELLOW, "\n--- History ---"))
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
                    print(f"{_colorize(Colors.BOLD + Colors.GREEN, 'Assistant')} called {_colorize(Colors.CYAN, name)}: {json.dumps(args)}")
                continue
            if turn.get("role") == "tool" and isinstance(turn.get("content"), dict):
                printable_turn = dict(turn)
                printable_turn["content"] = json.dumps(turn["content"], sort_keys=True)
                print(printable_turn)
                continue
            print(turn)
        return
    print(_colorize(Colors.BOLD + Colors.YELLOW, "\n--- History ---"))
    for turn in agent.history:
        if turn.get("role") == "session":
            continue
        if turn.get("role") == "assistant" and turn.get("tool_calls"):
            for tc in turn["tool_calls"]:
                fn = tc.get("function") or {}
                name = tc.get("name") or fn.get("name") or "?"
                print(f"  {_colorize(Colors.BLUE, 'Tool call:')} {_colorize(Colors.BOLD, name)}")
            continue
        if turn.get("role") == "tool" and isinstance(turn.get("content"), dict):
            content = turn["content"]
            ok = content.get("ok")
            name = content.get("tool") or turn.get("name") or "?"
            status = _colorize(Colors.GREEN, "ok") if ok else _colorize(Colors.RED, "FAILED")
            msg = content.get("message", "")
            line = f"  {_colorize(Colors.BOLD, name)}: {status}"
            if not ok and msg:
                line += f" — {_colorize(Colors.YELLOW, msg[:80])}"
            print(line)
            continue
        role = turn.get("role", "")
        text = turn.get("content", "")
        if role == "user" and isinstance(text, str):
            print(f"  {_colorize(Colors.BOLD + Colors.CYAN, 'User:')} {text[:100]}")
        elif role == "assistant" and isinstance(text, str) and text:
            print(f"  {_colorize(Colors.BOLD + Colors.GREEN, 'Assistant:')} {text[:120]}")


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
                requested.append(f"{_colorize(Colors.BOLD + Colors.CYAN, name)}{detail}")
        if turn.get("role") != "tool" or not isinstance(turn.get("content"), dict):
            continue
        content = turn["content"]
        name = content.get("tool") or turn.get("name") or "?"
        ok = content.get("ok")
        status = _colorize(Colors.GREEN, "ok") if ok is True else _colorize(Colors.RED, "FAILED") if ok is False else "unknown"
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
            "metadata is discoverable. Run `uv run grc-agent vector build` to build "
            "the local index."
        )
    elif error_type == ErrorCode.LLAMA_SERVER_MISSING:
        hint = (
            "Hint: install llama.cpp and ensure `llama-server` is on PATH. "
            "See the README install table. The CLI will auto-start a local "
            "server once `llama-server` is on PATH; to use a remote server, "
            "point `[llama].server_url` at it and set `start_llama=False` "
            "(advanced)."
        )
    elif error_type == ErrorCode.GRCC_MISSING:
        hint = (
            "Hint: install GNU Radio 3.10.x via your package manager and "
            "ensure `grcc` is on PATH. See the README install table."
        )
    elif error_type == ErrorCode.MODEL_NOT_FOUND:
        hint = (
            "Hint: set `[llama].model_path` in your config to a local GGUF "
            "file, or set `[llama].hf_model` to a Hugging Face repo for "
            "auto-download. Use `uv run grc-agent init` to write a starter config."
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
        if args.model_command == "specs":
            return _run_model_specs(args)
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
    """List every .gguf the local runtime can load."""
    hf_cache = Path(args.hf_cache).expanduser() if args.hf_cache else None
    models_dir: Path | None
    if args.models_dir:
        models_dir = Path(args.models_dir).expanduser()
    else:
        cfg_dir = app_config.llama.models_dir
        models_dir = Path(cfg_dir).expanduser() if cfg_dir else None
    models = discover_cached_models(hf_cache=hf_cache, models_dir=models_dir)
    if args.json:
        payload = {
            "ok": True,
            "models": [cached_model_to_dict(m) for m in models],
            "count": len(models),
        }
        print(json.dumps(payload, sort_keys=True))
        return 0
    if not models:
        print("No .gguf files found.")
        print(
            "Hint: download a model via the configured [llama].hf_model, or set "
            "[llama].models_dir to a directory containing local .gguf files."
        )
        return 0
    print(f"Found {len(models)} model file(s):")
    for model in models:
        size_mib = model.size_bytes / (1024 * 1024)
        used = (
            model.last_used.strftime("%Y-%m-%d")
            if model.last_used is not None
            else "unknown"
        )
        print(
            f"  {model.hf_repo}:{model.filename}  "
            f"({size_mib:.1f} MiB, last used {used})"
        )
    return 0


def _run_model_specs(args: argparse.Namespace) -> int:
    """Print local machine VRAM/GPU/RAM/CPU specs."""
    specs = list_system_specs()
    if args.json:
        payload = {"ok": True, "specs": system_specs_to_dict(specs)}
        print(json.dumps(payload, sort_keys=True))
        return 0
    print("Local machine specs:")
    print(f"  GPU : {specs.gpu_name or 'unknown'}")
    if specs.gpu_vram_bytes is not None:
        print(f"  VRAM: {specs.gpu_vram_bytes / (1024 ** 3):.2f} GiB")
    else:
        print("  VRAM: unknown")
    if specs.ram_bytes is not None:
        print(f"  RAM : {specs.ram_bytes / (1024 ** 3):.2f} GiB")
    else:
        print("  RAM : unknown")
    print(f"  CPU : {specs.cpu_name or 'unknown'}")
    if specs.cpu_cores_logical is not None:
        print(f"  Cores: {specs.cpu_cores_logical}")
    else:
        print("  Cores: unknown")
    return 0


def _run_model_swap(args: argparse.Namespace, app_config: AppConfig) -> int:
    """Restart the local llama.cpp server with a different model.

    Phase 3 of the model-selector rollout. Delegates to
    :meth:`grc_agent.llama_launcher.LlamaServerLauncher.swap_model`,
    which builds a new :class:`LlamaConfig`, starts a fresh
    ``llama-server`` process, waits for readiness, and returns the new
    provider config. Errors surface as rc=1 with a human-readable
    payload.
    """
    from grc_agent.llama_launcher import LlamaLauncherError, LlamaServerLauncher

    try:
        launcher = LlamaServerLauncher(app_config.llama)
        result = launcher.swap_model(
            new_hf_repo=args.hf_repo,
            new_filename=args.filename,
            new_alias=args.alias,
        )
    except LlamaLauncherError as exc:
        payload = build_error_payload(
            error_type=ErrorCode.INTERNAL_ERROR,
            message=str(exc),
        )
        _print_model_payload(payload, json_output=args.json)
        return 1
    payload = {
        "ok": True,
        "model_alias": result.model_alias,
        "server_url": result.server_url,
        "status": result.status,
        "health_evidence": result.health_evidence,
    }
    # Persist the selection so the next session starts with the
    # same model. Failure is non-fatal; the swap itself succeeded.
    try:
        from grc_agent.preferences import update_last_model

        update_last_model(
            hf_repo=args.hf_repo,
            filename=args.filename,
            alias=result.model_alias,
        )
    except OSError as exc:
        logger.warning("Failed to persist last_model preference: %s", exc)
        payload["prefs_persisted"] = False
    else:
        payload.setdefault("prefs_persisted", True)
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
        print(f"Found {len(models)} model file(s):")
        for model in models:
            size_mib = int(model["size_bytes"]) / (1024 * 1024)
            used = model["last_used"] or "unknown"
            if used != "unknown":
                used = used.split("T")[0]
            print(
                f"  {model['hf_repo']}:{model['filename']}  "
                f"({size_mib:.1f} MiB, last used {used})"
            )
        return
    if "specs" in payload:
        specs = payload["specs"]
        print("Local machine specs:")
        print(f"  GPU : {specs['gpu_name'] or 'unknown'}")
        if specs["gpu_vram_bytes"] is not None:
            print(f"  VRAM: {specs['gpu_vram_bytes'] / (1024 ** 3):.2f} GiB")
        else:
            print("  VRAM: unknown")
        if specs["ram_bytes"] is not None:
            print(f"  RAM : {specs['ram_bytes'] / (1024 ** 3):.2f} GiB")
        else:
            print("  RAM : unknown")
        print(f"  CPU : {specs['cpu_name'] or 'unknown'}")
        cores = specs["cpu_cores_logical"]
        print(f"  Cores: {cores if cores is not None else 'unknown'}")
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


def _render_init_template(
    *,
    model: str | None,
    hf_model: str | None,
    model_path: str | None,
    server_url: str | None,
    device: str | None,
    log_retention_days: int | None,
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
        f'server_url = "{_toml_escape(server_url or defaults.llama.server_url)}"',
        f'model = "{_toml_escape(model or defaults.llama.model)}"',
        f'hf_model = "{_toml_escape(hf_model or defaults.llama.hf_model)}"',
    ]
    resolved_model_path = model_path if model_path is not None else defaults.llama.model_path
    if resolved_model_path:
        lines.append(f'model_path = "{_toml_escape(resolved_model_path)}"')
    else:
        lines.append(
            '# Set model_path to a local GGUF file path, or leave empty to rely on hf_model.'
        )
        lines.append("model_path = \"\"")
    lines.append(
        f'device = "{_toml_escape(device or defaults.llama.device)}"'
    )
    lines.append("gpu_layers = 999")
    lines.append("desired_context_tokens = 120000")
    lines.append("startup_timeout_seconds = 300.0")
    lines.append("max_tokens = 4096")
    lines.append("max_tool_rounds = 8")
    lines.append("temperature = 0.0")
    lines.append("enable_thinking = false")
    lines.append("request_timeout_seconds = 120.0")
    resolved_retention = (
        log_retention_days
        if log_retention_days is not None
        else defaults.llama.log_retention_days
    )
    lines.append("# Retention in days for the launcher log files.")
    lines.append("# 0 keeps them forever; the default prunes anything older.")
    lines.append(f"log_retention_days = {int(resolved_retention)}")
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
        and not any(
            value is not None
            for value in (
                args.model,
                args.hf_model,
                args.model_path,
                args.server_url,
                args.device,
                getattr(args, "log_retention_days", None),
            )
        )
    )

    defaults = default_app_config()
    if interactive:
        print(f"Writing starter config to {target}.")
        print("Press Enter to accept the default shown in brackets.")
        model = _prompt_init_value("Model alias", default=defaults.llama.model, current=args.model)
        hf_model = _prompt_init_value(
            "Hugging Face repo (auto-download source)", default=defaults.llama.hf_model, current=args.hf_model
        )
        model_path = _prompt_init_value(
            "Local GGUF path (empty to use hf_model)",
            default=defaults.llama.model_path or "",
            current=args.model_path,
        )
        server_url = _prompt_init_value(
            "llama.cpp server URL", default=defaults.llama.server_url, current=args.server_url
        )
        device = _prompt_init_value(
            "Device (CPU/CUDA0/Metal/Vulkan0)", default=defaults.llama.device, current=args.device
        )
        log_retention_days_str = _prompt_init_value(
            "Log retention in days (0 = keep forever)",
            default=str(defaults.llama.log_retention_days),
            current=(
                str(args.log_retention_days)
                if getattr(args, "log_retention_days", None) is not None
                else None
            ),
        )
        try:
            log_retention_days = int(log_retention_days_str)
        except ValueError:
            log_retention_days = defaults.llama.log_retention_days
    else:
        model = args.model
        hf_model = args.hf_model
        model_path = args.model_path
        server_url = args.server_url
        device = args.device
        log_retention_days = getattr(args, "log_retention_days", None)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        body = _render_init_template(
            model=model,
            hf_model=hf_model,
            model_path=model_path,
            server_url=server_url,
            device=device,
            log_retention_days=log_retention_days,
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
        "hf_model": hf_model or defaults.llama.hf_model,
        "model_path": model_path or "",
        "server_url": server_url or defaults.llama.server_url,
        "device": device or defaults.llama.device,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        action = "Updated" if existing else "Wrote"
        print(f"{action} {target}.")
        print("Next: `uv run grc-agent doctor` to verify the environment.")
    return 0


def _collect_package_paths() -> dict[str, str]:
    """Return a stable mapping of every on-disk location the package uses."""
    from grc_agent.config import default_config_path, user_config_path
    from grc_agent.history import HISTORY_ENV_VAR, default_history_path
    from grc_agent.llama_launcher import (
        DEFAULT_LLAMA_LOG_DIR,
        DEFAULT_LLAMA_STATE_PATH,
    )
    from grc_agent.preferences import user_preferences_path

    cache_root = Path.home() / ".cache"
    # `default_history_path()` returns a cwd-relative path when no env
    # override is set; resolve it to an absolute path so the output is
    # unambiguous regardless of the current working directory.
    history_path = default_history_path()
    if not history_path.is_absolute():
        history_path = (Path.cwd() / history_path).resolve()
    paths: dict[str, str] = {
        "config_repo": str(default_config_path()),
        "config_user": str(user_config_path()),
        "preferences": str(user_preferences_path()),
        "history": str(history_path),
        "history_env_var": HISTORY_ENV_VAR,
        "sessions_db": str(Path.home() / ".grc_agent" / "sessions.db"),
        "vector_index_default": str(Path.home() / ".grc_agent" / "vector_index"),
        "llama_state": str(DEFAULT_LLAMA_STATE_PATH),
        "llama_logs": str(DEFAULT_LLAMA_LOG_DIR),
        "fastembed_cache": str(cache_root / "fastembed"),
        "hf_cache": str(cache_root / "huggingface"),
        "grc_agent_state": str(Path.home() / ".grc_agent"),
        "grc_agent_cache": str(cache_root / "grc_agent"),
    }
    return paths


def _run_paths_command(args: argparse.Namespace) -> int:
    """Print every filesystem location the package uses."""
    paths = _collect_package_paths()
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
    # the model and hf_model fields; everything else is preserved.
    try:
        from grc_agent.preferences import (
            apply_user_preferences_to_llama_config,
            load_user_preferences,
        )

        prefs = load_user_preferences()
        if (
            prefs.last_model.alias
            or prefs.last_model.hf_repo
            or prefs.last_model.filename
        ):
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
