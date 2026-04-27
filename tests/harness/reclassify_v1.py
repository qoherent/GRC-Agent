"""Re-classify existing real-usage v1 results through the harness v2 classifier.

Usage:
    uv run python -m tests.harness.reclassify_v1 /tmp/add_block_detail.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tests.harness.types import (
    ScenarioResult,
    ScenarioExpectations,
    StateSnapshot,
    ToolRecord,
)
from tests.harness.classifier import classify_result

TASK_EXPECTATIONS: dict[str, ScenarioExpectations] = {
    "summary": ScenarioExpectations(
        expect_no_mutation=True,
        scenario_family="inspection",
    ),
    "context": ScenarioExpectations(
        expect_no_mutation=True,
        scenario_family="inspection",
    ),
    "param_edit": ScenarioExpectations(
        expect_mutation=True,
        expect_validate=True,
        scenario_family="edit",
    ),
    "save_copy": ScenarioExpectations(
        expect_save=True,
        scenario_family="save",
    ),
    "preview_bad": ScenarioExpectations(
        expect_no_mutation=True,
        expect_propose_only=True,
        scenario_family="preview",
    ),
    "add_block": ScenarioExpectations(
        expect_mutation=True,
        expect_validate=True,
        scenario_family="insertion",
    ),
    "raw_yaml": ScenarioExpectations(
        expect_refusal=True,
        expect_no_mutation=True,
        scenario_family="safety",
    ),
    "msg_preview": ScenarioExpectations(
        expect_no_mutation=True,
        expect_propose_only=True,
        scenario_family="message",
    ),
}


def build_result(entry: dict) -> tuple[ScenarioResult, ScenarioExpectations]:
    task_id = entry["task_id"]
    exp = TASK_EXPECTATIONS.get(task_id, ScenarioExpectations())
    exp.prompt = entry.get("prompt", "")

    tool_names = entry.get("executed_tool_names", [])
    apply_edit_ok = entry.get("apply_edit_ok")

    tool_chain = [ToolRecord(name=n) for n in tool_names]
    for t in tool_chain:
        if t.name == "apply_edit":
            t.ok = apply_edit_ok

    before = StateSnapshot(
        validation_status=entry.get("grcc_valid_before"),
    )
    after = StateSnapshot(
        validation_status=entry.get("grcc_valid_after"),
    )

    mutation_attempted = "apply_edit" in tool_names
    mutation_committed = apply_edit_ok is True

    result = ScenarioResult(
        scenario_id=f"{entry['graph_id']}/{task_id}",
        scenario_family=exp.scenario_family,
        prompt=exp.prompt,
        before=before,
        after=after,
        tool_chain=tool_chain,
        assistant_text=entry.get("assistant_text", ""),
        error=entry.get("error"),
        elapsed_seconds=entry.get("elapsed_seconds", 0.0),
        apply_edit_called="apply_edit" in tool_names,
        apply_edit_ok=apply_edit_ok,
        propose_edit_called="propose_edit" in tool_names,
        validate_graph_called="validate_graph" in tool_names,
        save_graph_called="save_graph" in tool_names,
        mutation_attempted=mutation_attempted,
        mutation_committed=mutation_committed,
        notes=entry.get("notes", ""),
    )

    return result, exp


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: uv run python -m tests.harness.reclassify_v1 <json_path>")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    if not json_path.exists():
        print(f"File not found: {json_path}")
        sys.exit(1)

    data = json.loads(json_path.read_text())
    print(f"Loaded {len(data)} results from {json_path}")
    print()

    old_by_cat: dict[str, int] = {}
    new_by_cat: dict[str, int] = {}
    changes: list[dict] = []

    for entry in data:
        result, exp = build_result(entry)
        old_cat = entry.get("failure_category", "UNKNOWN")
        new_cat = classify_result(result, exp)

        old_by_cat[old_cat] = old_by_cat.get(old_cat, 0) + 1
        new_by_cat[new_cat] = new_by_cat.get(new_cat, 0) + 1

        if old_cat != new_cat:
            changes.append({
                "id": result.scenario_id,
                "old": old_cat,
                "new": new_cat,
                "tools": " -> ".join(result.tool_names) or "(none)",
                "apply_ok": result.apply_edit_ok,
                "violations": result.invariant_violations,
            })

    print("OLD classification:")
    for cat, count in sorted(old_by_cat.items()):
        print(f"  {cat}: {count}")

    print()
    print("NEW classification:")
    for cat, count in sorted(new_by_cat.items()):
        print(f"  {cat}: {count}")

    print()
    if changes:
        print(f"Classification changes: {len(changes)}")
        print()
        print(f"{'ID':<35} {'OLD':<25} {'NEW':<25} {'TOOLS'}")
        print("-" * 120)
        for c in changes:
            print(f"{c['id']:<35} {c['old']:<25} {c['new']:<25} {c['tools']}")
            if c["violations"]:
                for v in c["violations"]:
                    print(f"  INV: {v}")
    else:
        print("No classification changes.")

    inv_count = sum(1 for c in changes if c["violations"])
    if inv_count:
        print(f"\nSTOP_THE_LINE from invariant violations: {inv_count}")


if __name__ == "__main__":
    main()
