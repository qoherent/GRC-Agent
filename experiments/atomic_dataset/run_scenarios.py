"""CLI runner for executing harness-driven one-shot scenarios and exporting SFT datasets.

Runs realistic one-shot user prompts through the real GRC Agent executor harness
(`build_agents_from_cfg(cfg).executor`), captures all intermediate tool calls,
reasoning traces, and guardrail retries (`RetryPromptPart`, `ToolFailed`), verifies
every trace with `verify_trace_record`, and writes:
- `<out_dir>/<scenario_id>.json`: Full telemetry record (including `canonical_messages`,
  `messages`, `tools`, `guardrail_events`, `tool_trajectory`, `flowgraph_diff`, `usage`).
- `<out_dir>/<scenario_id>.md`: Human-readable inspection report showing every intermediate
  step, reasoning trace, tool call, guardrail retry, and flowgraph diff.
- `<out_dir>/sft_trajectories.jsonl`: Unsloth-ready OpenAI SFT JSONL dataset.
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
from typing import Any

from experiments.atomic_dataset.harness_driver import run_harness_turn
from experiments.atomic_dataset.scenarios import BENCHMARK_SCENARIOS, ScenarioSpec
from experiments.atomic_dataset.verify_traces import verify_trace_record


def render_markdown_inspection_report(
    record: dict[str, Any],
    verification: dict[str, Any],
) -> str:
    """Render a detailed Markdown inspection report for a single trajectory record."""
    lines = [
        f"# Trajectory Inspection Report: `{record['scenario_id']}`",
        "",
        f"- **Timestamp:** `{datetime.datetime.now().isoformat()}`",
        f"- **Endpoint:** `{record['endpoint']}`",
        f"- **Model:** `{record['model']}`",
        f"- **Elapsed:** `{record['elapsed_seconds']:.2f}s`",
        f"- **Flowgraph Status:** `{'VALID' if record['flowgraph_valid'] else 'INVALID'}`",
        f"- **Verifier Status:** `{'PASS' if verification['ok'] else 'FAIL'}`",
        f"- **Assistant Steps:** `{verification['assistant_turns']}` (Reasoning coverage: `{verification['reasoning_coverage'] * 100:.1f}%`)",
        f"- **Guardrail Events Captured:** `{len(record['guardrail_events'])}`",
        f"- **Usage:** `input={record['usage']['input_tokens']}`, `output={record['usage']['output_tokens']}`, `reasoning={record['usage']['reasoning_tokens']}`, `requests={record['usage']['requests']}`",
        "",
        "## User Prompt",
        "",
        f"> {record['prompt']}",
        "",
        "## Step-by-Step Tool & Reasoning Trajectory",
        "",
    ]

    for step in record["tool_trajectory"]:
        lines.append(
            f"### Step {step['step_index']} (Msg {step['msg_index']}): "
            f"`{step['tool_name'] or 'text_response'}` — Outcome: `{step['outcome']}`"
        )
        if step.get("reasoning_content"):
            lines.extend([
                "",
                "**Reasoning (`ThinkingPart` / `reasoning_content`):**",
                "```text",
                step["reasoning_content"].strip(),
                "```",
            ])
        if step.get("assistant_text"):
            lines.extend([
                "",
                "**Assistant Text (`TextPart`):**",
                step["assistant_text"].strip(),
            ])
        if step.get("tool_name"):
            lines.extend([
                "",
                f"**Tool Call (`{step['tool_name']}`, id=`{step['tool_call_id']}`):**",
                "```json",
                json.dumps(step["args"], indent=2, default=str),
                "```",
            ])
        if step.get("return_content") is not None:
            ret_str = (
                step["return_content"]
                if isinstance(step["return_content"], str)
                else json.dumps(step["return_content"], indent=2, default=str)
            )
            lines.extend([
                "",
                f"**Tool / Guardrail Return (`outcome={step['outcome']}`):**",
                "```text",
                ret_str,
                "```",
            ])
        lines.append("")

    if record["guardrail_events"]:
        lines.extend([
            "## Guardrail Events Detail",
            "",
            "```json",
            json.dumps(record["guardrail_events"], indent=2, default=str),
            "```",
            "",
        ])

    lines.extend([
        "## Flowgraph Diff (Before -> After)",
        "",
        "```json",
        json.dumps(record["flowgraph_diff"], indent=2, default=str),
        "```",
        "",
        "## Final Flowgraph Validation",
        "",
        "```json",
        json.dumps(record["flowgraph_validation"], indent=2, default=str),
        "```",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run harness-driven one-shot GRC Agent scenarios and export SFT dataset."
    )
    parser.add_argument("--endpoint", default="http://192.168.0.234:9000/v1", help="OpenAI base URL")
    parser.add_argument("--model", default="qwen3.8-27b", help="Model ID")
    parser.add_argument("--max-tokens", type=int, default=8192, help="Max token budget per request")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("experiments/atomic_dataset/output"),
        help="Output directory for JSON/MD inspection reports and SFT JSONL",
    )
    parser.add_argument(
        "--scenario",
        default="all",
        help="Scenario ID to run (or 'all' for all 5 benchmark scenarios)",
    )
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.scenario == "all":
        selected: list[ScenarioSpec] = list(BENCHMARK_SCENARIOS)
    else:
        wanted = {s.strip() for s in args.scenario.split(",") if s.strip()}
        selected = [s for s in BENCHMARK_SCENARIOS if s.id in wanted]
        if len(selected) != len(wanted):
            avail = [s.id for s in BENCHMARK_SCENARIOS]
            print(f"[ERROR] Unknown scenario(s) in {args.scenario!r}. Available: {avail}")
            return 1

    print("==========================================================================")
    print(" GRC Agent Harness-Driven One-Shot Trajectory Collector")
    print("==========================================================================")
    print(f"Endpoint:   {args.endpoint}")
    print(f"Model:      {args.model}")
    print(f"Max Tokens: {args.max_tokens}")
    print(f"Scenarios:  {len(selected)}")
    print(f"Output Dir: {args.out_dir}")
    print("==========================================================================\n")

    records: list[dict[str, Any]] = []
    verifications: list[dict[str, Any]] = []
    all_passed = True

    for spec in selected:
        print(f"---> Running [{spec.id}] {spec.name}")
        print(f"     Prompt: {spec.prompt}")
        rec = run_harness_turn(
            scenario_id=spec.id,
            prompt=spec.prompt,
            endpoint=args.endpoint,
            model_name=args.model,
            fixture_path=spec.fixture,
            max_tokens=args.max_tokens,
        )
        rec["category"] = spec.category
        rec["name"] = spec.name

        ver = verify_trace_record(rec)
        rec["verification"] = ver
        if not ver["ok"] or not rec["flowgraph_valid"]:
            all_passed = False

        tool_seq = [
            f"{s['tool_name']}({s['outcome']})"
            for s in rec["tool_trajectory"]
            if s.get("tool_name")
        ]
        print(f"     Completed in {rec['elapsed_seconds']:.2f}s | Requests: {rec['usage']['requests']}")
        print(f"     Tool Trajectory: {' -> '.join(tool_seq)}")
        print(f"     Guardrail Events: {len(rec['guardrail_events'])}")
        print(f"     Flowgraph Valid: {rec['flowgraph_valid']} | Verifier: {'PASS' if ver['ok'] else 'FAIL'}\n")

        # Write full JSON record & Markdown inspection report
        (args.out_dir / f"{spec.id}.json").write_text(
            json.dumps(rec, indent=2, default=str), encoding="utf-8"
        )
        md_report = render_markdown_inspection_report(rec, ver)
        (args.out_dir / f"{spec.id}.md").write_text(md_report, encoding="utf-8")

        records.append(rec)
        verifications.append({"scenario_id": spec.id, **ver})

    # Export Unsloth-ready SFT JSONL
    jsonl_path = args.out_dir / "sft_trajectories.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for rec in records:
            sft_row = {
                "scenario_id": rec["scenario_id"],
                "category": rec["category"],
                "model": rec["model"],
                "flowgraph_valid": rec["flowgraph_valid"],
                "guardrail_events": rec["guardrail_events"],
                "usage": rec["usage"],
                "system": rec["system"],
                "tools": rec["tools"],
                "messages": rec["messages"],
                "canonical_messages": rec["canonical_messages"],
            }
            f.write(json.dumps(sft_row, default=str) + "\n")

    print("==========================================================================")
    print(" Summary of Collected One-Shot Harness Trajectories")
    print("==========================================================================")
    print(f"{'Scenario ID':<30} {'Time':<8} {'Reqs':<6} {'Guardrails':<12} {'FG Valid':<10} {'Audit'}")
    print("-" * 80)
    for rec, ver in zip(records, verifications, strict=True):
        print(
            f"{rec['scenario_id']:<30} "
            f"{rec['elapsed_seconds']:<7.2f}s "
            f"{rec['usage']['requests']:<6} "
            f"{len(rec['guardrail_events']):<12} "
            f"{str(rec['flowgraph_valid']):<10} "
            f"{'PASS' if ver['ok'] else 'FAIL'}"
        )
    print("-" * 80)
    print(f"Exported SFT JSONL: {jsonl_path}")
    print("==========================================================================")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
