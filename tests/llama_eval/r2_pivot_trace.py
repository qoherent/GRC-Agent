#!/usr/bin/env python3
"""Extract detailed tool trace from R2 pivot scenario.

Run:
    uv run python tests/llama_eval/r2_pivot_trace.py
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

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "random_bit_generator.grc"


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
        agent=agent,
        user_message=prompt,
        client=provider,
        model=provider.model,
        mvp_tool_profile=True,
    )
    trace = []
    for turn in agent.history[start:]:
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
                trace.append(f"  [{name}] args={json.dumps(args, sort_keys=True)}")
        elif role == "tool":
            content = turn.get("content", {})
            if isinstance(content, dict):
                ok = content.get("ok")
                committed = content.get("committed")
                force = content.get("forced_validation_failure")
                err = content.get("error_type", "")
                effects = str(content.get("effects", content.get("effect", "")))[:200]
                msg = str(content.get("message", ""))[:200]
                status = "COMMITTED" if committed else ("FAIL" if ok is False else "OK")
                trace.append(f"  [{turn.get('name', '?')}] -> {status} force={force} {err} effects={effects} msg={msg}")
    return result, trace


def main():
    provider = _make_provider()
    print(f"\nProvider: {provider.base_url} model={provider.model}")

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        dst = workspace / "test.grc"
        shutil.copy2(FIXTURE, dst)

        session = FlowgraphSession()
        session.load(dst)
        agent = GrcAgent(session)

        print(f"\n=== System Prompt ({len(build_system_prompt())} chars) ===\n  {build_system_prompt()}")
        print(f"\nPre-turn: {[(b.instance_name, b.block_type) for b in session.flowgraph.blocks]}")

        # Turn 1: Change samp_rate to 48000
        print(f"\n{'='*60}")
        print("TURN 1: Change samp_rate to 48000")
        print(f"{'='*60}")
        result1, trace1 = _run_turn(agent, provider,
            "Change the sample rate to 48000.")
        print(f"  text: {result1.get('assistant_text', '')[:300]}")
        print(f"  rounds: {result1.get('tool_rounds_used')}")
        for t in trace1:
            print(t)

        # Turn 2: Change samp_rate to 96000 AND disable throttle
        print(f"\n{'='*60}")
        print("TURN 2: Change samp_rate to 96000 and disable throttle")
        print(f"{'='*60}")
        result2, trace2 = _run_turn(agent, provider,
            "Now change the sample rate to 96000 and disable the blocks_throttle2_0 block.")
        print(f"  text: {result2.get('assistant_text', '')[:400]}")
        print(f"  rounds: {result2.get('tool_rounds_used')}")
        for t in trace2:
            print(t)

        print("\n=== Final state ===")
        print(f"  variables: {session.get_variable_values()}")
        for b in session.flowgraph.blocks:
            state = b.params.get("states", {}).get("state", "enabled")
            print(f"  {b.instance_name}: state={state}")


if __name__ == "__main__":
    sys.exit(main())
