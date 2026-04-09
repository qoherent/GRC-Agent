"""Command-line entry point for GRC Agent."""

import argparse
import sys

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession


FAKE_USER_MESSAGE = "Please change the samp_rate to 48000 and validate the graph."
FAKE_ACTIONS = [
    {"text": "I'll do that right away."},
    {
        "tool": "set_param",
        "kwargs": {
            "instance_name": "samp_rate",
            "parameter_key": "value",
            "value": "48000",
        },
    },
    {"tool": "validate", "kwargs": {}},
]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GRC Agent CLI")
    parser.add_argument("file", nargs="?", help="Path to a .grc file to load")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Run a deterministic fake-model step through the runtime",
    )
    return parser


def _run_fake_runtime(file_path: str) -> int:
    print(f"Loading {file_path}...")
    session = FlowgraphSession()
    session.load(file_path)
    agent = GrcAgent(session)

    print("--- System Prompt ---")
    print(agent.get_system_prompt())
    print("---------------------\n")

    agent.run_step_fake(FAKE_USER_MESSAGE, FAKE_ACTIONS)

    print("\n--- History ---")
    for turn in agent.history:
        print(turn)

    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.fake:
        if args.file is None:
            parser.error("--fake requires a .grc file path")
        return _run_fake_runtime(args.file)

    print("GRC Agent CLI placeholder")
    return 0


if __name__ == "__main__":
    sys.exit(main())
