"""Single scenario verification driver for GRC Agent against a fast local OpenAI-compatible endpoint.

Simulates the real agent experience inside the harness:
- Connects to local endpoint (e.g. http://192.168.0.234:9000/v1, model qwen3.8-27b)
- Uses real GRC tools (change_graph, inspect_graph, query_knowledge, etc.)
- Drives automated tool approval loop so mutations execute in-memory on GNU Radio
- Captures native reasoning traces (ThinkingPart), tool calls, tool results, and responses
- Validates the resulting flowgraph with native GRC compiler validation
- Saves structured JSON and markdown reports under experiments/atomic_dataset/runs/
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

import httpx
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
from grc_agent.agent import (
    GrcAgentResponse,
    StopGracefully,
    grc_tools,
    json_repair_cap,
    validate_flowgraph_state,
    web_fetch_cap,
    web_search_cap,
)
from grc_agent.prompts import build_system_prompt

_log = logging.getLogger("run_single_scenario")


def run_with_auto_approvals(agent: Agent, prompt: str | None = None, **kwargs: Any) -> Any:
    """Drive agent execution loop, auto-approving deferred tool calls (e.g. change_graph)."""
    res = agent.run_sync(prompt, **kwargs)
    call_kwargs = {k: v for k, v in kwargs.items() if k != "message_history"}
    step_count = 0
    while isinstance(res.output, DeferredToolRequests):
        step_count += 1
        print(f"\n[Harness Loop] Turn step {step_count}: Auto-approving {len(res.output.approvals)} tool calls...")
        for call in res.output.approvals:
            print(f"  -> Approving: {call.tool_name} (id={call.tool_call_id})")
        res = agent.run_sync(
            message_history=res.all_messages(),
            deferred_tool_results=DeferredToolResults(
                approvals={c.tool_call_id: ToolApproved() for c in res.output.approvals}
            ),
            **call_kwargs,
        )
    return res


def extract_trace_events(run_result: Any) -> list[dict[str, Any]]:
    """Extract ordered conversation trace events including thinking, tool calls, and returns."""
    events = []
    messages = run_result.all_messages() if run_result else []

    for msg_idx, msg in enumerate(messages):
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    events.append({
                        "msg_index": msg_idx,
                        "role": "user",
                        "type": "user_prompt",
                        "content": part.content,
                    })
                elif isinstance(part, ToolReturnPart):
                    events.append({
                        "msg_index": msg_idx,
                        "role": "tool",
                        "type": "tool_return",
                        "tool_name": getattr(part, "tool_name", "unknown"),
                        "tool_call_id": part.tool_call_id,
                        "content": part.content,
                    })
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ThinkingPart):
                    events.append({
                        "msg_index": msg_idx,
                        "role": "assistant",
                        "type": "thinking",
                        "content": part.content,
                        "tokens_approx": len(part.content.split()),
                    })
                elif isinstance(part, ToolCallPart):
                    try:
                        args_dict = (
                            part.args
                            if isinstance(part.args, dict)
                            else json.loads(part.args_as_json_str())
                        )
                    except Exception:
                        args_dict = str(part.args)
                    events.append({
                        "msg_index": msg_idx,
                        "role": "assistant",
                        "type": "tool_call",
                        "tool_name": part.tool_name,
                        "tool_call_id": part.tool_call_id,
                        "args": args_dict,
                    })
                elif isinstance(part, TextPart):
                    events.append({
                        "msg_index": msg_idx,
                        "role": "assistant",
                        "type": "text",
                        "content": part.content,
                    })
    return events


def format_markdown_report(
    scenario_name: str,
    endpoint: str,
    model_name: str,
    fixture_path: str,
    prompt: str,
    events: list[dict[str, Any]],
    final_output: Any,
    flowgraph_validation: dict[str, Any],
    elapsed_seconds: float,
) -> str:
    lines = [
        f"# Scenario Run: {scenario_name}",
        "",
        f"- **Date / Time:** {datetime.datetime.now().isoformat()}",
        f"- **Endpoint:** `{endpoint}`",
        f"- **Model:** `{model_name}`",
        f"- **Fixture:** `{Path(fixture_path).name}`",
        f"- **Duration:** `{elapsed_seconds:.2f}s`",
        f"- **GRC Graph Validation:** `{'VALID' if flowgraph_validation.get('status') == 'valid' else 'INVALID'}`",
        "",
        "## User Prompt",
        "",
        f"> {prompt}",
        "",
        "## Execution Trace (Reasoning & Tools)",
        "",
    ]

    for ev in events:
        ev_type = ev["type"]
        if ev_type == "thinking":
            lines.extend([
                "### 🧠 Model Reasoning (Thinking Trace)",
                "",
                "```text",
                ev["content"].strip(),
                "```",
                "",
            ])
        elif ev_type == "tool_call":
            lines.extend([
                f"### 🛠️ Tool Call: `{ev['tool_name']}`",
                f"- **Call ID:** `{ev['tool_call_id']}`",
                "",
                "```json",
                json.dumps(ev["args"], indent=2, default=str),
                "```",
                "",
            ])
        elif ev_type == "tool_return":
            content_str = ev["content"]
            if isinstance(content_str, str):
                try:
                    parsed = json.loads(content_str)
                    content_str = json.dumps(parsed, indent=2)
                except Exception:
                    pass
            else:
                content_str = json.dumps(content_str, indent=2, default=str)
            lines.extend([
                f"### ↩️ Tool Return: `{ev['tool_name']}`",
                f"- **Call ID:** `{ev['tool_call_id']}`",
                "",
                "```json",
                content_str,
                "```",
                "",
            ])
        elif ev_type == "text":
            lines.extend([
                "### 💬 Assistant Message",
                "",
                ev["content"].strip(),
                "",
            ])

    lines.extend([
        "## Final Response Output",
        "",
        "```text",
        str(final_output),
        "```",
        "",
        "## Final Flowgraph Validation",
        "",
        "```json",
        json.dumps(flowgraph_validation, indent=2, default=str),
        "```",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run single scenario verification on GRC agent.")
    parser.add_argument("--endpoint", default="http://192.168.0.234:9000/v1", help="OpenAI-compatible base URL")
    parser.add_argument("--model", default="qwen3.8-27b", help="Model ID")
    parser.add_argument("--max-tokens", type=int, default=8192, help="Max tokens (budget for reasoning + output)")
    parser.add_argument("--fixture", default="tests/data/dial_tone.grc", help="Base .grc fixture file")
    parser.add_argument("--out-dir", default="experiments/atomic_dataset/runs", help="Output directory for traces")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("==================================================================")
    print(" GRC Agent Single Scenario Verification Harness")
    print("==================================================================")
    print(f"Endpoint:   {args.endpoint}")
    print(f"Model:      {args.model}")
    print(f"Max Tokens: {args.max_tokens}")
    print(f"Fixture:    {args.fixture}")
    print("==================================================================")

    # 1. Prepare isolated fixture flowgraph in tempdir
    tmp_dir = tempfile.mkdtemp(prefix="grc_scenario_")
    tmp_fixture = Path(tmp_dir) / Path(args.fixture).name
    shutil.copy2(args.fixture, tmp_fixture)
    print(f"Loaded isolated flowgraph: {tmp_fixture}")

    fg = load_flow_graph(str(tmp_fixture))
    init_snap = inspect_graph(fg).get("graph", {})
    init_blocks = [b["instance_name"] for b in init_snap.get("blocks", [])]
    print(f"Initial flowgraph contains {len(init_blocks)} blocks: {init_blocks}")

    # 2. Build model and agent
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=15.0, read=180.0, write=60.0, pool=30.0)
    )
    model = OpenAIChatModel(
        args.model,
        provider=OpenAIProvider(base_url=args.endpoint, api_key="not-needed", http_client=http_client),
        profile=ModelProfile(openai_supports_strict_tool_definition=False),
    )

    agent = Agent(
        model=model,
        deps_type=Any,
        output_type=[GrcAgentResponse, str, DeferredToolRequests],
        name="grc_verification_agent",
        instructions=build_system_prompt("pai-experiment-test"),
        tools=grc_tools(),
        capabilities=[
            json_repair_cap,
            StopGracefully(),
            web_search_cap,
            web_fetch_cap,
        ],
        model_settings=ModelSettings(max_tokens=args.max_tokens),
        retries={"tools": 3, "output": 3},
    )
    agent.output_validator(validate_flowgraph_state)

    # 3. Define the scenario prompt
    prompt = (
        "Take a look at the flowgraph, then add a throttle block in the"
        " path between the 350 Hz tone and the adder that mixes the tones"
        " together. Call it `mid_throttle`, set its type to float, and"
        " have it use the samp_rate variable for its rate. Make sure the"
        " wiring is rerouted so it actually sits inline. Then inspect the"
        " result to confirm."
    )
    print(f"\nUser Prompt:\n> {prompt}\n")

    # 4. Execute the agent with live timing
    start_time = time.monotonic()
    try:
        res = run_with_auto_approvals(agent, prompt, deps=fg)
    except Exception as exc:
        print(f"\n[ERROR] Agent execution failed: {exc}")
        raise
    elapsed = time.monotonic() - start_time

    print(f"\n[SUCCESS] Agent run completed in {elapsed:.2f}s!")

    # 5. Inspect final flowgraph
    final_snap = inspect_graph(fg).get("graph", {})
    validation = final_snap.get("validation", {})
    final_blocks = [b["instance_name"] for b in final_snap.get("blocks", [])]
    final_conns = final_snap.get("connections", [])

    print("\n---------------- Final Flowgraph State ----------------")
    print(f"Blocks:     {final_blocks}")
    print(f"Throttle Present: {'mid_throttle' in final_blocks}")
    print(f"Validation: {validation.get('status')} (errors: {validation.get('errors')})")
    print(f"Connections ({len(final_conns)}): {final_conns}")
    print("-------------------------------------------------------")

    # 6. Extract events & thinking
    events = extract_trace_events(res)
    thinking_events = [e for e in events if e["type"] == "thinking"]
    tool_events = [e for e in events if e["type"] == "tool_call"]
    print(f"\nCaptured {len(events)} trace events:")
    print(f"- {len(thinking_events)} thinking trace blocks")
    print(f"- {len(tool_events)} tool calls ({', '.join(t['tool_name'] for t in tool_events)})")

    # 7. Write reports
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_json_path = out_dir / f"run_{timestamp}_01_add_throttle.json"
    report_md_path = out_dir / f"run_{timestamp}_01_add_throttle.md"

    run_payload = {
        "timestamp": datetime.datetime.now().isoformat(),
        "scenario": "01_add_throttle",
        "endpoint": args.endpoint,
        "model": args.model,
        "elapsed_seconds": elapsed,
        "prompt": prompt,
        "events": events,
        "final_output": res.output.model_dump() if hasattr(res.output, "model_dump") else str(res.output),
        "validation": validation,
        "final_blocks": final_blocks,
        "final_connections": final_conns,
    }
    report_json_path.write_text(json.dumps(run_payload, indent=2, default=str), encoding="utf-8")

    md_report = format_markdown_report(
        scenario_name="01_add_throttle",
        endpoint=args.endpoint,
        model_name=args.model,
        fixture_path=str(tmp_fixture),
        prompt=prompt,
        events=events,
        final_output=res.output,
        flowgraph_validation=validation,
        elapsed_seconds=elapsed,
    )
    report_md_path.write_text(md_report, encoding="utf-8")

    print(f"\nSaved structured JSON trace to: {report_json_path}")
    print(f"Saved human-readable Markdown to: {report_md_path}")

    # Clean up tempdir
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
