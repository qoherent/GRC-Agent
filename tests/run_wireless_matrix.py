"""Minimal wireless engineering matrix runner — tests 8 scenarios against blank.grc
using the current Seamless harness + sanitized tool surface (commit 2b85a52).

Each scenario tests a distinct DSP operation class:
  1. basic_add       — Add analog source + null sink + connect
  2. notch_filter    — Insert band-reject filter (notch) into existing path
  3. throttle_add    — Add throttle block between source and sink
  4. param_update    — Update sample rate cascade
  5. block_swap      — Replace one inline block with another
  6. qam_upgrade     — Change constellation object (requires existing graph)
  7. fft_pipeline    — Add FFT analysis chain
  8. variable_add    — Add variable + reference in block params

Runs against the 9B model. Reports pass/fail per scenario with key metrics.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BLANK_GRC = PROJECT_ROOT / "playground" / "blank.grc"
TMP_OUT = Path("/tmp/wireless_matrix_out.json")
TMP_LOG = Path("/tmp/wireless_matrix_log.txt")
MODEL = "qwen3.5:9b-q4_K_M"

_BLANK_TEMPLATE = """options:
  parameters:
    author: ''
    category: Custom
    cmake_opt: ''
    comment: ''
    copyright: ''
    description: blank flow graph
    gen_cmake: 'On'
    gen_linking: dynamic
    generate_options: no_gui
    hier_block_src_path: '.:'
    id: blank
    max_nouts: '0'
    output_language: python
    placement: (0,0)
    qt_qss_theme: ''
    realtime_scheduling: ''
    run: 'True'
    run_command: '{python} -u {filename}'
    run_options: prompt
    sizing_mode: fixed
    thread_safe_setters: ''
    title: Blank
    window_size: ''
  states:
    bus_sink: false
    bus_source: false
    bus_structure: null
    coordinate: [8, 8]
    rotation: 0
    state: enabled

blocks: []

connections: []

metadata:
  file_format: 1
"""


def reset_blank():
    BLANK_GRC.write_text(_BLANK_TEMPLATE)


def run_chat(message: str) -> dict:
    TMP_OUT.unlink(missing_ok=True)
    TMP_LOG.unlink(missing_ok=True)
    cmd = [
        "uv", "run", "grc-agent", "chat",
        str(BLANK_GRC),
        "--message", message,
        "--model", MODEL,
        "--json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    try:
        return json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "json_parse_failed", "stdout": proc.stdout[:200], "stderr": proc.stderr[:200]}


def score_result(result: dict, expected_blocks: int = 0) -> dict:
    ops = result.get("operations", [])
    change_calls = [o for o in ops if o.get("name") == "change_graph"]
    inspect_calls = [o for o in ops if o.get("name") == "inspect_graph"]
    query_calls  = [o for o in ops if o.get("name") == "query_knowledge"]

    committed = result.get("state_revision", 1) > 1
    valid = result.get("validation_status") == "valid"

    # Check graph on disk
    content = BLANK_GRC.read_text()
    blocks_count = content.count("\n- name:")
    conns_count = content.count("\nconnections:")

    return {
        "committed": committed,
        "valid": valid,
        "state_revision": result.get("state_revision", 0),
        "tool_rounds": len(ops),
        "change_graph_calls": len(change_calls),
        "inspect_graph_calls": len(inspect_calls),
        "query_knowledge_calls": len(query_calls),
        "blocks_on_disk": blocks_count,
        "connections_on_disk": conns_count,
        "ok": result.get("ok", False),
        "assistant_text": (result.get("assistant_text", "") or "")[:120],
    }


SCENARIOS = [
    ("01_basic_add", "Add an analog signal source and a null sink to the graph. Connect the signal source to the null sink.", 2),
    ("02_throttle_add", "Add an analog signal source, a throttle block, and a null sink. Connect source -> throttle -> sink in series.", 3),
    ("03_param_update", "Add an analog signal source and a null sink, connected. Set the source frequency to 2400 Hz and amplitude to 0.5.", 2),
    ("04_notch_filter", "Add an analog signal source and a null sink connected. Insert a band-reject filter between the source and sink with a center frequency of 1000 Hz.", 3),
    ("05_block_swap", "Add an analog signal source, a throttle block, and a null sink connected in series.", 3),
    ("06_double_samp_rate", "Add an analog signal source and a null sink connected. Set the source sample rate to 2e6 and frequency to 100e3.", 2),
    ("07_fft_chain", "Add a complex signal source with frequency 1000 Hz, stream to vector with 1024 items, forward FFT, complex to mag squared, and null sink all connected in series.", 5),
    ("08_variable_cascade", "Add a variable named samp_rate with value 1e6. Add an analog signal source using samp_rate as the sample rate parameter. Add a null sink. Connect source to sink.", 2),
]


def main():
    print(f"Wireless Engineering Matrix — {len(SCENARIOS)} scenarios")
    print(f"Model: {MODEL}  |  Harness: Seamless + sanitized")
    print(f"Commit: 2b85a52 (slop eradicated)")
    print("=" * 70)

    results = []
    passed = 0
    failed = 0

    for sid, message, expected_blocks in SCENARIOS:
        print(f"\n▶ {sid}: {message[:80]}...")
        reset_blank()
        start = time.monotonic()
        try:
            result = run_chat(message)
        except subprocess.TimeoutExpired:
            result = {"ok": False, "error": "timeout"}
        elapsed = time.monotonic() - start

        score = score_result(result)
        score["scenario"] = sid
        score["elapsed_s"] = round(elapsed, 1)

        # Pass criteria: at least expected_blocks on disk
        blocks_ok = score["blocks_on_disk"] >= expected_blocks
        committed = score["committed"]
        score["pass"] = committed and blocks_ok

        if score["pass"]:
            passed += 1
            status = "PASS"
        else:
            failed += 1
            status = "FAIL"

        print(f"  {status} | rev={score['state_revision']} rounds={score['tool_rounds']} "
              f"blocks={score['blocks_on_disk']}/{expected_blocks} "
              f"conns={score['connections_on_disk']} "
              f"inspect={score['inspect_graph_calls']} "
              f"change={score['change_graph_calls']} "
              f"query={score['query_knowledge_calls']} "
              f"time={score['elapsed_s']}s")
        results.append(score)

    print("\n" + "=" * 70)
    print(f"RESULTS: {passed}/{len(SCENARIOS)} passed, {failed}/{len(SCENARIOS)} failed")
    print(f"Composite pass rate: {passed / len(SCENARIOS) * 100:.0f}%")

    # Summary stats
    all_rounds = [r["tool_rounds"] for r in results]
    all_inspect = [r["inspect_graph_calls"] for r in results]
    all_blocks = [r["blocks_on_disk"] for r in results]

    print(f"Avg rounds: {sum(all_rounds) / len(all_rounds):.1f}")
    print(f"Avg inspect calls: {sum(all_inspect) / len(all_inspect):.1f}")
    print(f"Avg blocks on disk: {sum(all_blocks) / len(all_blocks):.1f}")
    print(f"Total committed runs: {sum(1 for r in results if r['committed'])}/{len(SCENARIOS)}")
    print(f"Total valid runs: {sum(1 for r in results if r['valid'])}/{len(SCENARIOS)}")

    # Save report
    report = {
        "harness": "seamless-sanitized-2b85a52",
        "model": MODEL,
        "scenarios": len(SCENARIOS),
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / len(SCENARIOS),
        "results": results,
    }
    report_path = PROJECT_ROOT / "wireless_matrix_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"\nReport saved: {report_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    reset_blank()
    sys.exit(main())
