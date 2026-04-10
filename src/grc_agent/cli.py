"""Command-line entry point for GRC Agent."""

import argparse
import json
import sys

from grc_agent.agent import GrcAgent
from grc_agent.config import AppConfig, load_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import LlamaServerClient, LlamaServerError, run_bounded_llama_turn


FAKE_USER_MESSAGE = "Please change the samp_rate to 48000 and validate the graph."
FAKE_ACTIONS = [
    {"text": "I'll do that right away."},
    {
        "tool": "set_variable",
        "kwargs": {
            "instance_name": "samp_rate",
            "value": "48000",
        },
    },
    {"tool": "validate_graph", "kwargs": {}},
]


def _build_parser(config: AppConfig | None = None) -> argparse.ArgumentParser:
    app_config = load_app_config() if config is None else config
    llama_config = app_config.llama

    parser = argparse.ArgumentParser(description="GRC Agent CLI")
    parser.add_argument("file", nargs="?", help="Path to a .grc file to load")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Run a deterministic fake-model step through the runtime",
    )
    parser.add_argument(
        "--message",
        help="Run one bounded llama.cpp turn with this user message",
    )
    parser.add_argument(
        "--llama-server-url",
        default=llama_config.server_url,
        help="Base URL for a llama.cpp HTTP server. Defaults to grc_agent.toml.",
    )
    parser.add_argument(
        "--model",
        default=llama_config.model,
        help="llama.cpp model id. Defaults to the configured value in grc_agent.toml.",
    )
    parser.add_argument(
        "--api-key",
        help="Optional API key for llama.cpp server authentication",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=llama_config.max_steps,
        help="Maximum tool rounds before the bounded llama.cpp loop stops.",
    )
    return parser


def _print_history(agent: GrcAgent) -> None:
    """Render runtime history in a compact CLI-friendly form."""
    print("\n--- History ---")
    for turn in agent.history:
        if turn.get("role") == "tool" and isinstance(turn.get("content"), dict):
            printable_turn = dict(turn)
            printable_turn["content"] = json.dumps(turn["content"], sort_keys=True)
            print(printable_turn)
            continue
        print(turn)


def _run_fake_runtime(file_path: str) -> int:
    """Exercise the narrow runtime contract with deterministic fake actions."""
    print(f"Loading {file_path}...")
    session = FlowgraphSession()
    session.load(file_path)
    agent = GrcAgent(session)

    print("--- System Prompt ---")
    print(agent.get_system_prompt())
    print("---------------------\n")

    agent.run_step_fake(FAKE_USER_MESSAGE, FAKE_ACTIONS)

    _print_history(agent)

    return 0


def _run_llama_runtime(
    file_path: str,
    user_message: str,
    config: AppConfig,
    server_url: str,
    model: str | None,
    api_key: str | None,
    max_steps: int,
) -> int:
    """Run one bounded llama.cpp-backed turn against the narrowed runtime."""
    print(f"Loading {file_path}...")
    session = FlowgraphSession()
    session.load(file_path)
    agent = GrcAgent(session)
    llama_config = config.llama
    client = LlamaServerClient(
        base_url=server_url,
        api_key=api_key,
        timeout_seconds=llama_config.request_timeout_seconds,
        max_tokens=llama_config.max_tokens,
        temperature=llama_config.temperature,
        enable_thinking=llama_config.enable_thinking,
    )
    try:
        client.require_ready()
        result = run_bounded_llama_turn(
            agent,
            client,
            user_message,
            model=model,
            max_steps=max_steps,
        )
    except LlamaServerError as exc:
        print("\n--- Runtime ---")
        print(str(exc))
        return 1

    print(f"Using model {result['model']} via {server_url}")
    if result["ok"]:
        print("\n--- Assistant ---")
        print(result["assistant_text"])
    else:
        print("\n--- Runtime ---")
        print(result["message"])

    _print_history(agent)
    return 0 if result["ok"] else 1


def main() -> int:
    app_config = load_app_config()
    parser = _build_parser(app_config)
    args = parser.parse_args()

    if args.fake and args.message is not None:
        parser.error("--fake cannot be combined with --message")

    if args.fake:
        if args.file is None:
            parser.error("--fake requires a .grc file path")
        return _run_fake_runtime(args.file)

    if args.message is not None:
        if args.file is None:
            parser.error("--message requires a .grc file path")
        return _run_llama_runtime(
            args.file,
            args.message,
            app_config,
            args.llama_server_url,
            args.model,
            args.api_key,
            args.max_steps,
        )

    print("GRC Agent CLI placeholder")
    return 0


if __name__ == "__main__":
    sys.exit(main())
