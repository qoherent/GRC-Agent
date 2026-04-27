"""Run a non-gating live llama.cpp reliability matrix and print JSON."""

import argparse
import json
import os
from pathlib import Path
import sys
import time

from grc_agent.agent import GrcAgent
from grc_agent.config import load_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import (
    LlamaServerClient,
    LlamaServerError,
    run_bounded_llama_turn,
)


CASES = [
    ("summarize", "Summarize the graph."),
    (
        "set_and_validate",
        "Change the samp_rate variable to 48000 and validate the graph.",
    ),
    (
        "missing_variable_recovery",
        "Set the variable does_not_exist to 123 and validate the graph.",
    ),
    (
        "unsupported_structural_request",
        "Add a throttle block and connect it correctly.",
    ),
    ("repeat_summarize_1", "Summarize the graph."),
    ("repeat_summarize_2", "Summarize the graph."),
    ("repeat_summarize_3", "Summarize the graph."),
    ("repeat_summarize_4", "Summarize the graph."),
    ("repeat_summarize_5", "Summarize the graph."),
]


def _default_fixture_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "data"
        / "random_bit_generator.grc"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a live llama.cpp reliability matrix."
    )
    parser.add_argument(
        "--file",
        default=str(_default_fixture_path()),
        help="Path to the .grc fixture to load.",
    )
    parser.add_argument(
        "--server-url",
        default=os.environ.get("GRC_AGENT_LIVE_LLAMA_URL"),
        help="llama.cpp server URL. Defaults to GRC_AGENT_LIVE_LLAMA_URL.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GRC_AGENT_LIVE_LLAMA_MODEL"),
        help="llama.cpp model alias. Defaults to GRC_AGENT_LIVE_LLAMA_MODEL.",
    )
    return parser


def _build_client(server_url: str) -> LlamaServerClient:
    llama_config = load_app_config().llama
    return LlamaServerClient(
        server_url,
        timeout_seconds=llama_config.request_timeout_seconds,
        max_tokens=llama_config.max_tokens,
        temperature=llama_config.temperature,
        enable_thinking=llama_config.enable_thinking,
    )


def _run_case(
    file_path: str, server_url: str, model: str, name: str, prompt: str
) -> dict[str, object]:
    session = FlowgraphSession()
    session.load(file_path)
    agent = GrcAgent(session)
    client = _build_client(server_url)

    started_at = time.perf_counter()
    try:
        client.require_ready()
        result = run_bounded_llama_turn(
            agent,
            client,
            prompt,
            model=model,
        )
        error_message = None
    except LlamaServerError as exc:
        result = None
        error_message = str(exc)
    elapsed_seconds = time.perf_counter() - started_at

    tool_entries = [turn for turn in agent.history if turn.get("role") == "tool"]
    flowgraph = session.flowgraph
    samp_rate_value = None
    if flowgraph is not None:
        samp_rate_value = next(
            (
                block.params["parameters"]["value"]
                for block in flowgraph.blocks
                if block.instance_name == "samp_rate"
            ),
            None,
        )

    return {
        "case": name,
        "prompt": prompt,
        "ok": bool(result and result["ok"]),
        "error": error_message,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "assistant_text": result["assistant_text"] if result and result["ok"] else "",
        "steps": result["steps"] if result else None,
        "tool_calls_executed": result["tool_calls_executed"]
        if result
        else len(tool_entries),
        "tool_names": [entry["name"] for entry in tool_entries],
        "tool_results": [entry["content"] for entry in tool_entries],
        "dirty": session.is_dirty,
        "samp_rate": samp_rate_value,
    }


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.server_url:
        parser.error("--server-url is required or set GRC_AGENT_LIVE_LLAMA_URL")
    if not args.model:
        parser.error("--model is required or set GRC_AGENT_LIVE_LLAMA_MODEL")

    results = [
        _run_case(args.file, args.server_url, args.model, name, prompt)
        for name, prompt in CASES
    ]
    print(json.dumps({"results": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
