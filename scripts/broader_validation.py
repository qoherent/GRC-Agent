"""Broader deterministic validation for auto_insert_block across real graphs.

Runs auto_insert_block deterministically (no live model).
Saves results to /tmp/auto_insert_broader.json.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession

GRAPHS = [
    ("dial_tone",         "/usr/share/gnuradio/examples/audio/dial_tone.grc"),
    ("cvsd_sweep",        "/usr/share/gnuradio/examples/audio/cvsd_sweep.grc"),
    ("noise_power",       "/usr/share/gnuradio/examples/analog/noise_power.grc"),
    ("resampler_demo",    "/usr/share/gnuradio/examples/filter/resampler_demo.grc"),
    ("demo_two_tone",     "/usr/share/gnuradio/examples/channels/demo_two_tone.grc"),
    ("stream_demux",      "/usr/share/gnuradio/examples/blocks/stream_demux_demo.grc"),
    ("mpsk_stage6",       "/usr/share/gnuradio/examples/digital/mpsk_stage6.grc"),
    ("packet_tx_stage0",  "/usr/share/gnuradio/examples/digital/packet/tx_stage0.grc"),
    ("packet_tx_stage2",  "/usr/share/gnuradio/examples/digital/packet/tx_stage2.grc"),
    ("random_bit_gen",    "tests/data/random_bit_generator.grc"),
]

GOALS = [
    ("generic",              "insert compatible block"),
    ("explicit_head",        "insert head block"),
    ("explicit_throttle",    "add throttle"),
    ("explicit_filter",      "add low pass filter"),
    ("unsupported_sink",     "add sink"),
    ("unsupported_source",   "add source"),
]


def graph_meta(path: str) -> dict[str, Any]:
    session = FlowgraphSession()
    ok = session.load(path)
    fg = session.flowgraph
    if fg is None:
        return {"load_ok": ok, "error": "No flowgraph"}
    conns = fg.connections
    stream_conns = [c for c in conns if isinstance(c.src_port, int) and isinstance(c.dst_port, int)]
    msg_conns = [c for c in conns if not (isinstance(c.src_port, int) and isinstance(c.dst_port, int))]
    blocks = fg.blocks
    block_types = [b.block_type for b in blocks]
    return {
        "load_ok": ok,
        "block_count": len(blocks),
        "conn_count": len(conns),
        "stream_conn_count": len(stream_conns),
        "msg_conn_count": len(msg_conns),
        "has_throttle": any("throttle" in bt.lower() for bt in block_types),
        "has_filter": any("filter" in bt.lower() for bt in block_types),
        "variable_count": len([bt for bt in block_types if bt == "variable"]),
        "sink_count": len([bt for bt in block_types if "sink" in bt.lower()]),
        "source_count": len([bt for bt in block_types if "source" in bt.lower()]),
    }


def _classify_root_cause(result: dict[str, Any]) -> str:
    error = result.get("error_type") or ""
    if error == "UNSUPPORTED_GOAL_FOR_AUTO_INSERT":
        return "UNSUPPORTED_GOAL"
    if error == "NO_GRAPH_LOADED":
        return "NO_GRAPH_LOADED"
    attempted = result.get("attempted", [])
    if result.get("ok"):
        return "COMMITTED_OK"
    if attempted and all(not a.get("ok") for a in attempted):
        return "FAMILY_MATCHES_BUT_ALL_GRCC_FAILED"
    if error == "AUTO_INSERT_NO_GOAL_MATCH":
        return "NO_GOAL_FAMILY_MATCH"
    if result.get("mutation_committed") and not result.get("grcc_after"):
        return "STOP_THE_LINE"
    return "UNKNOWN"


def run_case(graph_path: str, goal_label: str, goal_text: str) -> dict[str, Any]:
    agent = GrcAgent()
    lr = agent.execute_tool("load_grc", {"file_path": graph_path})
    if not lr.get("ok"):
        return {
            "load_ok": False,
            "error": lr.get("message"),
            "classification": "BAD_TEST_EXPECTATION",
            "root_cause": "NO_GRAPH_LOADED",
        }

    before_blocks = len(agent.session.flowgraph.blocks) if agent.session.flowgraph else 0
    before_valid = agent.session.validate() if agent.session.flowgraph else False

    result = agent.execute_tool("auto_insert_block", {
        "goal": goal_text,
        "max_candidates": 10,
    })

    after_valid = agent.session.validate() if agent.session.flowgraph else False
    after_blocks = len(agent.session.flowgraph.blocks) if agent.session.flowgraph else 0
    committed = result.get("committed")
    attempted = result.get("attempted", [])
    committed_type = committed["block_type"] if committed else None
    mutation = after_blocks > before_blocks

    classification = "UNKNOWN"
    if result.get("error_type") == "UNSUPPORTED_GOAL_FOR_AUTO_INSERT":
        classification = "PASS_SAFE_REJECTION"
    elif not mutation and not result.get("ok"):
        classification = "PASS_SAFE_REJECTION"
    elif mutation and after_valid:
        if "head" in goal_text and committed_type and "head" not in committed_type.lower():
            classification = "WRONG_SEMANTIC_INSERTION"
        elif "throttle" in goal_text and committed_type and "throttle" not in committed_type.lower():
            classification = "WRONG_SEMANTIC_INSERTION"
        elif "filter" in goal_text and committed_type and "filter" not in committed_type.lower():
            classification = "WRONG_SEMANTIC_INSERTION"
        else:
            classification = "PASS_COMMITTED"
    elif mutation and not after_valid:
        classification = "STOP_THE_LINE"
    else:
        classification = "PASS_SAFE_REJECTION"

    rc = _classify_root_cause({
        "error_type": result.get("error_type"),
        "attempted": attempted,
        "mutation_committed": mutation,
        "grcc_after": after_valid,
    })

    return {
        "ok": result.get("ok"),
        "commit_block_type": committed_type,
        "attempt_count": result.get("attempt_count", 0),
        "error_type": result.get("error_type"),
        "classification": classification,
        "root_cause": rc,
        "mutation_committed": mutation,
        "mutation_committed_and_invalid": mutation and not after_valid,
        "grcc_before": before_valid,
        "grcc_after": after_valid,
        "goal_fit_correct": classification in ("PASS_COMMITTED", "PASS_SAFE_REJECTION"),
        "attempted_block_types": [a["block_type"] for a in attempted],
        "goal_label": goal_label,
        "goal_text": goal_text,
    }


def main() -> None:
    out: list[dict[str, Any]] = []
    for name, path in GRAPHS:
        meta = graph_meta(path)
        print(f"--- {name}: {meta}")
        for goal_label, goal_text in GOALS:
            case = run_case(path, goal_label, goal_text)
            case["graph"] = name
            case["path"] = path
            case.update(meta)
            out.append(case)
            print(f"  {goal_label:20s} -> {case['classification']:25s} rc={case['root_cause']:30s} commit={case['commit_block_type']} attempted={case['attempt_count']} mutation={case['mutation_committed']}")

    with open("/tmp/auto_insert_broader.json", "w") as f:
        json.dump(out, f, indent=2)
    print("\nWrote /tmp/auto_insert_broader.json")


if __name__ == "__main__":
    main()
