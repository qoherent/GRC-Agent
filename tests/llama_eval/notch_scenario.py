#!/usr/bin/env python3
"""CW Interference Notch — Advanced DSP Scenario.

Stress vectors:
  1. Semantic Search & DSP Translation: query_knowledge(catalog, "band reject filter")
  2. Dtype Trap: complex signal path — must use band_reject_filter_cc or type=complex
  3. Port Occupancy: must remove_connections for existing add:0 -> sink:0 edge
  4. CoT Schema: reasoning field must explain parameter math (45000 / 55000 Hz)

Run:
    uv run python tests/llama_eval/notch_scenario.py
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

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "notch_test.grc"
SEP = "=" * 70


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


def main():
    provider = _make_provider()
    print(f"Provider: {provider.base_url} model={provider.model}")
    print(f"System Prompt: {len(build_system_prompt())} chars")

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        dst = workspace / "notch_test.grc"
        shutil.copy2(FIXTURE, dst)

        session = FlowgraphSession()
        session.load(dst)
        agent = GrcAgent(session)

        print(f"\n{SEP}")
        print("PRE-TURN STATE")
        print(SEP)
        for b in session.flowgraph.blocks:
            vt = b.params.get("parameters", {}).get("value", "")
            tp = b.params.get("parameters", {}).get("type", "")
            extra = f" value={vt}" if vt else (f" type={tp}" if tp else "")
            print(f"  {b.instance_name} ({b.block_type}){extra}")
        for c in session.flowgraph.connections:
            print(f"  conn: {c.src_block}:{c.src_port} -> {c.dst_block}:{c.dst_port}")

        prompt = (
            "We have a strong continuous wave (CW) interferer centered at 50 kHz. "
            "Insert a band reject filter right before the frequency sink to notch "
            "out the interference from 45 kHz to 55 kHz. The signal path is complex."
        )

        print(f"\n{SEP}")
        print(f"PROMPT: {prompt}")
        print(SEP)

        from grc_agent.toolagents_runtime import run_bounded_toolagents_turn
        start = len(agent.history)
        result = run_bounded_toolagents_turn(
            agent=agent, user_message=prompt, client=provider,
            model=provider.model, mvp_tool_profile=True,
        )

        print(f"\n{SEP}")
        print("AGENT TRACE")
        print(SEP)
        trace_round = 0
        for turn in agent.history[start:]:
            role = turn.get("role")
            if role == "user":
                continue
            if role == "assistant":
                for tc in turn.get("tool_calls", []):
                    fn = tc.get("function", {})
                    name = fn.get("name", "?")
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            args = {"raw": args[:200]}
                    # Show reasoning if present
                    reasoning = args.get("reasoning", "")
                    if reasoning:
                        print(f"\n  ROUND {trace_round}: {name}")
                        print(f"    reasoning: {reasoning[:300]}")
                    else:
                        print(f"\n  ROUND {trace_round}: {name}")
                    # Show key args (hide verbose fields)
                    compact = {}
                    for k, v in args.items():
                        if k in ("reasoning",):
                            continue
                        if isinstance(v, list):
                            compact[k] = f"[{len(v)} items]"
                        elif isinstance(v, dict) and k in ("add_blocks", "remove_blocks", "update_params", "update_states", "add_connections", "remove_connections"):
                            continue  # shown as count
                        else:
                            compact[k] = v
                    if compact:
                        print(f"    args: {json.dumps(compact, sort_keys=True)}")
                    # Show edit arrays compactly
                    for field in ("add_blocks", "remove_blocks", "update_params", "update_states", "remove_connections", "add_connections"):
                        val = args.get(field)
                        if val:
                            print(f"    {field}: {json.dumps(val, sort_keys=True)}")
                    trace_round += 1
            elif role == "tool":
                content = turn.get("content", {})
                if isinstance(content, dict):
                    ok = content.get("ok")
                    committed = content.get("committed")
                    forced = content.get("forced_validation_failure")
                    err = content.get("error_type", "")
                    effects = content.get("effects", content.get("effect", ""))
                    msg = str(content.get("message", ""))[:250]
                    if ok and committed:
                        print(f"    -> COMMITTED {'(forced)' if forced else ''} effects={effects}")
                    elif ok is False:
                        hint = content.get("hint", "")
                        print(f"    -> FAIL {err}: {msg}")
                        if hint:
                            print(f"       hint: {hint[:200]}")
                    else:
                        name = turn.get("name", "?")
                        if name in ("inspect_graph", "query_knowledge"):
                            pass  # quiet read-only
                        else:
                            print(f"    -> tool result: {msg[:150]}")

        print(f"\n{SEP}")
        print(f"ASSISTANT: {result.get('assistant_text', '')[:600]}")
        print(f"ROUNDS: {result.get('tool_rounds_used')}")
        print(f"TOOL CALLS: {result.get('tool_calls_executed')}")
        print(SEP)

        print(f"\n{SEP}")
        print("POST-TURN STATE")
        print(SEP)
        for b in session.flowgraph.blocks:
            vt = b.params.get("parameters", {}).get("value", "")
            tp = b.params.get("parameters", {}).get("type", "")
            st = (b.params.get("states") or {}).get("state", "enabled")
            extra = f" value={vt}" if vt else (f" type={tp}" if tp else "")
            print(f"  {b.instance_name} ({b.block_type}) state={st}{extra}")
        for c in session.flowgraph.connections:
            print(f"  conn: {c.src_block}:{c.src_port} -> {c.dst_block}:{c.dst_port}")

        # Semantic checks
        print(f"\n{SEP}")
        print("SEMANTIC CHECKS")
        print(SEP)
        block_names = [b.instance_name for b in session.flowgraph.blocks]
        conn_ids = [f"{c.src_block}:{c.src_port}->{c.dst_block}:{c.dst_port}" for c in session.flowgraph.connections]

        checks = []
        # Check 1: Band reject filter block exists
        has_notch = any("reject" in name.lower() or "notch" in name.lower() or "band_reject" in name.lower() for name in block_names)
        checks.append(("Band reject filter added", has_notch))

        # Check 2: Original connection removed
        old_conn = "blocks_add_xx_0:0->qtgui_freq_sink_x_0:0"
        checks.append(("Old connection removed", old_conn not in conn_ids))

        # Check 3: New connections exist (use notch/filter, not reject)
        has_add_to_filter = any("add_xx_0" in c and ("notch" in c.lower() or "filter" in c.lower() or "reject" in c.lower()) for c in conn_ids)
        has_filter_to_sink = any(("notch" in c.lower() or "filter" in c.lower() or "reject" in c.lower()) and "sink" in c for c in conn_ids)
        checks.append(("add -> filter connected", has_add_to_filter))
        checks.append(("filter -> sink connected", has_filter_to_sink))

        # Check 4: Filter has correct cutoff params
        filter_cutoffs_ok = False
        for b in session.flowgraph.blocks:
            if "reject" in b.instance_name.lower() or "notch" in b.instance_name.lower():
                params = b.params.get("parameters", {})
                low = params.get("low_cutoff_freq") or params.get("lower_cutoff_freq") or params.get("low_cutoff")
                high = params.get("high_cutoff_freq") or params.get("upper_cutoff_freq") or params.get("high_cutoff")
                if low and high:
                    try:
                        filter_cutoffs_ok = float(low) <= 48000 and float(high) >= 52000
                    except (ValueError, TypeError):
                        pass
                checks.append(("  low_cutoff", low))
                checks.append(("  high_cutoff", high))

        checks.append(("Filter cutoff params correct", filter_cutoffs_ok))

        # Check 5: Dtype is complex
        dtype_correct = True
        for b in session.flowgraph.blocks:
            if "reject" in b.instance_name.lower():
                tp = b.params.get("parameters", {}).get("type", "")
                dtype_correct = tp in ("complex", "cc")
                checks.append(("  filter dtype", tp))
        checks.append(("Filter dtype is complex", dtype_correct))

        # Check 6: No force=true abuse
        force_used = False
        for turn in agent.history[start:]:
            if turn.get("role") == "assistant":
                for tc in turn.get("tool_calls", []):
                    fn = tc.get("function", {})
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    if args.get("force") is True:
                        force_used = True
        checks.append(("Force=true NOT used (clean insert)", not force_used))

        passed = 0
        total = 0
        for name, result_check in checks:
            status = "PASS" if result_check else "FAIL"
            if isinstance(result_check, str):
                print(f"  {name}: {result_check}")
            else:
                print(f"  [{status}] {name}")
                total += 1
                if result_check:
                    passed += 1

        print(f"\n  SCORE: {passed}/{total}")
        print(SEP)


if __name__ == "__main__":
    sys.exit(main())
