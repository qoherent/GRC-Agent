"""API probe script to compare GNU Radio official GRC API with our custom code.

Run from project root with:
    GRC_BLOCKS_PATH=/usr/share/gnuradio/grc/blocks python3 tests/probes/grc_api_probe.py

This is a research probe only — not part of production.
"""

from __future__ import annotations

import os
import sys
import yaml
from pathlib import Path

# Ensure GNU Radio can find its blocks
os.environ.setdefault("GRC_BLOCKS_PATH", "/usr/share/gnuradio/grc/blocks")


from gnuradio.grc.core.platform import Platform

# Adjust sys.path to import our own modules for comparison
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.catalog.describe import describe_block

# Test graphs
TEST_GRAPHS = [
    "/usr/share/gnuradio/examples/audio/dial_tone.grc",
    "/usr/share/gnuradio/examples/digital/packet/tx_stage0.grc",
    "/usr/share/gnuradio/examples/fec/polar_code_example.grc",
]


def main() -> None:
    print("=" * 60)
    print("Phase 1: Environment")
    print("=" * 60)
    print("GNU Radio version:", "3.10.9.2")
    print("Python version:", sys.version.split()[0])
    print("grcc path:", os.popen("which grcc").read().strip())

    print("\n" + "=" * 60)
    print("Phase 2: Platform construction")
    print("=" * 60)
    p = Platform(version="3.10.9.2", version_parts=["3", "10", "9", "2"])
    p.build_library()
    print("Block classes loaded:", len(p.block_classes))
    print("Sample keys:", list(p.block_classes.keys())[:5])

    print("\n" + "=" * 60)
    print("Phase 3: Per-graph comparison")
    print("=" * 60)

    for graph_path in TEST_GRAPHS:
        print(f"\n--- Graph: {graph_path} ---")
        if not Path(graph_path).exists():
            print("  FILE NOT FOUND, skipping")
            continue

        # Our loader
        our_session = FlowgraphSession()
        our_session.load(graph_path)
        our_blocks = our_session.flowgraph.blocks if our_session.flowgraph else []
        our_connections = our_session.flowgraph.connections if our_session.flowgraph else []
        our_msg_ports = sum(
            1 for b in our_blocks
            for p in (b.params.get("inputs", []) + b.params.get("outputs", []))
            if isinstance(p, dict) and p.get("domain") == "message"
        )

        # GNU loader
        try:
            with open(graph_path, "r", encoding="utf-8") as fp:
                raw = yaml.safe_load(fp)
            gnu_fg = p.make_flow_graph()
            gnu_fg.import_data(raw)
            gnu_blocks = list(gnu_fg.iter_enabled_blocks())
            gnu_connections = list(gnu_fg.get_enabled_connections())
            gnu_msg_count = sum(
                1 for b in gnu_blocks
                for s in (list(b.sinks) + list(b.sources))
                if s.domain == "message"
            )

            gnu_fg.export_data()
            print(f"  Our blocks:    {len(our_blocks)}")
            print(f"  GNU blocks:    {len(gnu_blocks)}")
            print(f"  Our conns:     {len(our_connections)}")
            print(f"  GNU conns:     {len(gnu_connections)}")
            print(f"  Our msg ports: {our_msg_ports}")
            print(f"  GNU msg ports: {gnu_msg_count}")
            print(f"  Validate():    {len(list(gnu_fg.iter_error_messages()))} errors")

            # Compare block names
            our_names = {b.instance_name for b in our_blocks}
            gnu_names = {b.name for b in gnu_blocks}
            print(f"  Name match:    {our_names == gnu_names} (our={len(our_names)} gnu={len(gnu_names)})")
        except Exception as exc:
            print(f"  GNU load FAILED: {type(exc).__name__}: {exc}")

    print("\n" + "=" * 60)
    print("Phase 4: Catalog comparison (one block)")
    print("=" * 60)
    block_id = "blocks_throttle2"

    # Our catalog
    our_payload = describe_block(block_id)
    print("Our describe ok:", our_payload.get("ok"))
    if our_payload.get("ok"):
        our_params = our_payload.get("parameters", [])
        our_inputs = our_payload.get("inputs", [])
        our_outputs = our_payload.get("outputs", [])
        print(f"  Our params:  {len(our_params)}")
        print(f"  Our inputs:  {len(our_inputs)}")
        print(f"  Our outputs: {len(our_outputs)}")

    # GNU catalog
    gnu_cls = p.block_classes.get(block_id)
    if gnu_cls:
        print("GNU block_class found: yes")
        print(f"  GNU params_data: {len(gnu_cls.parameters_data)}")
        print(f"  GNU inputs_data: {len(gnu_cls.inputs_data)}")
        print(f"  GNU outputs_data: {len(gnu_cls.outputs_data)}")
        print(f"  GNU asserts:     {gnu_cls.asserts}")
    else:
        print("GNU block_class found: no")

    print("\n" + "=" * 60)
    print("Phase 5: Save roundtrip comparison")
    print("=" * 60)
    graph_path = TEST_GRAPHS[0]
    with open(graph_path, "r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp)
    gnu_fg = p.make_flow_graph()
    gnu_fg.import_data(raw)

    tmp_path = "/tmp/probe_save_roundtrip.grc"
    p.save_flow_graph(tmp_path, gnu_fg)
    print(f"GNU save to {tmp_path}: size={os.path.getsize(tmp_path)}")

    with open(tmp_path, "r", encoding="utf-8") as fp:
        reloaded = yaml.safe_load(fp)
    print(f"Reloaded blocks: {len(reloaded.get('blocks', []))}")
    print(f"Reloaded conns:  {len(reloaded.get('connections', []))}")

    print("\n" + "=" * 60)
    print("Probe complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
