"""GRC Agent Trace Collection Tool.

High-throughput trace generator and headless verifier for GNU Radio Companion.
Executes atomic one-shot or multi-turn agent interactions against a local
OpenAI-compatible model endpoint (e.g. qwen3.8-27b on NInfer/vLLM), capturing
reasoning traces, tool calls, and headless compiler validation into Unsloth-ready JSONL.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import openai
from pydantic_ai import Agent, ModelResponse, ModelSettings
from pydantic_ai.messages import (
    ModelRequest,
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
_log = logging.getLogger("collect_traces")

ONESHOT_INSTRUCTION = (
    "You are an expert GNU Radio Companion assistant.\n"
    "CRITICAL REQUIREMENT: Execute the user's requested action DIRECTLY using the appropriate tool call in ONE SHOT.\n"
    "Do NOT call inspect_graph or query_knowledge before acting unless the user explicitly asks you to inspect or search.\n"
    "Keep your internal reasoning brief and focused, and emit the target tool call immediately."
)

PRESET_SCENARIOS = [
    {
        "id": "01_update_param",
        "name": "Parameter Update",
        "prompt": "In the dial_tone flowgraph, change the frequency parameter `value` of the variable `freq_350` to `380`.",
        "expected_tool": "change_graph",
    },
    {
        "id": "02_add_throttle",
        "name": "Block Addition (Throttle)",
        "prompt": "Add a Throttle block (`blocks_throttle2`) named `mid_throttle` with type `float` and `samples_per_second` set to `samp_rate`, and wire it inline between `analog_sig_source_x_0:0` and `blocks_add_xx:0` (removing the direct connection between them).",
        "expected_tool": "change_graph",
    },
    {
        "id": "03_add_filter",
        "name": "Filter Addition",
        "prompt": "Add a Low Pass Filter block (`low_pass_filter`) named `clean_lpf` with type `fir_filter_fff`, decim `1`, gain `1`, samp_rate `samp_rate`, cutoff_freq `400`, and width `100`, and connect `blocks_add_xx:0` to `clean_lpf:0` and `clean_lpf:0` to `audio_sink:0` (removing the direct `blocks_add_xx:0->audio_sink:0` connection).",
        "expected_tool": "change_graph",
    },
    {
        "id": "04_query_catalog",
        "name": "Catalog Knowledge Lookup",
        "prompt": "Look up the GNU Radio block catalog entry for `analog_quadrature_demod_cf` and return its parameters.",
        "expected_tool": "query_knowledge",
    },
    {
        "id": "05_inspect_targets",
        "name": "Targeted Graph Inspection",
        "prompt": "Inspect the parameters and state of the `blocks_add_xx` and `audio_sink` blocks in the current flowgraph.",
        "expected_tool": "inspect_graph",
    },
    {
        "id": "06_rewire_connection",
        "name": "Connection Rewiring",
        "prompt": "Connect `analog_noise_source_x_0:0` to `freq_sink_0:0` in the current flowgraph.",
        "expected_tool": "change_graph",
    },
]


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


def run_interaction(
    agent: Agent,
    prompt: str,
    deps: Any,
    system_prompt: str,
) -> tuple[Any, list[dict[str, Any]], float]:
    """Execute interaction with automated tool approval loop."""
    start_time = time.monotonic()
    res = agent.run_sync(prompt, deps=deps)

    while isinstance(res.output, DeferredToolRequests):
        approvals = {c.tool_call_id: ToolApproved() for c in res.output.approvals}
        res = agent.run_sync(
            message_history=res.all_messages(),
            deferred_tool_results=DeferredToolResults(approvals=approvals),
            deps=deps,
        )

    elapsed = time.monotonic() - start_time
    openai_messages = _convert_to_openai_messages(system_prompt, res.all_messages())
    return res, openai_messages, elapsed


def generate_markdown_report(
    scenario_id: str,
    prompt: str,
    elapsed: float,
    is_valid: bool,
    validation_errors: list[Any],
    messages: list[dict[str, Any]],
) -> str:
    lines = [
        f"# Trace Report: {scenario_id}",
        "",
        f"- **Timestamp:** {datetime.datetime.now().isoformat()}",
        f"- **Duration:** {elapsed:.2f}s",
        f"- **Flowgraph Status:** {'VALID' if is_valid else 'INVALID'}",
        "",
        "## Prompt",
        f"> {prompt}",
        "",
        "## Interaction Trace",
        "",
    ]

    for msg in messages:
        role = msg.get("role")
        if role == "user":
            continue
        if role == "assistant":
            reasoning = msg.get("reasoning_content")
            if reasoning:
                lines.extend([
                    "### 🧠 Model Reasoning",
                    "```text",
                    reasoning.strip(),
                    "```",
                    "",
                ])
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                lines.extend([
                    f"### 🛠️ Tool Call: `{fn.get('name')}`",
                    "```json",
                    fn.get("arguments", "{}"),
                    "```",
                    "",
                ])
            content = msg.get("content")
            if content:
                lines.extend([
                    "### 💬 Assistant Response",
                    content.strip(),
                    "",
                ])
        elif role == "tool":
            lines.extend([
                f"### ↩️ Tool Return: `{msg.get('name')}`",
                "```json",
                msg.get("content", ""),
                "```",
                "",
            ])

    if validation_errors:
        lines.extend([
            "## Validation Errors",
            "```json",
            json.dumps(validation_errors, indent=2),
            "```",
            "",
        ])

    return "\n".join(lines)


def build_agent(endpoint: str, model_name: str, max_tokens: int, oneshot: bool) -> tuple[Agent, str]:
    client = openai.AsyncOpenAI(base_url=endpoint, api_key="not-needed", timeout=90.0)
    model = OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(openai_client=client),
        profile=ModelProfile(openai_supports_strict_tool_definition=False),
    )

    base_instructions = build_system_prompt("trace-collection")
    sys_prompt = base_instructions + ("\n\n" + ONESHOT_INSTRUCTION if oneshot else "")

    agent = Agent(
        model=model,
        deps_type=Any,
        instructions=sys_prompt,
        tools=grc_tools(),
        capabilities=[json_repair_cap],
        model_settings=ModelSettings(max_tokens=max_tokens),
        output_type=[str, DeferredToolRequests],
    )
    return agent, sys_prompt


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect reasoning & tool traces for GNU Radio Companion.")
    parser.add_argument("--endpoint", default="http://192.168.0.234:9000/v1", help="OpenAI-compatible base URL")
    parser.add_argument("--model", default="qwen3.8-27b", help="Model ID served by endpoint")
    parser.add_argument("--max-tokens", type=int, default=8192, help="Token budget (reasoning + tool calls)")
    parser.add_argument("--fixture", default="dial_tone.grc", help="Path to base .grc flowgraph fixture")
    parser.add_argument("--out-dir", default="output", help="Directory to save traces and JSONL")
    parser.add_argument("--scenario", default="all", help="Scenario ID to run (or 'all' for all presets)")
    parser.add_argument("--prompt", default=None, help="Custom prompt (overrides preset scenarios)")
    parser.add_argument("--multiturn", action="store_true", help="Allow multi-turn exploratory conversation")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Locate fixture
    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        # Fallback to tests/data/dial_tone.grc if local copy not found
        fallback = Path(__file__).resolve().parent.parent.parent / "tests" / "data" / "dial_tone.grc"
        if fallback.exists():
            fixture_path = fallback
        else:
            print(f"[ERROR] Fixture not found at {fixture_path}")
            return 1

    print("==================================================================")
    print(" GNU Radio Companion - Agent Trace Collector")
    print("==================================================================")
    print(f"Endpoint:   {args.endpoint}")
    print(f"Model:      {args.model}")
    print(f"Max Tokens: {args.max_tokens}")
    print(f"Mode:       {'Multi-turn' if args.multiturn else 'One-shot (direct tool execution)'}")
    print(f"Fixture:    {fixture_path}")
    print(f"Output:     {out_dir}")
    print("==================================================================")

    agent, sys_prompt = build_agent(
        endpoint=args.endpoint,
        model_name=args.model,
        max_tokens=args.max_tokens,
        oneshot=not args.multiturn,
    )

    # Determine tasks
    if args.prompt:
        tasks = [{
            "id": "custom_prompt",
            "name": "Custom User Prompt",
            "prompt": args.prompt,
            "expected_tool": "auto",
        }]
    elif args.scenario != "all":
        tasks = [s for s in PRESET_SCENARIOS if s["id"] == args.scenario]
        if not tasks:
            print(f"[ERROR] Unknown scenario '{args.scenario}'. Available: {[s['id'] for s in PRESET_SCENARIOS]}")
            return 1
    else:
        tasks = PRESET_SCENARIOS

    print(f"Executing {len(tasks)} trace collection task(s)...\n")
    all_records = []
    summary_table = []

    for task in tasks:
        t_id = task["id"]
        prompt = task["prompt"]
        print(f"-> [{t_id}] {task['name']}")
        print(f"   Prompt: \"{prompt}\"")

        # Isolated fixture copy
        tmp_dir = tempfile.mkdtemp(prefix=f"grc_trace_{t_id}_")
        tmp_fixture = Path(tmp_dir) / fixture_path.name
        shutil.copy2(fixture_path, tmp_fixture)
        fg = load_flow_graph(str(tmp_fixture))

        try:
            res, openai_msgs, elapsed = run_interaction(agent, prompt, deps=fg, system_prompt=sys_prompt)
        except Exception as exc:
            print(f"   [FAILED] Error: {exc}\n")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            continue

        # Inspect resulting flowgraph
        snap = inspect_graph(fg).get("graph", {})
        validation = snap.get("validation", {})
        is_valid = validation.get("status") == "valid"
        val_errors = validation.get("errors", [])

        # Extract tool calls
        asst_tool_calls = [
            tc["function"]["name"]
            for m in openai_msgs
            if m.get("role") == "assistant"
            for tc in m.get("tool_calls", [])
        ]

        print(f"   Completed: {elapsed:.2f}s | Tools: {asst_tool_calls} | GRC: {'VALID' if is_valid else 'INVALID'}\n")

        record = {
            "scenario_id": t_id,
            "name": task["name"],
            "prompt": prompt,
            "elapsed_seconds": elapsed,
            "tool_calls": asst_tool_calls,
            "flowgraph_valid": is_valid,
            "validation_errors": val_errors,
            "messages": openai_msgs,
        }
        all_records.append(record)
        summary_table.append((t_id, f"{elapsed:.2f}s", str(asst_tool_calls), "VALID" if is_valid else "INVALID"))

        # Save individual JSON & MD reports
        (out_dir / f"{t_id}.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        md_text = generate_markdown_report(t_id, prompt, elapsed, is_valid, val_errors, openai_msgs)
        (out_dir / f"{t_id}.md").write_text(md_text, encoding="utf-8")

        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Save Unsloth SFT JSONL dataset
    jsonl_path = out_dir / "sft_traces.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in all_records:
            f.write(json.dumps({"messages": r["messages"]}) + "\n")

    print("==================================================================")
    print(" Summary of Collected Traces")
    print("==================================================================")
    print(f"{'ID':<25} {'Time':<10} {'Tools':<30} {'GRC Status'}")
    print("-" * 75)
    for row in summary_table:
        print(f"{row[0]:<25} {row[1]:<10} {row[2]:<30} {row[3]}")
    print("-" * 75)
    print(f"Total traces collected: {len(all_records)} / {len(tasks)}")
    print(f"Unsloth JSONL exported: {jsonl_path}")
    print(f"All reports saved to:   {out_dir}")
    print("==================================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
