#!/usr/bin/env python3
"""Build high-signal MVP advisor mode corpus v2 (contrastive, balanced)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

OUTPUT_PATH = Path("tests/data/turnplan_advisor_mvp_modes_v2.jsonl")

TARGET_COUNTS = {
    "inspect": 200,      # 20%
    "preview": 200,      # 20%
    "change": 200,       # 20%
    "clarify": 250,      # 25%
    "unsupported": 150,  # 15%
}


def _row(prompt: str, mode: str, notes: str) -> dict[str, object]:
    return {
        "prompt": prompt,
        "expected_mode": mode,
        "mutation_allowed": mode == "change",
        "clarification_expected": mode == "clarify",
        "unsupported_expected": mode == "unsupported",
        "preview_only_expected": mode == "preview",
        "notes": notes,
    }


def _inspect_candidates() -> list[dict[str, object]]:
    verbs = [
        "Summarize",
        "Explain",
        "Describe",
        "Show",
        "List",
        "Find",
        "Search for",
        "Inspect",
    ]
    targets = [
        "this graph",
        "the active flowgraph",
        "all variables",
        "all connections",
        "all QT GUI blocks",
        "throttle blocks",
        "message ports",
        "where tags are used",
        "the signal path",
        "the current validation status",
    ]
    rows: list[dict[str, object]] = []
    for verb in verbs:
        for target in targets:
            rows.append(_row(f"{verb} {target}.", "inspect", "inspect_concept"))
            if target.startswith("the"):
                rows.append(_row(f"{verb} {target} please.", "inspect", "inspect_polite"))
    rows.extend(
        [
            _row("Validate this graph.", "inspect", "inspect_validate_via_wrapper"),
            _row("Check whether this compiles.", "inspect", "inspect_validate_via_wrapper"),
            _row("Run validation only and tell me if it is valid.", "inspect", "inspect_validate_via_wrapper"),
        ]
    )
    return rows


def _preview_candidates() -> list[dict[str, object]]:
    stems = [
        "set samp_rate to 48000",
        "disable blocks_throttle2_0",
        "remove connection blocks_throttle2_0:0->blocks_char_to_float_0:0",
        "rewire blocks_throttle2_0:0->blocks_char_to_float_0:0 to analog_random_source_x_0:0->blocks_char_to_float_0:0",
        "insert a throttle on connection blocks_throttle2_0:0->blocks_char_to_float_0:0",
        "add variable demo_rate with value 9600",
    ]
    preview_markers = [
        "Preview",
        "Dry-run",
        "Draft",
        "Propose",
        "Show me what would happen if you",
        "Try changing and do not apply:",
    ]
    tails = [
        "do not apply",
        "don't commit",
        "without changing the graph",
        "before applying",
        "as a proposal only",
    ]
    rows: list[dict[str, object]] = []
    for marker in preview_markers:
        for stem in stems:
            rows.append(_row(f"{marker} {stem}.", "preview", "preview_direct"))
            for tail in tails:
                rows.append(_row(f"{marker} {stem}, {tail}.", "preview", "preview_no_commit"))
    rows.append(
        _row(
            "Show me the transaction for setting gain to 2.",
            "preview",
            "preview_transaction_language",
        )
    )
    return rows


def _change_candidates() -> list[dict[str, object]]:
    exact_changes = [
        "Set samp_rate to 48000.",
        "Set samp_rate to 16000.",
        "Disable blocks_throttle2_0.",
        "Enable blocks_throttle2_0.",
        "Add variable self_dogfood_flag with value 1.",
        "Remove exact connection blocks_throttle2_0:0->blocks_char_to_float_0:0.",
        "Rewire exact connection blocks_throttle2_0:0->blocks_char_to_float_0:0 to analog_random_source_x_0:0->blocks_char_to_float_0:0.",
        "Insert blocks_head on exact connection blocks_throttle2_0:0->blocks_char_to_float_0:0.",
        "Change variable samp_rate value to 32000.",
        "Set qtgui_time_sink_x_0 update_time to 0.20.",
    ]
    wrappers = [
        "Do this now:",
        "Apply the change:",
        "Execute:",
        "Make this live change:",
    ]
    rows: list[dict[str, object]] = []
    for sentence in exact_changes:
        rows.append(_row(sentence, "change", "change_exact"))
        for wrapper in wrappers:
            rows.append(_row(f"{wrapper} {sentence}", "change", "change_exact_wrapped"))
    return rows


def _clarify_candidates() -> list[dict[str, object]]:
    vague_edit = [
        "Change that block.",
        "Set the important rate.",
        "Adjust the sink parameter.",
        "Update that value.",
        "Tweak the cutoff.",
    ]
    vague_disconnect = [
        "Disconnect the bad wire.",
        "Remove the connection from the source.",
        "Disconnect from the bad endpoint.",
        "Remove that connection.",
    ]
    vague_rewire = [
        "Wire this differently.",
        "Fix the topology.",
        "Repair the wiring.",
        "Rewire everything to make it work.",
    ]
    vague_insert = [
        "Add a compatible filter somewhere.",
        "Insert something in the middle.",
        "Drop a block between those two.",
    ]
    duplicate_ambiguous = [
        "Disable the duplicate block.",
        "Pick the right one and change it.",
        "Use the first matching throttle and update it.",
        "Change the right sink block value.",
    ]
    ambiguous_disconnect_vs_remove = [
        "Remove connection not block.",
        "Disconnect it, not the block.",
    ]
    rows: list[dict[str, object]] = []
    for prompt in (
        vague_edit
        + vague_disconnect
        + vague_rewire
        + vague_insert
        + duplicate_ambiguous
        + ambiguous_disconnect_vs_remove
    ):
        rows.append(_row(prompt, "clarify", "clarify_under_specified"))
        rows.append(_row(f"{prompt} Please.", "clarify", "clarify_under_specified"))
        rows.append(_row(f"{prompt} quickly.", "clarify", "clarify_under_specified"))
    return rows


def _unsupported_candidates() -> list[dict[str, object]]:
    prompts = [
        "Edit raw .grc YAML directly.",
        "Patch the source text of this graph.",
        "Undo the last change.",
        "Redo the previous operation.",
        "Export this graph as Python code.",
        "Generate runnable Python from this graph now.",
        "Bypass validation and force apply this edit.",
        "Save the invalid graph anyway.",
        "Ignore grcc and commit the broken graph.",
        "Apply a tutorial recipe directly.",
        "Mutate by block_uid block:abc123 now.",
    ]
    rows: list[dict[str, object]] = []
    for prompt in prompts:
        rows.append(_row(prompt, "unsupported", "unsupported_contract"))
        rows.append(_row(f"{prompt} Do it now.", "unsupported", "unsupported_contract"))
    return rows


def _take_balanced(
    candidates: list[dict[str, object]],
    *,
    count: int,
) -> list[dict[str, object]]:
    if not candidates:
        return []
    out: list[dict[str, object]] = []
    index = 0
    while len(out) < count:
        out.append(candidates[index % len(candidates)])
        index += 1
    return out


def build_rows() -> list[dict[str, object]]:
    grouped = {
        "inspect": _inspect_candidates(),
        "preview": _preview_candidates(),
        "change": _change_candidates(),
        "clarify": _clarify_candidates(),
        "unsupported": _unsupported_candidates(),
    }
    rows: list[dict[str, object]] = []
    for mode, count in TARGET_COUNTS.items():
        rows.extend(_take_balanced(grouped[mode], count=count))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-path", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args(argv)

    rows = build_rows()
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    with args.output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"Wrote {len(rows)} rows to {args.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
