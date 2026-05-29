#!/usr/bin/env python3
"""MAC-Layer Sniffer — Asynchronous Message Port Routing.

Stress vectors:
  1. String Port IDs: must use "pdus" and "print_pdu" (strings, not integers)
  2. Message Port Discovery: query_knowledge(catalog) to find port names
  3. Cross-Domain Avoidance: async message ports vs stream ports

Run:
    uv run python tests/llama_eval/mac_sniffer_scenario.py
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

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "mac_sniffer.grc"
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
        dst = workspace / "mac_sniffer.grc"
        shutil.copy2(FIXTURE, dst)

        session = FlowgraphSession()
        session.load(dst)
        agent = GrcAgent(session)

        print(f"\n{SEP}")
        print("PRE-TURN STATE")
        print(SEP)
        for b in session.flowgraph.blocks:
            print(f"  {b.instance_name} ({b.block_type})")
        conns = [(c.src_block, c.src_port, c.dst_block, c.dst_port) for c in session.flowgraph.connections]
        if conns:
            for c in conns:
                print(f"  conn: {c[0]}:{c[1]} -> {c[2]}:{c[3]}")
        else:
            print("  (no connections — graph is disconnected)")

        prompt = (
            "We are building a MAC-layer packet testing rig. "
            "Add a 'Message Debug' block to act as our packet sniffer. "
            "Connect the output of the Random PDU generator to the PDU print "
            "port of the Message Debug block. Do not connect the Message Strobe. "
            "Note: These are asynchronous message ports, not standard stream ports."
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
                        print(f"    reasoning: {reasoning[:500]}")

                    # Show ports specifically — highlight string vs int
                    for field in ("add_blocks", "add_connections", "remove_connections"):
                        val = args.get(field)
                        if val and field == "add_connections":
                            print(f"    {field}:")
                            for conn in val:
                                src_port = conn.get("src", {}).get("port")
                                dst_port = conn.get("dst", {}).get("port")
                                print(f"      src=({conn.get('src',{}).get('block')}, port={src_port!r})")
                                print(f"      dst=({conn.get('dst',{}).get('block')}, port={dst_port!r})")
                        elif val and field == "add_blocks":
                            print(f"    {field}: {json.dumps(val, sort_keys=True)}")
                        elif val:
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

        print(f"\n{SEP}\nPOST-TURN STATE\n{SEP}")
        for b in session.flowgraph.blocks:
            print(f"  {b.instance_name} ({b.block_type})")
        conns = [(c.src_block, c.src_port, c.dst_block, c.dst_port) for c in session.flowgraph.connections]
        if conns:
            for c in conns:
                print(f"  conn: {c[0]}:{c[1]!r} -> {c[2]}:{c[3]!r}")
        else:
            print("  (no connections)")

        # Semantic checks
        print(f"\n{SEP}\nSEMANTIC CHECKS\n{SEP}")
        checks = []
        conns = [(c.src_block, c.src_port, c.dst_block, c.dst_port) for c in session.flowgraph.connections]

        # Check 1: Message Debug block added
        has_debug = any("debug" in b.instance_name.lower() or "message_debug" in b.block_type for b in session.flowgraph.blocks)
        checks.append(("Message Debug block added", has_debug))

        # Check 2: Connection uses STRING ports (not int 0)
        pdu_conn = None
        for c in conns:
            if "random_pdu" in c[0]:
                pdu_conn = c
                break
        if pdu_conn:
            checks.append(("src port is string", isinstance(pdu_conn[1], str)))
            checks.append(("  src port value", pdu_conn[1]))
            checks.append(("dst port is string", isinstance(pdu_conn[3], str)))
            checks.append(("  dst port value", pdu_conn[3]))
            checks.append(("src port = pdus", pdu_conn[1] == "pdus"))
        else:
            checks.append(("PDU connection exists", False))

        # Check 3: Message Strobe is NOT connected
        strobe_conn = any("strobe" in c[0] for c in conns)
        checks.append(("Message Strobe NOT connected", not strobe_conn))

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
