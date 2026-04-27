"""Backend comparison runner. Runs focused scenario families per model.

Usage:
    GRC_AGENT_LIVE_LLAMA_MODEL="unsloth/gemma-4-E4B-it-GGUF" uv run python -m tests.scenarios.compare_backends

Records 2B baseline first if model not specified, then runs target model.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from grc_agent.retrieval import initialize_retrieval
from grc_agent.llama_server import LlamaServerClient
from tests.llama_eval.harness import (
    restart_llama_server,
)
from tests.scenarios.families import ALL_CASES, run_scenario

FOCUS_FAMILIES = {"A_raw_yaml", "C_save", "D_message", "E_insertion", "G_create"}

MODEL_ORDER = [
    "unsloth/gemma-4-E2B-it-GGUF",
    "unsloth/gemma-4-E4B-it-GGUF",
    "unsloth/Qwen3.5-9B-GGUF",
]


def _find_focus_cases():
    return sorted(
        [c for c in ALL_CASES if c.family in FOCUS_FAMILIES],
        key=lambda c: c.case_id,
    )


def run_model(model: str, cases: list, catalog_root: str | None):
    print(f"\n{'='*80}")
    print(f"MODEL: {model}")
    print(f"{'='*80}")

    env_url = os.environ.get("GRC_AGENT_LIVE_LLAMA_URL")
    if env_url:
        print(f"Connecting to existing server at {env_url}")
        client = LlamaServerClient(base_url=env_url)
        server_url = env_url
        model_alias = model
    else:
        try:
            server_url, model_alias, client = restart_llama_server(model=model)
        except Exception as exc:
            print(f"FATAL: Could not start llama.cpp server for {model}: {exc}")
            return []

    print(f"Server: {server_url} model={model_alias}")

    results = []
    for i, case in enumerate(cases):
        label = f"[{i+1}/{len(cases)}] {case.case_id}"
        print(f"{label}: {case.prompt[:60]}...", end="", flush=True)

        try:
            result = run_scenario(case, client, model_alias, catalog_root)
        except Exception as exc:
            print(f" FATAL_EXCEPTION | {exc}")
            # Create minimal result record
            from tests.harness.types import ScenarioResult
            result = ScenarioResult(
                scenario_id=case.case_id,
                scenario_family=case.family,
                error=str(exc),
                failure_category="INFRA_FAIL",
            )

        results.append(result)
        cat = result.failure_category
        tools = " -> ".join(result.tool_names) if result.tool_names else "(none)"
        print(f" {cat} | {tools}")
        if result.invariant_violations:
            for v in result.invariant_violations:
                print(f"    INV: {v}")
        if result.error:
            print(f"    ERR: {result.error[:80]}")

    return results


def _summarize(results):
    from collections import Counter
    by_family = {}
    for r in results:
        by_family.setdefault(r.scenario_family, []).append(r)

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    total_pass = 0
    total_cases = len(results)
    all_cats = Counter()

    for family, rs in sorted(by_family.items()):
        cats = Counter(r.failure_category for r in rs)
        passed = cats.get("PASS", 0)
        total_pass += passed
        all_cats.update(cats)
        print(f"{family:15s}: {passed}/{len(rs)} PASS")
        for cat, count in sorted(cats.items()):
            print(f"  {cat:20s}: {count}")

    print(f"\nTotal: {total_pass}/{total_cases} PASS")
    for cat, count in sorted(all_cats.items()):
        print(f"  {cat:20s}: {count}")
    return total_pass, total_cases, all_cats


def main():
    # Allow override from environment
    target_model = os.environ.get("GRC_AGENT_LIVE_LLAMA_MODEL")
    if not target_model:
        print("Set GRC_AGENT_LIVE_LLAMA_MODEL to specify target model")
        print("Available models:")
        for m in MODEL_ORDER:
            print(f"  {m}")
        sys.exit(0)

    focus_cases = _find_focus_cases()
    print(f"Focus cases: {len(focus_cases)}")
    for c in focus_cases:
        print(f"  {c.case_id:6} {c.family}")

    print("\nInitializing retrieval...")
    readiness = initialize_retrieval()
    catalog_root = readiness.get("catalog_root") if readiness.get("ok") else None
    print(f"Retrieval: {'ready' if readiness.get('ok') else 'NOT READY'}")

    results = run_model(target_model, focus_cases, catalog_root)
    total_pass, total_cases, all_cats = _summarize(results)

    # Save results
    output = {
        "model": target_model,
        "total_cases": total_cases,
        "pass": total_pass,
        "categories": dict(all_cats),
        "details": [
            {
                "scenario_id": r.scenario_id,
                "scenario_family": r.scenario_family,
                "failure_category": r.failure_category,
                "tool_names": r.tool_names,
                "assistant_text": r.assistant_text[:200] if r.assistant_text else "",
                "apply_edit_ok": r.apply_edit_ok,
                "error": (r.error or "")[:100],
            }
            for r in results
        ],
    }

    out_path = Path(f"/tmp/backend_compare_{target_model.replace('/', '_')}.json")
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults written: {out_path}")


if __name__ == "__main__":
    main()
