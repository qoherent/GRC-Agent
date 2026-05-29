#!/usr/bin/env python3
"""BLE Rate Match — Rational Resampler Insertion.

Stress vectors:
  1. DSP Math (Rate Conversion): 4 MHz → 1 MHz requires decim=4, NOT interp=4
  2. Dtype Discovery: rational_resampler_xxx needs type=ccc for complex
  3. Topology Insertion: must remove old connection and wire new one

Key metric: what does the model put in its CoT reasoning for decim?

Run:
    uv run python tests/llama_eval/ble_resampler_scenario.py
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

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "ble_resampler.grc"
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

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        dst = workspace / "ble_resampler.grc"
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

        [b.instance_name for b in session.flowgraph.blocks]

        prompt = (
            "The signal source is capturing Bluetooth LE at 4 MHz, but the GFSK "
            "demodulator strictly requires a 1 MHz baseband input. Insert a "
            "Rational Resampler between the Low Pass Filter and the GFSK "
            "demodulator to step the sample rate down correctly. The signal is complex."
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
                    reasoning = args.get("reasoning", "")
                    print(f"\n  ROUND {trace_round}: {name}")
                    trace_round += 1
                    if reasoning:
                        print(f"    reasoning: {reasoning[:500]}")
                    for field in ("add_blocks", "remove_blocks", "update_params", "update_states", "remove_connections", "add_connections"):
                        val = args.get(field)
                        if val:
                            print(f"    {field}: {json.dumps(val, sort_keys=True)}")
                    if args.get("force"):
                        print("    force: true")
            elif role == "tool":
                content = turn.get("content", {})
                if isinstance(content, dict):
                    ok = content.get("ok")
                    committed = content.get("committed")
                    forced = content.get("forced_validation_failure")
                    err = content.get("error_type", "")
                    effects = content.get("effects", content.get("effect", ""))
                    msg = str(content.get("message", ""))[:250]
                    hint = content.get("hint", "")
                    if ok and committed:
                        print(f"    -> COMMITTED {'(forced)' if forced else ''} effects={effects}")
                    elif ok is False:
                        print(f"    -> FAIL {err}: {msg}")
                        if hint:
                            print(f"       hint: {hint[:200]}")
                    else:
                        name = turn.get("name", "?")
                        if name in ("inspect_graph", "query_knowledge"):
                            pass

        print(f"\n{SEP}")
        print(f"ASSISTANT: {result.get('assistant_text', '')[:600]}")
        print(f"ROUNDS: {result.get('tool_rounds_used')}")
        print(SEP)

        print(f"\n{SEP}")
        print("POST-TURN STATE")
        print(SEP)
        for b in session.flowgraph.blocks:
            params = b.params.get("parameters", {})
            tp = params.get("type", "")
            dec = params.get("decim", "")
            interp = params.get("interp", "")
            extra = ""
            if b.instance_name == "samp_rate":
                extra = f" value={params.get('value', '')}"
            elif "resampler" in b.instance_name.lower():
                extra = f" type={tp} decim={dec} interp={interp}"
            elif tp:
                extra = f" type={tp}"
            print(f"  {b.instance_name} ({b.block_type}){extra}")
        for c in session.flowgraph.connections:
            print(f"  conn: {c.src_block}:{c.src_port} -> {c.dst_block}:{c.dst_port}")

        # ── Semantic Checks ──
        print(f"\n{SEP}")
        print("SEMANTIC CHECKS")
        print(SEP)
        checks = []
        conn_ids = [f"{c.src_block}:{c.src_port}->{c.dst_block}:{c.dst_port}" for c in session.flowgraph.connections]

        # Check 1: Resampler block added
        has_resampler = any("resampler" in b.instance_name.lower() or "rational_resampler" in b.block_type.lower() for b in session.flowgraph.blocks)
        checks.append(("Resampler block added", has_resampler))

        # Check 2: Old connection removed
        old_conn = "low_pass_filter_0:0->digital_gfsk_demod_0:0"
        checks.append(("Old connection removed", old_conn not in conn_ids))

        # Check 3: New connections exist
        has_lpf_to_resampler = any("low_pass_filter_0" in c and "resampler" in c.lower() for c in conn_ids)
        has_resampler_to_gfsk = any("resampler" in c.lower() and "gfsk" in c for c in conn_ids)
        checks.append(("LPF -> resampler connected", has_lpf_to_resampler))
        checks.append(("Resampler -> GFSK connected", has_resampler_to_gfsk))

        # Check 4: decim=4 (correct DSP math)
        resampler = next((b for b in session.flowgraph.blocks if "resampler" in b.instance_name.lower()), None)
        if resampler:
            params = resampler.params.get("parameters", {})
            dec = params.get("decim", "")
            interp = params.get("interp", "")
            checks.append(("  decim value", dec))
            checks.append(("  interp value", interp))
            checks.append(("decim = 4 (correct 4→1 MHz ratio)", dec in ("4", 4)))
            checks.append(("interp = 1 (no upsampling)", interp in ("1", 1, "", None)))

        # Check 5: Dtype is complex (ccc)
        if resampler:
            tp = resampler.params.get("parameters", {}).get("type", "")
            checks.append(("  type", tp))
            checks.append(("Dtype is complex (ccc)", tp in ("complex", "ccc")))

        # Check 6: No force=true
        force_used = False
        for turn in agent.history[start:]:
            if turn.get("role") == "assistant":
                for tc in turn.get("tool_calls", []):
                    fn = tc.get("function", {})
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try: args = json.loads(args)
                        except: pass
                    if args.get("force") is True:
                        force_used = True
        checks.append(("Force=true NOT used", not force_used))

        passed = total = 0
        for name, val in checks:
            if isinstance(val, str):
                print(f"  {name}: {val}")
            else:
                status = "PASS" if val else "FAIL"
                print(f"  [{status}] {name}")
                total += 1
                if val: passed += 1
        print(f"\n  SCORE: {passed}/{total}")
        print(SEP)


if __name__ == "__main__":
    sys.exit(main())
