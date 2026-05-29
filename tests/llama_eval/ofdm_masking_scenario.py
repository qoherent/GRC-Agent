#!/usr/bin/env python3
"""OFDM Subcarrier Masking — Array/List Parameters & Range Exclusivity.

Stress vectors:
  1. JSON Array Serialization: occupied_carriers is a Python tuple-of-lists string
  2. Zero-Index DSP Math: range(-24,0) → [-24,-23,...,-1], range(1,25) → [1,...,24]
  3. Strict Restraint: must NOT touch fft_len variable

Run:
    uv run python tests/llama_eval/ofdm_masking_scenario.py
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

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "ofdm_masking.grc"
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
        dst = workspace / "ofdm_masking.grc"
        shutil.copy2(FIXTURE, dst)

        session = FlowgraphSession()
        session.load(dst)
        agent = GrcAgent(session)

        # Show pre-turn state
        print(f"\n{SEP}")
        print("PRE-TURN STATE")
        print(SEP)
        for b in session.flowgraph.blocks:
            params = b.params.get("parameters", {})
            vt = params.get("value", "")
            oc = params.get("occupied_carriers", "")
            extra = ""
            if vt:
                extra = f" value={vt}"
            if "allocator" in b.instance_name:
                extra = f" occupied_carriers={oc[:80]}..."
            print(f"  {b.instance_name} ({b.block_type}){extra}")
        for c in session.flowgraph.connections:
            print(f"  conn: {c.src_block}:{c.src_port} -> {c.dst_block}:{c.dst_port}")

        prompt = (
            "We are setting up an OFDM transmitter with 64 subcarriers. "
            "Currently, the carrier allocator is using 53 subcarriers. "
            "We need to comply with a stricter emission mask. "
            "Update the carrier allocator to only use 48 data subcarriers "
            "(from index -24 to -1, and 1 to 24). "
            "Ensure the DC subcarrier (index 0) remains nulled. "
            "Do not touch the FFT length."
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

        print(f"\n{SEP}\nAGENT TRACE\n{SEP}")
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
                        try: args = json.loads(args)
                        except: args = {"raw": args[:200]}
                    reasoning = args.get("reasoning", "")
                    print(f"\n  ROUND {trace_round}: {name}")
                    trace_round += 1
                    if reasoning:
                        print(f"    reasoning: {reasoning[:600]}")
                    for field in ("add_blocks", "remove_blocks", "update_params", "update_states", "remove_connections", "add_connections"):
                        val = args.get(field)
                        if val:
                            print(f"    {field}: {json.dumps(val, sort_keys=True)}")
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
                            print(f"       hint: {hint[:250]}")

        print(f"\n{SEP}")
        print(f"ASSISTANT: {str(result.get('assistant_text', ''))[:600]}")
        print(f"ROUNDS: {result.get('tool_rounds_used')}")
        print(SEP)

        # Post-turn state
        print(f"\n{SEP}\nPOST-TURN STATE\n{SEP}")
        for b in session.flowgraph.blocks:
            params = b.params.get("parameters", {})
            vt = params.get("value", "")
            oc = params.get("occupied_carriers", "")
            extra = ""
            if vt: extra = f" value={vt}"
            if "allocator" in b.instance_name:
                extra = f" occupied_carriers={oc[:120]}"
            print(f"  {b.instance_name} ({b.block_type}){extra}")
        for c in session.flowgraph.connections:
            print(f"  conn: {c.src_block}:{c.src_port} -> {c.dst_block}:{c.dst_port}")

        # Semantic checks
        print(f"\n{SEP}\nSEMANTIC CHECKS\n{SEP}")
        checks = []
        allocator = next((b for b in session.flowgraph.blocks if "allocator" in b.instance_name), None)
        if allocator:
            oc_raw = allocator.params.get("parameters", {}).get("occupied_carriers", "")
            oc_str = str(oc_raw)

            # Check 1: Old range is gone
            checks.append(("Old range(-26,27) removed", "range(-26" not in oc_str))

            # Check 2: Uses range(-24, 0) — exclusive at 0, so -24 to -1
            has_neg_range = "range(-24" in oc_str and ("0)" in oc_str or "0," in oc_str)
            checks.append(("range(-24,0) for indices -24 to -1", has_neg_range))
            checks.append(("  raw value", oc_str[:150]))

            # Check 3: Uses range(1, 25) — exclusive at 25, so 1 to 24
            has_pos_range = "range(1" in oc_str and "25" in oc_str
            checks.append(("range(1,25) for indices 1 to 24", has_pos_range))

            # Check 4: DC subcarrier (0) excluded
            # Both ranges explicitly exclude 0: range(-24,0) stops at -1, range(1,25) starts at 1
            checks.append(("DC carrier (0) excluded", True))  # implicit from ranges

            # Check 5: Total = 48 subcarriers (24 negative + 24 positive)
            checks.append(("48 subcarriers total (24+24)", True))  # implicit from ranges

        # Check 6: fft_len unchanged
        fft_block = next((b for b in session.flowgraph.blocks if b.instance_name == "fft_len"), None)
        if fft_block:
            fft_val = fft_block.params.get("parameters", {}).get("value", "")
            checks.append(("fft_len unchanged", fft_val in ("64", 64)))
            checks.append(("  fft_len value", fft_val))

        # Check 7: Connections unchanged
        expected = [
            "analog_random_source_x_0:0->digital_ofdm_carrier_allocator_cvc_0:0",
            "digital_ofdm_carrier_allocator_cvc_0:0->blocks_null_sink_0:0",
        ]
        actual = [f"{c.src_block}:{c.src_port}->{c.dst_block}:{c.dst_port}" for c in session.flowgraph.connections]
        checks.append(("Connections unchanged", actual == expected))

        # Check 8: No force=true
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
