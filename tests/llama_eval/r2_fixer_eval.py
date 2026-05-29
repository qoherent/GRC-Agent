#!/usr/bin/env python3
"""R2 Fixer Scenario: Feed a broken graph to the agent and trace its repair.

Two deliberate errors in the fixture:
  1. blocks_throttle2_0.samples_per_second = 'missing_var' (undefined variable)
  2. analog_random_source_x_0:0 is a dangling output (no connection)

Prompt: "This graph is failing validation. Inspect it, identify the errors,
         and fix it so it compiles."

Run:
    uv run python tests/llama_eval/r2_fixer_eval.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.prompt import build_system_prompt

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "r2_broken_fixer.grc"


def _make_provider():
    from grc_agent.config import load_app_config
    from grc_agent.toolagents_runtime import ToolAgentsLlamaProviderConfig
    cfg = load_app_config()
    llama = cfg.llama
    return ToolAgentsLlamaProviderConfig(
        base_url=llama.server_url,
        model=llama.model or "",
        max_tokens=llama.max_tokens,
        temperature=llama.temperature,
    )


def _extract_trace(agent: GrcAgent, start_index: int) -> list[dict]:
    trace = []
    for turn in agent.history[start_index:]:
        role = turn.get("role")
        if role == "assistant":
            for tc in turn.get("tool_calls", []):
                fn = tc.get("function", {})
                name = fn.get("name", "?")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        pass
                trace.append({"turn": "assistant", "tool": name, "arguments": args})
        elif role == "tool":
            content = turn.get("content", {})
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    content = {"raw": str(content)[:200]}
            ok = content.get("ok") if isinstance(content, dict) else None
            committed = content.get("committed") if isinstance(content, dict) else None
            error_type = content.get("error_type") if isinstance(content, dict) else None
            effects = content.get("effects", content.get("effect", "")) if isinstance(content, dict) else ""
            msg = str(content.get("message", ""))[:300] if isinstance(content, dict) else ""
            trace.append({
                "turn": "tool",
                "name": turn.get("name", "?"),
                "ok": ok,
                "committed": committed,
                "error_type": error_type,
                "effects": str(effects)[:200],
                "message": msg,
            })
    return trace


def main() -> int:
    from grc_agent.toolagents_runtime import run_bounded_toolagents_turn

    provider = _make_provider()
    print(f"\nProvider: {provider.base_url} model={provider.model}")
    print(f"Fixture: {FIXTURE}")

    # Load broken fixture directly (bypass validate-gate in load_grc)
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        src = FIXTURE
        dst = workspace / "broken.grc"
        shutil.copy2(src, dst)

        session = FlowgraphSession()
        session.load(dst)
        agent = GrcAgent(session)

        # Verify broken state
        print("\n=== Pre-turn state ===")
        for b in session.flowgraph.blocks:
            print(f"  block: {b.instance_name} ({b.block_type})")
        for c in session.flowgraph.connections:
            print(f"  conn:  {c.src_block}:{c.src_port} -> {c.dst_block}:{c.dst_port}")
        print(f"  validation: {session.validation_state().get('status', 'unknown')}")

        # Record history start
        start_index = len(agent.history)
        system_prompt = build_system_prompt()
        print(f"\n=== System Prompt ({len(system_prompt)} chars) ===")
        print(f"  {system_prompt[:200]}...")

        # Run the agent turn
        prompt = (
            "This graph is failing GNU Radio validation. "
            "Inspect the graph, find the compilation errors, and fix them "
            "so the graph compiles cleanly. Do NOT use force=true."
        )

        print(f"\n=== Prompt ({len(prompt)} chars) ===")
        print(f"  {prompt}")
        print("\n=== Running agent turn ===")

        result = run_bounded_toolagents_turn(
            agent=agent,
            user_message=prompt,
            client=provider,
            model=provider.model,
            mvp_tool_profile=True,
        )

        # Extract trace
        trace = _extract_trace(agent, start_index)

        print("\n=== Agent Trace ===")
        print(f"  Assistant text: {result.get('assistant_text', '')[:500]}")
        print(f"  Tool rounds: {result.get('tool_rounds_used', '?')}")
        print(f"  Tool calls executed: {result.get('tool_calls_executed', '?')}")
        print()
        for step in trace:
            role = step.get("turn")
            if role == "assistant":
                print(f"  [{role}] {step.get('tool')}")
                args = step.get('arguments', {})
                if isinstance(args, dict):
                    print(f"         args: {json.dumps(args, sort_keys=True)}")
            else:
                status = "COMMITTED" if step.get("committed") else ("FAIL" if step.get("ok") is False else "OK")
                print(f"  [{role}]  {step.get('name')} -> {status}")
                if step.get("effects"):
                    print(f"         effects: {step.get('effects')[:200]}")
                if step.get("message"):
                    print(f"         message: {step.get('message')[:300]}")

        # Check final state
        print("\n=== Post-turn state ===")
        for b in session.flowgraph.blocks:
            print(f"  block: {b.instance_name} ({b.block_type})")
        for c in session.flowgraph.connections:
            print(f"  conn:  {c.src_block}:{c.src_port} -> {c.dst_block}:{c.dst_port}")
        v = session.validate()
        if v:
            print(f"  validation: {v.get('status')}")
            for e in v.get('errors', [])[:5]:
                print(f"    err: {str(e)[:200]}")
        else:
            print("  validation: unavailable")

    return 0


if __name__ == "__main__":
    sys.exit(main())
