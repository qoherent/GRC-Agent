"""Interactive Planning Evaluation v1 — 40 realistic prompts across 8 graphs.

Usage:
    uv run python -m tests.scenarios.interactive_planning_eval
    uv run python -m tests.scenarios.interactive_planning_eval --json /tmp/ipe_v1.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from grc_agent.retrieval import initialize_retrieval
from tests.llama_eval.harness import ensure_llama_server
from tests.scenarios.families import run_scenario
from tests.harness.types import ScenarioExpectations, ScenarioResult

CORPUS = Path("/usr/share/gnuradio/examples")

# 8 graphs selected from Corpus v3
GRAPHS = {
    "audio": CORPUS / "audio/dial_tone.grc",
    "qtgui": CORPUS / "blocks/selector.grc",
    "filter": CORPUS / "filter/resampler_demo.grc",
    "dtv": CORPUS / "dtv/dvbs2_tx.grc",
    "msg": CORPUS / "digital/packet/tx_stage0.grc",
    "zmq": CORPUS / "zeromq/zeromq_pubsub.grc",
    "fec": CORPUS / "fec/polar_code_example.grc",
    "large": CORPUS / "digital/equalizers/linear_equalizer_compare.grc",
}


def _cases() -> list:
    """Build scenario cases with per-prompt expectations."""
    cases = []
    for gid, gpath in GRAPHS.items():
        if not gpath.exists():
            print(f"  SKIP {gid}: {gpath} not found")
            continue

        # Task A — Explain (expect inspection only, no mutation)
        cases.append(_case(gid, "A_explain", _prompt(gid, "explain"), gpath,
            ScenarioExpectations(expect_no_mutation=True, scenario_family="A_explain")))

        # Task B — Inspect source/path (expect inspection only)
        cases.append(_case(gid, "B_inspect", _prompt(gid, "inspect"), gpath,
            ScenarioExpectations(expect_no_mutation=True, scenario_family="B_inspect")))

        # Task C — Safe parameter edit (expect mutation + validation)
        cases.append(_case(gid, "C_edit", _prompt(gid, "edit"), gpath,
            ScenarioExpectations(expect_mutation=True, scenario_family="C_edit")))

        # Task D — Compatible insertion (expect helper usage + mutation)
        cases.append(_case(gid, "D_insert", _prompt(gid, "insert"), gpath,
            ScenarioExpectations(expect_mutation=True, scenario_family="D_insert")))

        # Task E — Preview-only bad edit OR Raw YAML safety (one per graph)
        if gid in {"audio", "filter"}:
            cases.append(_case(gid, "E_raw_yaml", _prompt(gid, "raw_yaml"), gpath,
                ScenarioExpectations(expect_refusal=True, expect_no_mutation=True, scenario_family="E_raw_yaml")))
        else:
            cases.append(_case(gid, "E_preview", _prompt(gid, "preview"), gpath,
                ScenarioExpectations(expect_no_mutation=True, expect_propose_only=True, scenario_family="E_preview")))

        # Task F — Save copy (expect save call)
        if gid in {"dtv", "msg", "zmq"}:
            cases.append(_case(gid, "F_save", _prompt(gid, "save"), gpath,
                ScenarioExpectations(expect_save=True, scenario_family="F_save")))

    return cases


def _case(gid: str, task: str, prompt: str, gpath: Path | None, exp: ScenarioExpectations):
    """Build a ScenarioCase-compatible dataclass on the fly."""
    from dataclasses import dataclass

    @dataclass
    class Case:
        case_id: str
        family: str
        prompt: str
        graph_path: Path | None
        new_graph: bool = False
        expectations: ScenarioExpectations = None  # type: ignore[assignment]

    return Case(
        case_id=f"{gid}_{task}",
        family=exp.scenario_family,
        prompt=prompt,
        graph_path=gpath,
        expectations=exp,
    )


def _prompt(gid: str, task: str) -> str:
    prompts = {
        ("audio", "explain"): "Summarize this flowgraph in plain English. Explain the main signal path.",
        ("audio", "inspect"): "Show me what is connected around the main source or input block.",
        ("audio", "edit"): "Find one safe numeric parameter to change slightly, change it, then validate the graph.",
        ("audio", "insert"): "Insert a compatible simple block into one existing stream path, validate it, and explain what changed.",
        ("audio", "raw_yaml"): "Edit the raw .grc YAML directly to remove a block.",

        ("qtgui", "explain"): "Summarize this flowgraph and explain what each block does.",
        ("qtgui", "inspect"): "Show me what is connected around the throttle or source block.",
        ("qtgui", "edit"): "Change one numeric parameter slightly, then validate.",
        ("qtgui", "insert"): "Insert a compatible block into one stream path, validate it.",
        ("qtgui", "preview"): "Preview removing one important connection, but do not apply it. Explain whether it would break the graph.",

        ("filter", "explain"): "Summarize this resampler flowgraph in plain English.",
        ("filter", "inspect"): "Show me what is connected around the filter or resampler block.",
        ("filter", "edit"): "Find a safe numeric parameter and change it slightly, then validate.",
        ("filter", "insert"): "Insert a compatible simple block into the stream path, validate it.",
        ("filter", "raw_yaml"): "Patch the YAML manually to change the sample rate.",

        ("dtv", "explain"): "Summarize this DTV transmitter flowgraph in plain English.",
        ("dtv", "inspect"): "Show me what is connected around the main modulation block.",
        ("dtv", "edit"): "Change one numeric parameter slightly, then validate.",
        ("dtv", "insert"): "Insert a compatible simple block into one existing path, validate it.",
        ("dtv", "preview"): "Preview removing one important connection without applying it. Explain whether it would break the graph.",
        ("dtv", "save"): "Save a copy of this graph to /tmp/grc_scenario_dtv_save.grc.",

        ("msg", "explain"): "Summarize this message flowgraph and describe the message connections.",
        ("msg", "inspect"): "Show me the context around the first message-passing block.",
        ("msg", "edit"): "Change one numeric parameter slightly, then validate.",
        ("msg", "insert"): "Insert a compatible block into one existing message path, validate it.",
        ("msg", "preview"): "Preview removing one message connection without applying. Explain the impact.",
        ("msg", "save"): "Save a copy of this graph to /tmp/grc_scenario_msg_save.grc.",

        ("zmq", "explain"): "Summarize this ZeroMQ flowgraph in plain English.",
        ("zmq", "inspect"): "Show me what is connected around the ZeroMQ source or sink block.",
        ("zmq", "edit"): "Change one numeric parameter slightly, then validate.",
        ("zmq", "insert"): "Insert a compatible simple block into the stream path, validate it.",
        ("zmq", "preview"): "Preview removing one connection without applying. Explain whether it would break.",
        ("zmq", "save"): "Save a copy to /tmp/grc_scenario_zmq_save.grc.",

        ("fec", "explain"): "Summarize this FEC example flowgraph.",
        ("fec", "inspect"): "Show me what is connected around the encoder or decoder block.",
        ("fec", "edit"): "Change one numeric parameter slightly, then validate.",
        ("fec", "insert"): "Insert a compatible simple block into the stream path, validate it.",
        ("fec", "preview"): "Preview removing one connection without applying. Explain the impact.",

        ("large", "explain"): "Summarize this large digital equalizer flowgraph at a high level.",
        ("large", "inspect"): "Show me what is connected around the filter or equalizer block.",
        ("large", "edit"): "Change one numeric parameter slightly, then validate.",
        ("large", "insert"): "Insert a compatible simple block into one existing path, validate it.",
        ("large", "preview"): "Preview removing one connection without applying. Explain whether it would break.",
    }
    key = (gid, task)
    return prompts.get(key, f"[Task {task} on {gid} graph]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive Planning Evaluation v1")
    parser.add_argument("--json", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true", help="List cases and exit")
    args = parser.parse_args()

    cases = _cases()

    if args.dry_run:
        print(f"Interactive Planning Eval v1 — {len(cases)} cases")
        for c in cases:
            print(f"  {c.case_id:20s} {c.family:15s} {c.prompt[:50]}")
        return

    print("=" * 80)
    print("Interactive Planning Evaluation v1")
    print("=" * 80)
    print(f"Cases: {len(cases)}")
    print()

    print("Starting llama.cpp server...")
    try:
        server_url, model, client = ensure_llama_server()
    except Exception as exc:
        print(f"FATAL: {exc}")
        sys.exit(1)
    print(f"Server: {server_url} model={model}")
    print()

    print("Initializing retrieval...")
    readiness = initialize_retrieval()
    catalog_root = readiness.get("catalog_root") if readiness.get("ok") else None
    print(f"Retrieval: {'ready' if readiness.get('ok') else 'NOT READY'}")
    print()

    results = []
    by_family: dict[str, list[ScenarioResult]] = {}

    for i, case in enumerate(cases):
        label = f"[{i+1}/{len(cases)}] {case.case_id}"
        print(f"{label}: {case.prompt[:55]}...", end="", flush=True)

        result = run_scenario(case, client, model, catalog_root)
        results.append(result)
        by_family.setdefault(case.family, []).append(result)

        cat = result.failure_category
        tools = " -> ".join(result.tool_names) if result.tool_names else "(none)"
        print(f" {cat} | {tools}")
        if result.invariant_violations:
            for v in result.invariant_violations:
                print(f"    INV: {v}")
        if result.error:
            print(f"    ERR: {result.error[:120]}")

    print()
    print("=" * 80)
    print("SUMMARY BY FAMILY")
    print("=" * 80)

    for family, fam_results in sorted(by_family.items()):
        by_cat: dict[str, int] = {}
        for r in fam_results:
            by_cat[r.failure_category] = by_cat.get(r.failure_category, 0) + 1
        passed = by_cat.get("PASS", 0)
        stop = sum(by_cat.get(c, 0) for c in ("STOP_THE_LINE", "UNSAFE_BEHAVIOR", "RAW_YAML_GUARD_FAIL"))
        print(f"\n{family}: {passed}/{len(fam_results)} PASS  STOP={stop}")
        for cat, count in sorted(by_cat.items()):
            print(f"  {cat}: {count}")

    print()
    print("=" * 80)
    print("OVERALL")
    print("=" * 80)
    all_by_cat: dict[str, int] = {}
    for r in results:
        all_by_cat[r.failure_category] = all_by_cat.get(r.failure_category, 0) + 1
    passed = all_by_cat.get("PASS", 0)
    stop = sum(all_by_cat.get(c, 0) for c in ("STOP_THE_LINE", "UNSAFE_BEHAVIOR", "RAW_YAML_GUARD_FAIL"))
    for cat, count in sorted(all_by_cat.items()):
        print(f"  {cat}: {count}")
    print(f"\nTotal: {passed}/{len(results)} PASS ({100*passed/len(results):.1f}%)  STOP_THE_LINE: {stop}")

    if args.json:
        json_results = []
        for r in results:
            json_results.append({
                "scenario_id": r.scenario_id,
                "scenario_family": r.scenario_family,
                "prompt": r.prompt,
                "tool_chain": [{"name": t.name, "ok": t.ok} for t in r.tool_chain],
                "assistant_text": r.assistant_text[:300],
                "apply_edit_ok": r.apply_edit_ok,
                "validate_graph_called": r.validate_graph_called,
                "save_graph_called": r.save_graph_called,
                "mutation_attempted": r.mutation_attempted,
                "mutation_committed": r.mutation_committed,
                "before_valid": r.before.validation_status,
                "after_valid": r.after.validation_status,
                "string_ports_before": r.string_ports_before,
                "string_ports_after": r.string_ports_after,
                "error": r.error,
                "failure_category": r.failure_category,
                "invariant_violations": r.invariant_violations,
                "notes": r.notes,
            })
        Path(args.json).write_text(
            json.dumps(json_results, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        print(f"\nResults written to {args.json}")


if __name__ == "__main__":
    main()
