#!/usr/bin/env python3
"""Mid-tier freeform scenarios with CoT reasoning traces.

Scenario A (Inline Swap): Source -> LPF -> Sink.
  Prompt: "Replace the Low Pass Filter with a Band Pass Filter."

Scenario B (Parameter Cascade): Source -> Throttle.
  Prompt: "Double the sample rate."

Scenario C (Typo Correction): Functional graph.
  Prompt: "Add an AGC block."

Run:
    uv run python tests/llama_eval/mid_tier_scenarios.py
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


def _make_provider():
    from grc_agent.config import load_app_config
    from grc_agent.toolagents_runtime import ToolAgentsLlamaProviderConfig
    cfg = load_app_config()
    return ToolAgentsLlamaProviderConfig(
        base_url=cfg.llama.server_url,
        model=cfg.llama.model or "",
        max_tokens=cfg.llama.max_tokens,
        temperature=cfg.llama.temperature,
    )


def _run_turn(agent, provider, prompt):
    from grc_agent.toolagents_runtime import run_bounded_toolagents_turn
    start = len(agent.history)
    result = run_bounded_toolagents_turn(
        agent=agent, user_message=prompt, client=provider,
        model=provider.model, mvp_tool_profile=True,
    )
    trace = []
    for turn in agent.history[start:]:
        role = turn.get("role")
        if role == "user":
            continue
        if role == "assistant":
            text = str(turn.get("content", ""))[:500]
            for tc in turn.get("tool_calls", []):
                fn = tc.get("function", {})
                name = fn.get("name", "?")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        args = {"raw": args[:200]}
                trace.append(f"  [{name}] {json.dumps(args, sort_keys=True)}")
            if text.strip():
                trace.append(f"  [think] {text[:500]}")
        elif role == "tool":
            content = turn.get("content", {})
            if isinstance(content, dict):
                ok = content.get("ok")
                committed = content.get("committed")
                err = content.get("error_type", "")
                effects = str(content.get("effects", content.get("effect", "")))[:150]
                if ok and committed:
                    trace.append(f"  [result] COMMITTED effects={effects}")
                elif ok:
                    pass  # read-only tool — skip noise
                else:
                    errs = content.get("validation_errors", content.get("errors", []))
                    err_text = "; ".join(str(e.get("message", e))[:100] for e in (errs if isinstance(errs, list) else [])[:2])
                    trace.append(f"  [result] FAIL {err}: {err_text}")
    return result, trace


def _print_state(session):
    for b in session.flowgraph.blocks:
        st = (b.params.get("states") or {}).get("state", "enabled")
        vt = b.params.get("parameters", {}).get("value", "")
        print(f"  {b.instance_name} ({b.block_type}) state={st} value={vt}")
    for c in session.flowgraph.connections:
        print(f"  {c.src_block}:{c.src_port} -> {c.dst_block}:{c.dst_port}")


def main():
    provider = _make_provider()
    print(f"\nProvider: {provider.base_url} model={provider.model}")
    print(f"\nSystem Prompt ({len(build_system_prompt())} chars):")
    print(f"  {build_system_prompt()}")

    # ── Scenario A: Inline Swap ──
    print(f"\n{'='*60}")
    print("SCENARIO A: Inline Swap (Source -> LPF -> Sink)")
    print(f"{'='*60}")
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        fixture = Path(__file__).resolve().parent.parent / "data" / "random_bit_generator.grc"
        dst = workspace / "test.grc"
        shutil.copy2(fixture, dst)
        session = FlowgraphSession()
        session.load(dst)
        agent = GrcAgent(session)
        print("Pre-turn state:")
        _print_state(session)

        result, trace = _run_turn(agent, provider,
            "Replace the blocks_char_to_float_0 block with a blocks_float_to_float block. "
            "Remove the old block and insert the new one, keeping the same connections.")
        print("\nTrace:")
        for t in trace:
            print(t)
        print(f"\nAssistant: {result.get('assistant_text', '')[:500]}")
        print(f"Rounds: {result.get('tool_rounds_used')}")
        print("\nPost-turn state:")
        _print_state(session)

    # ── Scenario B: Parameter Cascade ──
    print(f"\n{'='*60}")
    print("SCENARIO B: Parameter Cascade (Double the sample rate)")
    print(f"{'='*60}")
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        shutil.copy2(fixture, workspace / "test.grc")
        session = FlowgraphSession()
        session.load(workspace / "test.grc")
        agent = GrcAgent(session)
        print("Pre-turn state:")
        _print_state(session)

        result, trace = _run_turn(agent, provider,
            "Double the sample rate.")
        print("\nTrace:")
        for t in trace:
            print(t)
        print(f"\nAssistant: {result.get('assistant_text', '')[:500]}")
        print(f"Rounds: {result.get('tool_rounds_used')}")
        print("\nPost-turn state:")
        _print_state(session)

    # ── Scenario C: Typo Correction ──
    print(f"\n{'='*60}")
    print("SCENARIO C: Typo Correction (Add an AGC block)")
    print(f"{'='*60}")
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        shutil.copy2(fixture, workspace / "test.grc")
        session = FlowgraphSession()
        session.load(workspace / "test.grc")
        agent = GrcAgent(session)

        result, trace = _run_turn(agent, provider,
            "Add an AGC block.")
        print("\nTrace:")
        for t in trace:
            print(t)
        print(f"\nAssistant: {result.get('assistant_text', '')[:500]}")
        print(f"Rounds: {result.get('tool_rounds_used')}")


if __name__ == "__main__":
    sys.exit(main())
