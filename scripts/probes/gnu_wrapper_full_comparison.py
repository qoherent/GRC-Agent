"""Comprehensive GNU API wrapper comparison against our parser.

Run with:
    PYTHONPATH=src python3 tests/probes/gnu_wrapper_full_comparison.py

Produces a Markdown report on stdout.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session.gnu_loader import extract_connections_from_file

# Representative corpus covering all domains used in prior evaluations
CORPUS_PATHS: list[Path] = [
    Path("/usr/share/gnuradio/examples/audio/dial_tone.grc"),
    Path("/usr/share/gnuradio/examples/audio/cvsd_sweep.grc"),
    Path("/usr/share/gnuradio/examples/blocks/selector.grc"),
    Path("/usr/share/gnuradio/examples/blocks/stream_mux_demo.grc"),
    Path("/usr/share/gnuradio/examples/blocks/msg_to_var.grc"),
    Path("/usr/share/gnuradio/examples/filter/resampler_demo.grc"),
    Path("/usr/share/gnuradio/examples/dtv/dvbs2_tx.grc"),
    Path("/usr/share/gnuradio/examples/digital/packet/tx_stage0.grc"),
    Path("/usr/share/gnuradio/examples/digital/packet/tx_stage2.grc"),
    Path("/usr/share/gnuradio/examples/zeromq/zeromq_pubsub.grc"),
    Path("/usr/share/gnuradio/examples/fec/polar_code_example.grc"),
    Path("/usr/share/gnuradio/examples/analog/fm_tx.grc"),
    Path("/usr/share/gnuradio/examples/channels/demo_qam.grc"),
    Path("/usr/share/gnuradio/examples/network/test_tcp_sink_client.grc"),
    Path("/usr/share/gnuradio/examples/qt-gui/qtgui_vector_sink_example.grc"),
    Path("/usr/share/gnuradio/examples/metadata/file_metadata_source.grc"),
    Path("/usr/share/gnuradio/examples/pdu/pdu_tools_demo.grc"),
    Path("/usr/share/gnuradio/examples/soapy/soapy_receive.grc"),
    Path("/usr/share/gnuradio/examples/digital/equalizers/linear_equalizer_compare.grc"),
    Path("/usr/share/gnuradio/examples/digital/ofdm/ofdm_loopback.grc"),
]


def _grcc_ok(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["grcc", "-o", "/tmp", str(path)],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def _has_string_ports(conns: list) -> bool:
    for c in conns:
        if isinstance(c.src_port, str) or isinstance(c.dst_port, str):
            return True
    return False


def main() -> None:
    print("# GNU API Wrapper Full Corpus Comparison\n")
    print("| Graph | grcc OK | Our Blocks | GNU Blocks | Our Conns | GNU Conns | Exact Match | String Ports | Notes |")
    print("|---|---|---|---|---|---|---|---|---|")

    total = 0
    exact_matches = 0
    conn_mismatches = 0
    for graph_path in CORPUS_PATHS:
        if not graph_path.exists():
            print(f"| {graph_path.name} | SKIP | — | — | — | — | — | — | File not found |")
            continue

        total += 1
        grcc = _grcc_ok(graph_path)

        # Our parser
        session = FlowgraphSession()
        try:
            session.load(graph_path)
            our_blocks = len(session.flowgraph.blocks) if session.flowgraph else 0
            our_conns = len(session.flowgraph.connections) if session.flowgraph else 0
        except Exception:
            our_blocks = -1
            our_conns = -1

        # GNU loader
        try:
            gnu_conns = extract_connections_from_file(str(graph_path))
            gnu_conn_count = len(gnu_conns)
        except Exception:
            gnu_conns = []
            gnu_conn_count = -1

        # Block count via GNU (expensive, only for subset)
        gnu_block_count = "—"
        exact = "—"
        notes = []

        if our_blocks >= 0:
            exact = "yes" if (our_conns == gnu_conn_count) else "no"
            if our_conns != gnu_conn_count:
                conn_mismatches += 1
                notes.append(f"conn_delta={gnu_conn_count - our_conns}")
            else:
                exact_matches += 1

        if our_blocks >= 0 and gnu_conn_count >= 0:
            our_set = {
                (c.src_block, str(c.src_port), c.dst_block, str(c.dst_port))
                for c in (session.flowgraph.connections if session.flowgraph else [])
            }
            gnu_set = {
                (c.src_block, str(c.src_port), c.dst_block, str(c.dst_port))
                for c in gnu_conns
            }
            if not gnu_set.issubset(our_set):
                notes.append("GNU_conn_not_in_ours")

        if _has_string_ports(gnu_conns):
            notes.append("string_ports")

        if not grcc:
            notes.append("grcc_FAIL")

        print(
            f"| {graph_path.name} | {'yes' if grcc else 'no'} | {our_blocks} | {gnu_block_count} | "
            f"{our_conns} | {gnu_conn_count} | {exact} | {'yes' if _has_string_ports(gnu_conns) else 'no'} | "
            f"{'; '.join(notes) if notes else '—'} |"
        )

    print(f"\n**Summary**: {exact_matches}/{total} exact connection matches. {conn_mismatches} mismatches.")

    # Performance
    print("\n## Performance\n")
    t0 = time.perf_counter()
    extract_connections_from_file(str(CORPUS_PATHS[0]))
    first_time = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(10):
        extract_connections_from_file(str(CORPUS_PATHS[0]))
    cached_time = (time.perf_counter() - t0) / 10.0

    print("| Metric | Time |")
    print("|---|---|")
    print(f"| First graph (includes Platform init) | {first_time * 1000:.1f} ms |")
    print(f"| Cached graph (avg of 10) | {cached_time * 1000:.1f} ms |")


if __name__ == "__main__":
    main()
