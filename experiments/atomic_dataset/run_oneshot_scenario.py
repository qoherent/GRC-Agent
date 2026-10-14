"""One-shot atomic tool-use verification driver for GRC Agent.

Executes single-turn atomic interactions against a fast local OpenAI endpoint:
- Prompt -> Model Reasoning -> Single Tool Call -> Headless GRC Execution -> Tool Return -> Final Confirmation
- Zero exploratory turns (no inspect-before-act or query-before-act loops)
- Headless in-memory validation
- Exports standard Unsloth/OpenAI format dataset records
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic_ai import Agent, ModelSettings
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelProfile
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolApproved

from grc_agent.adapter import inspect_graph, load_flow_graph
from grc_agent.agent import grc_tools, json_repair_cap
from grc_agent.prompts import build_system_prompt

_log = logging.getLogger("run_oneshot")

ONESHOT_INSTRUCTION = (
    "You are an expert GNU Radio Companion assistant.\n"
    "CRITICAL REQUIREMENT: Execute the user's requested action DIRECTLY using the appropriate tool call in ONE SHOT.\n"
    "Do NOT call inspect_graph or query_knowledge before acting unless the user explicitly asks you to inspect or search.\n"
    "Emit your reasoning and then call the target tool immediately."
)


def _format_assistant_message(msg: ModelResponse) -> dict[str, Any]:
    reasoning_chunks: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    text_chunks: list[str] = []

    for part in msg.parts:
        if isinstance(part, ThinkingPart):
            reasoning_chunks.append(part.content)
        elif isinstance(part, ToolCallPart):
            args_dict = (
                part.args
                if isinstance(part.args, dict)
                else (
                    json.loads(part.args_as_json_str())
                    if hasattr(part, "args_as_json_str")
                    else part.args
                )
            )
            tool_calls.append({
                "id": part.tool_call_id,
                "type": "function",
                "function": {
                    "name": part.tool_name,
                    "arguments": json.dumps(args_dict),
                },
            })
        elif isinstance(part, TextPart):
            text_chunks.append(part.content)

    asst_msg: dict[str, Any] = {"role": "assistant"}
    if reasoning_chunks:
        asst_msg["reasoning_content"] = "\n".join(reasoning_chunks)
    if tool_calls:
        asst_msg["tool_calls"] = tool_calls
    if text_chunks:
        asst_msg["content"] = "\n".join(text_chunks)
    elif not tool_calls:
        asst_msg["content"] = ""
    return asst_msg


def _convert_to_openai_messages(system_prompt: str | None, messages: list[Any]) -> list[dict[str, Any]]:
    openai_messages: list[dict[str, Any]] = []
    if system_prompt and isinstance(system_prompt, str):
        openai_messages.append({"role": "system", "content": system_prompt})

    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    openai_messages.append({"role": "user", "content": part.content})
                elif isinstance(part, ToolReturnPart):
                    content_str = (
                        part.content
                        if isinstance(part.content, str)
                        else json.dumps(part.content, default=str)
                    )
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": part.tool_call_id,
                        "name": getattr(part, "tool_name", "unknown"),
                        "content": content_str,
                    })
        elif isinstance(msg, ModelResponse):
            openai_messages.append(_format_assistant_message(msg))
    return openai_messages


def run_oneshot(
    agent: Agent,
    prompt: str,
    deps: Any,
    system_prompt: str | None = None,
) -> tuple[Any, list[dict[str, Any]], float]:
    """Execute a single-turn prompt and capture the one-shot tool call and response."""
    start_time = time.monotonic()
    res = agent.run_sync(prompt, deps=deps)

    # If the tool call is deferred (e.g. change_graph requires approval), approve it immediately
    if isinstance(res.output, DeferredToolRequests):
        approvals = {c.tool_call_id: ToolApproved() for c in res.output.approvals}
        res = agent.run_sync(
            message_history=res.all_messages(),
            deferred_tool_results=DeferredToolResults(approvals=approvals),
            deps=deps,
        )

    elapsed = time.monotonic() - start_time
    openai_messages = _convert_to_openai_messages(system_prompt, res.all_messages())
    return res, openai_messages, elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one-shot atomic tool call scenarios.")
    parser.add_argument("--endpoint", default="http://192.168.0.234:9000/v1", help="OpenAI base URL")
    parser.add_argument("--model", default="qwen3.8-27b", help="Model ID")
    parser.add_argument("--max-tokens", type=int, default=4096, help="Max tokens")
    parser.add_argument("--fixture", default="tests/data/dial_tone.grc", help="Base .grc fixture")
    parser.add_argument("--out-dir", default="experiments/atomic_dataset/runs_oneshot", help="Output directory")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    http_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0, read=180.0, write=60.0, pool=30.0))
    model = OpenAIChatModel(
        args.model,
        provider=OpenAIProvider(base_url=args.endpoint, api_key="not-needed", http_client=http_client),
        profile=ModelProfile(openai_supports_strict_tool_definition=False),
    )

    sys_prompt = build_system_prompt("pai-experiment-test") + "\n\n" + ONESHOT_INSTRUCTION
    agent = Agent(
        model=model,
        deps_type=Any,
        instructions=sys_prompt,
        tools=grc_tools(),
        capabilities=[json_repair_cap],
        model_settings=ModelSettings(max_tokens=args.max_tokens),
        output_type=[str, DeferredToolRequests],
    )

    # Define test suite of 4 atomic one-shot scenarios
    scenarios = [
        {
            "id": "oneshot_01_update_param",
            "desc": "Update frequency variable parameter",
            "prompt": "In the dial_tone flowgraph, change the frequency parameter `value` of the variable `freq_350` to `380`.",
            "target_tool": "change_graph",
        },
        {
            "id": "oneshot_02_add_block",
            "desc": "Add low pass filter block",
            "prompt": "Add a Low Pass Filter block (`low_pass_filter`) named `clean_lpf` with type `fir_filter_fff`, decim `1`, gain `1`, samp_rate `samp_rate`, and cutoff_freq `400`.",
            "target_tool": "change_graph",
        },
        {
            "id": "oneshot_03_query_knowledge",
            "desc": "Query catalog documentation for quadrature demodulator",
            "prompt": "Look up the GNU Radio block catalog entry for `analog_quadrature_demod_cf` and return its parameters.",
            "target_tool": "query_knowledge",
        },
        {
            "id": "oneshot_04_inspect_target",
            "desc": "Inspect specific targets in the flowgraph",
            "prompt": "Inspect the parameters and state of the `blocks_add_xx` and `audio_sink` blocks in the current flowgraph.",
            "target_tool": "inspect_graph",
        },
    ]

    print("==================================================================")
    print(" GRC Agent One-Shot Atomic Dataset Verification")
    print("==================================================================")
    print(f"Endpoint:   {args.endpoint}")
    print(f"Model:      {args.model}")
    print(f"Scenarios:  {len(scenarios)}")
    print("==================================================================")

    all_records = []

    for sc in scenarios:
        sc_id = sc["id"]
        prompt = sc["prompt"]
        print(f"\n--- Running: {sc_id} ({sc['desc']}) ---")
        print(f"Prompt: {prompt}")

        # Isolated copy of fixture for each scenario
        tmp_dir = tempfile.mkdtemp(prefix=f"grc_{sc_id}_")
        tmp_fixture = Path(tmp_dir) / Path(args.fixture).name
        shutil.copy2(args.fixture, tmp_fixture)
        fg = load_flow_graph(str(tmp_fixture))

        res, openai_msgs, elapsed = run_oneshot(agent, prompt, deps=fg, system_prompt=sys_prompt)

        # Count tool calls in messages
        asst_tool_calls = [
            tc
            for m in openai_msgs
            if m.get("role") == "assistant"
            for tc in m.get("tool_calls", [])
        ]
        tool_names = [tc["function"]["name"] for tc in asst_tool_calls]

        # In-memory validation
        snap = inspect_graph(fg).get("graph", {})
        validation = snap.get("validation", {})
        is_valid = validation.get("status") == "valid"

        print(f"Completed in: {elapsed:.2f}s")
        print(f"Tool calls emitted: {tool_names} (expected {sc['target_tool']})")
        print(f"GRC Flowgraph Validation: {'VALID' if is_valid else 'INVALID'} (errors: {validation.get('errors')})")

        sample_record = {
            "scenario_id": sc_id,
            "description": sc["desc"],
            "prompt": prompt,
            "elapsed_seconds": elapsed,
            "tool_calls": tool_names,
            "flowgraph_valid": is_valid,
            "messages": openai_msgs,
        }
        all_records.append(sample_record)

        # Save individual trace
        sample_path = out_dir / f"{sc_id}.json"
        sample_path.write_text(json.dumps(sample_record, indent=2, default=str), encoding="utf-8")
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Save complete dataset JSONL
    jsonl_path = out_dir / "sft_oneshot_samples.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for rec in all_records:
            f.write(json.dumps({"messages": rec["messages"]}) + "\n")

    print("\n==================================================================")
    print(f"All {len(scenarios)} one-shot scenarios completed successfully!")
    print(f"Exported Unsloth-ready JSONL: {jsonl_path}")
    print("==================================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
