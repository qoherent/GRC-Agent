#!/usr/bin/env python3
"""16-QAM Link Upgrade — Digital Modulation Scenario.

Stress vectors:
  1. Cross-Block Dependency (Math): 16-QAM = 16 symbols = 2^4 → max must be 16
  2. Object Referencing: constellation is a variable block referenced by string ID
  3. No-Action Edge Integrity: parameter-only edit, no connections should change

Run:
    uv run python tests/llama_eval/qam16_scenario.py
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

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "16qam_upgrade.grc"
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
        dst = workspace / "16qam_upgrade.grc"
        shutil.copy2(FIXTURE, dst)

        session = FlowgraphSession()
        session.load(dst)
        agent = GrcAgent(session)

        print(f"\n{SEP}")
        print("PRE-TURN STATE")
        print(SEP)
        for b in session.flowgraph.blocks:
            params = b.params.get("parameters", {})
            mx = params.get("max", "")
            tp = params.get("type", "")
            con = params.get("constellation", "")
            vt = params.get("value", "")
            extra = ""
            if b.instance_name == "qpsk_obj":
                extra = f" type={tp}"
            elif b.instance_name == "analog_random_source_x_0":
                extra = f" min=0 max={mx}"
            elif b.instance_name == "digital_constellation_modulator_0":
                extra = f" constellation={con}"
            elif vt:
                extra = f" value={vt}"
            print(f"  {b.instance_name} ({b.block_type}){extra}")
        for c in session.flowgraph.connections:
            print(f"  conn: {c.src_block}:{c.src_port} -> {c.dst_block}:{c.dst_port}")

        prompt = (
            "We are upgrading our digital link to support a higher data rate. "
            "Upgrade the modulation scheme from QPSK to 16-QAM. Make sure to "
            "update the random data source so it generates the correct range "
            "of byte values for a 16-QAM alphabet."
        )

        print(f"\n{SEP}")
        print(f"PROMPT: {prompt}")
        print(SEP)

        pre_names = [b.instance_name for b in session.flowgraph.blocks]
        [f"{c.src_block}:{c.src_port}->{c.dst_block}:{c.dst_port}" for c in session.flowgraph.connections]

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
                    if reasoning:
                        print(f"\n  ROUND {trace_round}: {name}")
                        print(f"    reasoning: {reasoning[:400]}")
                    else:
                        print(f"\n  ROUND {trace_round}: {name}")
                    compact = {k: v for k, v in args.items() if k != "reasoning" and not isinstance(v, (list, dict))}
                    if compact:
                        print(f"    args: {json.dumps(compact, sort_keys=True)}")
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
                    err = content.get("error_type", "")
                    effects = content.get("effects", content.get("effect", ""))
                    msg = str(content.get("message", ""))[:250]
                    hint = content.get("hint", "")
                    if ok and committed:
                        print(f"    -> COMMITTED effects={effects}")
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
            mx = params.get("max", "")
            tp = params.get("type", "")
            con = params.get("constellation", "")
            vt = params.get("value", "")
            extra = ""
            if b.instance_name == "qpsk_obj":
                extra = f" type={tp}"
            elif b.instance_name == "analog_random_source_x_0":
                extra = f" min=0 max={mx}"
            elif b.instance_name == "digital_constellation_modulator_0":
                extra = f" constellation={con}"
            elif vt:
                extra = f" value={vt}"
            print(f"  {b.instance_name} ({b.block_type}){extra}")
        for c in session.flowgraph.connections:
            print(f"  conn: {c.src_block}:{c.src_port} -> {c.dst_block}:{c.dst_port}")

        # ── Semantic Checks ──
        print(f"\n{SEP}")
        print("SEMANTIC CHECKS")
        print(SEP)
        checks = []
        block_names = [b.instance_name for b in session.flowgraph.blocks]
        conn_ids = [f"{c.src_block}:{c.src_port}->{c.dst_block}:{c.dst_port}" for c in session.flowgraph.connections]

        # Check 1: No blocks added or removed (parameter-only edit)
        checks.append(("Blocks unchanged (no add/remove)", block_names == pre_names))

        # Check 2: Connections unchanged
        expected_conns = [
            "analog_random_source_x_0:0->digital_constellation_modulator_0:0",
            "digital_constellation_modulator_0:0->qtgui_const_sink_x_0:0",
        ]
        checks.append(("Connections unchanged", conn_ids == expected_conns))

        # Check 3: Constellation updated to 16-QAM equivalent
        qpsk_block = next((b for b in session.flowgraph.blocks if b.instance_name == "qpsk_obj"), None)
        if qpsk_block:
            params = qpsk_block.params.get("parameters", {})
            tp = params.get("type", "")
            checks.append(("Constellation type changed from qpsk", tp != "qpsk"))
            checks.append(("  constellation type value", tp))

        # Check 4: Random source max updated to 16
        src_block = next((b for b in session.flowgraph.blocks if b.instance_name == "analog_random_source_x_0"), None)
        if src_block:
            mx = src_block.params.get("parameters", {}).get("max", "")
            checks.append(("Random source max = 16", mx in ("16", 16)))
            checks.append(("  max value", mx))

        # Check 5: No force=true used for a simple param edit
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
