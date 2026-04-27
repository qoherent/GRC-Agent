"""Run scenario families through the harness v2.

Usage:
    uv run python -m tests.scenarios.run_families
    uv run python -m tests.scenarios.run_families --family A_raw_yaml
    uv run python -m tests.scenarios.run_families --json /tmp/scenarios.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from grc_agent.retrieval import initialize_retrieval
from tests.llama_eval.harness import ensure_llama_server

from .families import ALL_CASES, run_scenario


def main() -> None:
    parser = argparse.ArgumentParser(description="Run scenario families")
    parser.add_argument("--family", type=str, default=None)
    parser.add_argument("--case", type=str, default=None)
    parser.add_argument("--json", type=str, default=None)
    parser.add_argument("--list", action="store_true", help="List cases and exit")
    args = parser.parse_args()

    cases = ALL_CASES
    if args.family:
        cases = [c for c in cases if c.family == args.family]
    if args.case:
        cases = [c for c in cases if c.case_id == args.case]

    if args.list:
        for c in cases:
            graph = c.graph_path.name if c.graph_path else "(new graph)"
            print(f"  {c.case_id:5s} {c.family:15s} {graph:35s} {c.prompt[:60]}")
        print(f"\nTotal: {len(cases)} cases")
        return

    print("Scenario Families — Harness v2")
    print("=" * 80)

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
    by_family: dict[str, list] = {}

    for i, case in enumerate(cases):
        label = f"[{i+1}/{len(cases)}] {case.case_id}"
        print(f"{label}: {case.prompt[:60]}...", end="", flush=True)

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
            print(f"    ERR: {result.error[:100]}")

    print()
    print("=" * 80)
    print("SUMMARY BY FAMILY")
    print("=" * 80)

    for family, fam_results in sorted(by_family.items()):
        by_cat: dict[str, int] = {}
        for r in fam_results:
            by_cat[r.failure_category] = by_cat.get(r.failure_category, 0) + 1

        total = len(fam_results)
        passed = by_cat.get("PASS", 0)
        stop = sum(by_cat.get(c, 0) for c in ("STOP_THE_LINE", "UNSAFE_BEHAVIOR", "RAW_YAML_GUARD_FAIL"))
        top_cat = max(by_cat, key=by_cat.get) if by_cat else "?"

        print(f"\n{family}: {passed}/{total} PASS  STOP_THE_LINE={stop}  top={top_cat}")
        for cat, count in sorted(by_cat.items()):
            print(f"  {cat}: {count}")

    print()
    print("=" * 80)
    print("OVERALL")
    print("=" * 80)

    all_by_cat: dict[str, int] = {}
    for r in results:
        all_by_cat[r.failure_category] = all_by_cat.get(r.failure_category, 0) + 1

    total = len(results)
    passed = all_by_cat.get("PASS", 0)
    stop = sum(all_by_cat.get(c, 0) for c in ("STOP_THE_LINE", "UNSAFE_BEHAVIOR", "RAW_YAML_GUARD_FAIL"))

    for cat, count in sorted(all_by_cat.items()):
        print(f"  {cat}: {count}")

    print(f"\nTotal: {passed}/{total} PASS ({100*passed/total:.1f}%)  STOP_THE_LINE: {stop}")

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
            })
        Path(args.json).write_text(
            json.dumps(json_results, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        print(f"\nResults written to {args.json}")


if __name__ == "__main__":
    main()
