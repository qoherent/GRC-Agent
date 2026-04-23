"""Command-line entry point for GRC Agent."""

import argparse
import json
import logging
import sys
from typing import Any

from grc_agent.agent import GrcAgent, PUBLIC_TOOL_NAMES
from grc_agent.config import AppConfig, load_app_config
from grc_agent.doctor import print_doctor_report, run_doctor
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_launcher import LlamaLauncherError, LlamaServerLauncher
from grc_agent.llama_server import (
    LlamaServerClient,
    LlamaServerError,
    run_bounded_llama_turn,
)
from grc_agent.retrieval import initialize_retrieval

logger = logging.getLogger(__name__)


FAKE_USER_MESSAGE = "Please change the samp_rate to 48000 and validate the graph."
FAKE_ACTIONS = [
    {"text": "I'll do that right away."},
    {
        "tool": "apply_edit",
        "kwargs": {
            "transaction": {
                "op_type": "update_params",
                "instance_name": "samp_rate",
                "params": {"value": "48000"},
            }
        },
    },
]

_RETRIEVAL_READY_TOOLS = {"search_grc", "describe_block", "propose_edit", "apply_edit"}


def _build_parser(config: AppConfig | None = None) -> argparse.ArgumentParser:
    llama_config = config.llama if config is not None else None

    parser = argparse.ArgumentParser(description="GRC Agent CLI")
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
    subparsers.required = True

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

    fake_parser = subparsers.add_parser(
        "fake",
        help="Run a deterministic fake-model step through the runtime.",
    )
    fake_parser.add_argument("file", help="Path to a .grc file to load.")

    chat_parser = subparsers.add_parser(
        "chat",
        help="Run one or more llama.cpp-backed turns against a loaded graph. "
        "With --message, runs a single turn; without it, starts an interactive REPL.",
    )
    chat_parser.add_argument("file", help="Path to a .grc file to load.")
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

    tool_parser = subparsers.add_parser(
        "tool",
        help="Execute one routed runtime tool directly without a model backend.",
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

    return parser


def _maybe_translate_legacy_args(argv: list[str]) -> list[str]:
    if not argv or argv[0] in {"fake", "chat", "tool"}:
        return argv

    if "--fake" in argv:
        translated = [arg for arg in argv if arg != "--fake"]
        return ["fake", *translated]

    if "--message" in argv:
        return ["chat", *argv]

    return argv


def _print_history(agent: GrcAgent) -> None:
    """Render runtime history in a compact CLI-friendly form."""
    print("\n--- History ---")
    for turn in agent.history:
        if turn.get("role") == "session" and isinstance(turn.get("content"), dict):
            printable_turn = dict(turn)
            printable_turn["content"] = json.dumps(turn["content"], sort_keys=True)
            print(printable_turn)
            continue
        if turn.get("role") == "assistant" and turn.get("tool_calls"):
            for tc in turn["tool_calls"]:
                # Flat structure (fake mode): {"name": ..., "arguments": ...}
                # Nested structure (llama mode): {"function": {"name": ..., "arguments": ...}}
                fn = tc.get("function") or {}
                name = tc.get("name") or fn.get("name") or "?"
                print(f"Assistant called {name}: {json.dumps(tc.get('arguments', fn.get('arguments', {})))}")
            continue
        if turn.get("role") == "tool" and isinstance(turn.get("content"), dict):
            printable_turn = dict(turn)
            printable_turn["content"] = json.dumps(turn["content"], sort_keys=True)
            print(printable_turn)
            continue
        print(turn)


def _print_active_session(agent: GrcAgent) -> None:
    """Render the currently bound session before running the chat loop."""
    active_session = agent.active_session_snapshot()
    print("\n--- Active Session ---")
    if active_session is None:
        print("No active flowgraph session.")
        return
    validation = active_session["validation"]["status"]
    print(
        f"{active_session['path']} "
        f"(graph_id={active_session['graph_id']}, "
        f"state_revision={active_session['state_revision']}, "
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
        session.load(file_path)
    return session


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
    print(f"Loading {file_path}...")
    session = _load_initial_session(file_path)
    retrieval_status, catalog_root = _prepare_retrieval()
    if retrieval_status != 0:
        return retrieval_status
    agent = GrcAgent(session, catalog_root=catalog_root, config=config.agent)
    _print_active_session(agent)

    print("--- System Prompt ---")
    print(agent.get_system_prompt())
    print("---------------------\n")

    agent.run_step_fake(FAKE_USER_MESSAGE, FAKE_ACTIONS)

    _print_history(agent)

    return 0


def _run_llama_runtime(
    file_path: str,
    user_message: str | None,
    config: AppConfig,
    server_url: str,
    model: str | None,
    api_key: str | None,
) -> int:
    """Run one or more bounded llama.cpp-backed turns against the routed runtime."""
    print(f"Loading {file_path}...")
    logger.info("chat_start file=%s message=%s", file_path, user_message[:80] if user_message else None)
    session = _load_initial_session(file_path)
    retrieval_status, catalog_root = _prepare_retrieval()
    if retrieval_status != 0:
        return retrieval_status
    agent = GrcAgent(session, catalog_root=catalog_root, config=config.agent)
    _print_active_session(agent)
    llama_config = config.llama
    launcher = LlamaServerLauncher(
        llama_config,
        server_url=server_url,
        model_alias=model,
        api_key=api_key,
    )
    try:
        launch_result = launcher.ensure_server_ready()
    except LlamaLauncherError as exc:
        logger.error("launcher_failed error=%s", exc)
        print("\n--- Launcher ---")
        print(str(exc))
        return 1

    client = LlamaServerClient(
        base_url=server_url,
        api_key=api_key,
        timeout_seconds=llama_config.request_timeout_seconds,
        max_tokens=llama_config.max_tokens,
        temperature=llama_config.temperature,
        enable_thinking=llama_config.enable_thinking,
    )

    if launch_result.status == "started":
        logger.info("server_started url=%s pid=%s", launch_result.server_url, launch_result.pid)
        print(
            f"Started llama.cpp server for {launch_result.model_alias} "
            f"at {launch_result.server_url} (pid {launch_result.pid})"
        )
    else:
        logger.info("server_reused url=%s", launch_result.server_url)
        print(
            f"Reusing llama.cpp server for {launch_result.model_alias} "
            f"at {launch_result.server_url}"
        )

    if user_message is not None:
        return _run_single_turn(agent, client, user_message, model)

    return _run_repl_loop(agent, client, model)


def _run_single_turn(
    agent: GrcAgent,
    client: LlamaServerClient,
    user_message: str,
    model: str | None,
) -> int:
    """Run one bounded llama turn and print the result."""
    try:
        result = run_bounded_llama_turn(
            agent,
            client,
            user_message,
            model=model,
        )
    except LlamaServerError as exc:
        print("\n--- Runtime ---")
        print(str(exc))
        return 1

    print(f"Using model {result['model']}")
    if result["ok"]:
        print("\n--- Assistant ---")
        print(result["assistant_text"])
    else:
        print("\n--- Runtime ---")
        print(result["message"])

    _print_history(agent)
    return 0 if result["ok"] else 1


def _run_repl_loop(
    agent: GrcAgent,
    client: LlamaServerClient,
    model: str | None,
) -> int:
    """Run an interactive REPL loop over the current agent and session."""
    print("\nInteractive REPL. Type /quit or /exit to stop.\n")
    last_exit_code = 0

    while True:
        try:
            user_input = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in ("/quit", "/exit"):
            break

        _print_active_session(agent)

        try:
            result = run_bounded_llama_turn(
                agent,
                client,
                user_input,
                model=model,
            )
        except LlamaServerError as exc:
            print(f"\n--- Runtime Error ---\n{exc}")
            last_exit_code = 1
            continue

        if result["ok"]:
            print(f"\n--- Assistant ---\n{result['assistant_text']}")
        else:
            print(f"\n--- Runtime ---\n{result['message']}")
            last_exit_code = 1

        _print_history(agent)
        print()

    return last_exit_code


def _run_tool_command(
    tool_name: str, tool_kwargs: dict[str, Any], file_path: str | None, config: AppConfig
) -> int:
    """Execute one routed runtime tool directly and print the structured result."""
    session = _load_initial_session(file_path)
    catalog_root: str | None = None
    if tool_name in _RETRIEVAL_READY_TOOLS:
        retrieval_status, catalog_root = _prepare_retrieval()
        if retrieval_status != 0:
            return retrieval_status

    agent = GrcAgent(session, catalog_root=catalog_root, config=config.agent)
    result = agent.execute_tool(tool_name, tool_kwargs)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


def _run_health_command(config: AppConfig) -> int:
    """Print a structured agent health check and return 0 when healthy."""
    readiness = initialize_retrieval()
    catalog_root = readiness.get("catalog_root") if readiness.get("ok") else None
    session = FlowgraphSession()
    agent = GrcAgent(session, catalog_root=catalog_root, config=config.agent)
    report = agent.health_check()
    if not readiness.get("ok"):
        report["retrieval_message"] = readiness.get("message", "Retrieval not ready.")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


def _run_doctor_command(
    *,
    config_path: str | None,
    json_output: bool,
    skip_retrieval: bool,
) -> int:
    """Execute the packaged-app doctor checks."""
    report = run_doctor(config_path=config_path, check_retrieval=not skip_retrieval)
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
    parser = _build_parser()
    args = parser.parse_args(translated_argv)

    if args.verbose:
        logging.getLogger("grc_agent").setLevel(logging.DEBUG)

    app_config = load_app_config(args.config)

    if args.command == "doctor":
        return _run_doctor_command(
            config_path=args.config,
            json_output=args.json,
            skip_retrieval=args.skip_retrieval,
        )

    if args.command == "health":
        return _run_health_command(app_config)

    if args.command == "fake":
        return _run_fake_runtime(args.file, app_config)

    if args.command == "chat":
        return _run_llama_runtime(
            args.file,
            args.message,
            app_config,
            app_config.llama.server_url
            if args.llama_server_url is None
            else args.llama_server_url,
            app_config.llama.model if args.model is None else args.model,
            args.api_key,
        )

    if args.command == "tool":
        try:
            tool_kwargs = _parse_tool_kwargs(args.args)
        except ValueError as exc:
            parser.error(str(exc))
        return _run_tool_command(args.tool_name, tool_kwargs, args.file, app_config)

    parser.error("Unknown command.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
