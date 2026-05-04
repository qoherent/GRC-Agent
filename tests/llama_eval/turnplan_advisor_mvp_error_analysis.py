#!/usr/bin/env python3
"""Analyze MVP-mode advisor errors and emit grouped root-cause report."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any

DEFAULT_INPUT = Path("reports/turnplan_advisor_shadow_2026-05-02.jsonl")
DEFAULT_REPORT = Path("reports/TURNPLAN_ADVISOR_MVP_ERROR_ANALYSIS.md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-jsonl", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    rows = _load_rows(args.input_jsonl)
    groups = _group_errors(rows)
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.write_text(_render_report(rows, groups, args.input_jsonl), encoding="utf-8")
    print(f"Wrote MVP error analysis report to {args.report_path}")
    return 0


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def _error_group(expected: str, actual: str) -> str:
    if expected == "preview" and actual == "change":
        return "preview -> change"
    if expected == "clarify" and actual == "change":
        return "clarify -> change"
    if expected == "unsupported" and actual == "change":
        return "unsupported -> change"
    if expected == "preview" and actual == "inspect":
        return "preview -> inspect"
    return "other"


def _group_errors(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        expected = str(row.get("expected_mode") or (row.get("expected") or {}).get("expected_mode", ""))
        actual = str(row.get("advisor_mode") or (row.get("advisor_mvp") or {}).get("mode", ""))
        if expected == actual:
            continue
        grouped[_error_group(expected, actual)].append(row)
    return grouped


def _representative_prompts(rows: list[dict[str, Any]], limit: int = 5) -> list[str]:
    prompts: list[str] = []
    seen: set[str] = set()
    for row in rows:
        prompt = str(row.get("prompt", "")).strip()
        if not prompt or prompt in seen:
            continue
        seen.add(prompt)
        prompts.append(prompt)
        if len(prompts) >= limit:
            break
    return prompts


def _root_cause_and_fix(group: str) -> tuple[str, str]:
    if group == "preview -> change":
        return (
            "Preview/proposal phrasing is being interpreted as execution intent.",
            "Add stronger contrastive preview-vs-change examples (propose/dry-run/draft/what-would-happen/no-commit).",
        )
    if group == "clarify -> change":
        return (
            "Action verb is recognized, but missing target/endpoint/placement/duplicate-choice is ignored.",
            "Increase actionability contrastive pairs (exact target vs underspecified target) with clarify-first supervision.",
        )
    if group == "unsupported -> change":
        return (
            "Out-of-contract workflows are mapped to executable change.",
            "Add unsupported-heavy pairs for raw YAML/source-edit/undo-redo/export/force-save-invalid vs supported edits.",
        )
    if group == "preview -> inspect":
        return (
            "Preview requests are treated as pure read-only instead of dry-run change.",
            "Add preview examples that keep non-mutating behavior but still classify as preview mode.",
        )
    return (
        "Mixed residual confusion across classes.",
        "Expand balanced contrastive corpus and prioritize high-loss confusion pairs in few-shot examples.",
    )


def _render_report(
    rows: list[dict[str, Any]],
    grouped: dict[str, list[dict[str, Any]]],
    input_path: Path,
) -> str:
    total = len(rows)
    errors = sum(len(items) for items in grouped.values())
    confusion = Counter()
    for row in rows:
        expected = str(row.get("expected_mode") or (row.get("expected") or {}).get("expected_mode", ""))
        actual = str(row.get("advisor_mode") or (row.get("advisor_mvp") or {}).get("mode", ""))
        confusion[(expected, actual)] += 1

    ordered_groups = [
        "preview -> change",
        "clarify -> change",
        "unsupported -> change",
        "preview -> inspect",
        "other",
    ]

    lines = [
        "# TurnPlan Advisor MVP Error Analysis",
        "",
        f"- Source JSONL: `{input_path}`",
        f"- Rows analyzed: {total}",
        f"- Error rows: {errors}",
        "",
        "## Error Groups",
        "",
    ]

    for group in ordered_groups:
        items = grouped.get(group, [])
        cause, fix = _root_cause_and_fix(group)
        lines.append(f"### {group}")
        lines.append("")
        lines.append(f"- Count: {len(items)}")
        lines.append(f"- Root cause: {cause}")
        lines.append(f"- Prompt/corpus fix proposal: {fix}")
        prompts = _representative_prompts(items)
        if prompts:
            lines.append("- Representative prompts:")
            for prompt in prompts:
                lines.append(f"  - {prompt}")
        lines.append("")

    lines.extend(
        [
            "## Confusion Snapshot",
            "",
        ]
    )
    for (expected, actual), count in sorted(confusion.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))[:20]:
        lines.append(f"- expected `{expected}` -> actual `{actual}`: {count}")

    lines.extend(
        [
            "",
            "## Summary",
            "",
            "- Main failure mode is over-classifying ambiguous/preview/unsupported requests as `change`.",
            "- Next tuning should increase contrastive actionability supervision and keep runtime unchanged.",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
