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
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from experiments.atomic_dataset.harness_driver import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    run_harness_session,
    run_harness_turn,
)
from experiments.atomic_dataset.scenarios import (
    BENCHMARK_SCENARIOS,
    ScenarioSpec,
    build_tier1_scenarios,
    build_tier2_scenarios,
)
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


def _load_checkpoint(out_dir: Path, scenario_id: str) -> dict[str, Any] | None:
    """Load a previously written scenario record (checkpoint) if present."""
    p = out_dir / f"{scenario_id}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _checkpoint_done(rec: dict[str, Any] | None) -> bool:
    """A checkpoint counts as done when its verifier passed and the flowgraph is valid."""
    if rec is None:
        return False
    ver = rec.get("verification") or {}
    return bool(ver.get("ok")) and bool(rec.get("flowgraph_valid"))


def _write_crash_checkpoint(
    spec: ScenarioSpec,
    args: argparse.Namespace,
    exit_code: int,
    detail: str,
) -> dict[str, Any]:
    """Record a native child-process crash as a checkpoint.

    Crashes are RETRYABLE (not terminal): a C++ crash in the GRC layer depends on
    which actions the model takes, so a retry may well pass. The watchdog's
    stall-guard stops the campaign if a scenario crashes on every attempt.
    """
    rec: dict[str, Any] = {
        "scenario_id": spec.id,
        "prompt": spec.prompt,
        "category": spec.category,
        "name": spec.name,
        "provider": args.provider,
        "endpoint": args.endpoint,
        "model": args.model,
        "crash": True,
        "exit_code": exit_code,
        "crash_detail": detail[-2000:],
        "elapsed_seconds": 0.0,
        "flowgraph_valid": False,
        "flowgraph_validation": {"status": "unknown", "errors": [f"native crash (exit {exit_code})"]},
        "flowgraph_diff": {},
        "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "requests": 0},
        "approvals_granted": [],
        "guardrail_events": [],
        "tool_trajectory": [],
        "final_output": None,
        "verification": {"ok": False, "crash": True, "assistant_turns": 0, "reasoning_coverage": 0.0},
    }
    (args.out_dir / f"{spec.id}.json").write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
    return rec


def _export_jsonl(out_dir: Path, specs: list[ScenarioSpec], jsonl_name: str) -> int:
    """Rebuild the SFT JSONL from all completed checkpoint records, in suite order."""
    jsonl_path = out_dir / jsonl_name
    n = 0
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for spec in specs:
            rec = _load_checkpoint(out_dir, spec.id)
            if not _checkpoint_done(rec):
                continue
            sft_row = {
                "scenario_id": rec["scenario_id"],
                "category": rec.get("category", spec.category),
                "model": rec["model"],
                "flowgraph_valid": rec["flowgraph_valid"],
                "guardrail_events": rec["guardrail_events"],
                "usage": rec["usage"],
                "system": rec["system"],
                "tools": rec["tools"],
                "messages": rec["messages"],
                "canonical_messages": rec["canonical_messages"],
            }
            if "turns" in rec:
                sft_row["turns"] = rec["turns"]
            f.write(json.dumps(sft_row, default=str) + "\n")
            n += 1
    return n


def process_scenario(spec: ScenarioSpec, args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute one scenario through the real harness, verify, and write its checkpoint.

    Runs identically in-process (benchmark suite) or in a child process (tier1 suite,
    via --run-one) so a native C++ crash in the GRC layer kills only the child.
    """
    write_md = spec.family == "benchmark"  # benchmark: always; tier1/tier2: fail-only
    if spec.turns:
        rec = run_harness_session(
            scenario_id=spec.id,
            prompts=list(spec.turns),
            provider=args.provider,
            endpoint=args.endpoint,
            model_name=args.model,
            fixture_path=spec.fixture,
            max_tokens=args.max_tokens,
        )
    else:
        rec = run_harness_turn(
            scenario_id=spec.id,
            prompt=spec.prompt,
            provider=args.provider,
            endpoint=args.endpoint,
            model_name=args.model,
            fixture_path=spec.fixture,
            max_tokens=args.max_tokens,
        )
    rec["category"] = spec.category
    rec["name"] = spec.name

    ver = verify_trace_record(rec)
    rec["verification"] = ver

    tool_seq = [
        f"{s['tool_name']}({s['outcome']})"
        for s in rec["tool_trajectory"]
        if s.get("tool_name")
    ]
    print(f"     Completed in {rec['elapsed_seconds']:.2f}s | Requests: {rec['usage']['requests']}")
    print(f"     Tool Trajectory: {' -> '.join(tool_seq)}")
    print(f"     Guardrail Events: {len(rec['guardrail_events'])}")
    print(f"     Flowgraph Valid: {rec['flowgraph_valid']} | Verifier: {'PASS' if ver['ok'] else 'FAIL'}\n")

    # Write full JSON record (checkpoint) & Markdown inspection report
    (args.out_dir / f"{spec.id}.json").write_text(
        json.dumps(rec, indent=2, default=str), encoding="utf-8"
    )
    if write_md or not (ver["ok"] and rec["flowgraph_valid"]):
        (args.out_dir / f"{spec.id}.md").write_text(
            render_markdown_inspection_report(rec, ver), encoding="utf-8"
        )
    return rec, ver


def _run_one_child(spec: ScenarioSpec, args: argparse.Namespace, timeout_s: int = 1800) -> int:
    """Run one scenario in a child process; returns the child's exit code.

    0 = PASS, 1 = completed but failed verification (checkpoint written),
    other = killed/crashed (SIGSEGV=139, SIGABRT=134, SIGKILL=137, timeout=124).
    """
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=f"{repo_root}{os.pathsep}{os.environ.get('PYTHONPATH', '')}")
    cmd = [
        sys.executable,
        "-m",
        "experiments.atomic_dataset.run_scenarios",
        "--run-one-id", spec.id,
        "--run-one-prompt", spec.prompt,
        "--run-one-fixture", spec.fixture or "",
        "--run-one-category", spec.category,
        "--run-one-name", spec.name,
        "--run-one-family", spec.family,
        "--run-one-subcat", spec.subcategory,
        "--run-one-tools", ",".join(spec.expected_tools),
        "--run-one-turns", json.dumps(list(spec.turns)),
        "--run-one-expects-guardrail" if spec.expects_guardrail else "--run-one-no-expects-guardrail",
        "--provider", args.provider,
        "--endpoint", args.endpoint,
        "--model", args.model,
        "--max-tokens", str(args.max_tokens),
        "--out-dir", str(args.out_dir),
    ]
    # Multi-turn scenarios run 2-3x longer: give the child a bigger budget.
    if spec.turns:
        timeout_s = max(timeout_s, 3600)
    try:
        proc = subprocess.run(cmd, cwd=repo_root, env=env, timeout=timeout_s)
        return proc.returncode
    except subprocess.TimeoutExpired:
        return 124


def _run_one(args: argparse.Namespace) -> int:
    """Child-process mode: execute exactly one scenario (launched by _run_one_child)."""
    turns = tuple(json.loads(args.run_one_turns)) if args.run_one_turns else ()
    spec = ScenarioSpec(
        id=args.run_one_id,
        category=args.run_one_category,
        name=args.run_one_name,
        fixture=args.run_one_fixture or None,
        prompt=args.run_one_prompt,
        expected_tools=tuple(t for t in args.run_one_tools.split(",") if t),
        expects_guardrail=args.run_one_expects_guardrail,
        family=args.run_one_family,
        subcategory=args.run_one_subcat,
        turns=turns,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if not (spec.fixture is None or Path(spec.fixture).is_file()):
        print(f"     [ERROR] fixture missing: {spec.fixture}")
        return 1
    try:
        rec, ver = process_scenario(spec, args)
    except Exception as e:  # noqa: BLE001
        print(f"     [ERROR] scenario raised: {e!r}")
        return 1
    return 0 if (ver["ok"] and rec["flowgraph_valid"]) else 1


def _select_scenarios(all_specs: list[ScenarioSpec], wanted: str) -> list[ScenarioSpec] | None:
    """Resolve the --scenario filter; None if any requested id is unknown."""
    if wanted == "all":
        return list(all_specs)
    wanted_ids = {s.strip() for s in wanted.split(",") if s.strip()}
    selected = [s for s in all_specs if s.id in wanted_ids]
    if len(selected) != len(wanted_ids):
        unknown = wanted_ids - {s.id for s in all_specs}
        print(f"[ERROR] Unknown scenario(s): {sorted(unknown)}")
        return None
    return selected


def _select_suite(args: argparse.Namespace) -> tuple[list[ScenarioSpec], str, dict[str, Any] | None]:
    """Build the scenario suite selected by --suite and its SFT JSONL name."""
    if args.suite == "benchmark":
        return list(BENCHMARK_SCENARIOS), "sft_trajectories.jsonl", None
    if args.suite == "tier2":
        specs, manifest = build_tier2_scenarios()
        return specs, "tier2_sft_trajectories.jsonl", manifest
    specs, manifest = build_tier1_scenarios()
    return specs, "tier1_sft_trajectories.jsonl", manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run harness-driven one-shot GRC Agent scenarios and export SFT dataset."
    )
    parser.add_argument(
        "--suite",
        default="benchmark",
        choices=["benchmark", "tier1", "tier2"],
        help="Scenario suite (default: benchmark = the 5 smoke scenarios)",
    )
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        choices=["ollama_cloud", "openai_compatible"],
        help="LLM provider (default: ollama_cloud)",
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Base URL")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model ID (default: deepseek-v4.1-flash)",
    )
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
        help="Scenario ID(s) to run, comma-separated (or 'all')",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip scenarios whose checkpoint JSON already passed verification",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Cap on the number of NEWLY executed scenarios this run (0 = no cap)",
    )
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="Do not run anything; rebuild the SFT JSONL from existing checkpoints",
    )
    # Hidden child-process mode (launched by _run_one_child; not for manual use):
    parser.add_argument("--run-one-id", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--run-one-prompt", default="", help=argparse.SUPPRESS)
    parser.add_argument("--run-one-fixture", default="", help=argparse.SUPPRESS)
    parser.add_argument("--run-one-category", default="", help=argparse.SUPPRESS)
    parser.add_argument("--run-one-name", default="", help=argparse.SUPPRESS)
    parser.add_argument("--run-one-family", default="benchmark", help=argparse.SUPPRESS)
    parser.add_argument("--run-one-subcat", default="", help=argparse.SUPPRESS)
    parser.add_argument("--run-one-tools", default="", help=argparse.SUPPRESS)
    parser.add_argument("--run-one-turns", default="", help=argparse.SUPPRESS)
    parser.add_argument("--run-one-expects-guardrail", dest="run_one_expects_guardrail", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-one-no-expects-guardrail", dest="run_one_expects_guardrail", action="store_false", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.run_one_id:
        return _run_one(args)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    all_specs, jsonl_name, manifest = _select_suite(args)

    selected = _select_scenarios(all_specs, args.scenario)
    if selected is None:
        return 1

    if manifest is not None:
        manifest.update({
            "provider": args.provider,
            "endpoint": args.endpoint,
            "model": args.model,
            "max_tokens": args.max_tokens,
            "out_dir": str(args.out_dir),
            "jsonl": jsonl_name,
            "run_started_at": datetime.datetime.now().isoformat(),
        })
        (args.out_dir / f"{args.suite}_manifest.json").write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8"
        )

    print("==========================================================================")
    print(" GRC Agent Harness-Driven One-Shot Trajectory Collector")
    print("==========================================================================")
    print(f"Suite:      {args.suite}")
    print(f"Provider:   {args.provider}")
    print(f"Endpoint:   {args.endpoint}")
    print(f"Model:      {args.model}")
    print(f"Max Tokens: {args.max_tokens}")
    print(f"Scenarios:  {len(selected)}")
    print(f"Output Dir: {args.out_dir}")
    print(f"SFT JSONL:  {args.out_dir / jsonl_name}")
    print("==========================================================================\n")

    if args.export_only:
        n = _export_jsonl(args.out_dir, selected, jsonl_name)
        print(f"Export-only: {n}/{len(selected)} completed checkpoints -> {args.out_dir / jsonl_name}")
        return 0

    records: list[dict[str, Any]] = []
    verifications: list[dict[str, Any]] = []
    all_passed = True
    new_runs = 0

    for spec in selected:
        if args.resume:
            rec = _load_checkpoint(args.out_dir, spec.id)
            if _checkpoint_done(rec):
                print(f"---> [{spec.id}] checkpoint PASS (resumed, not re-run)")
                records.append(rec)
                verifications.append({"scenario_id": spec.id, **rec["verification"]})
                continue

        print(f"---> Running [{spec.id}] {spec.name}")
        if spec.turns:
            for i, p in enumerate(spec.turns, 1):
                print(f"     Turn {i}: {p}")
        else:
            print(f"     Prompt: {spec.prompt}")
        # Each scenario runs in an isolated child process: a native C++ crash in the
        # GRC layer (e.g. an uncatchable pybind exception) kills only the child, and
        # the parent records a terminal crash checkpoint instead of dying with it.
        rc = _run_one_child(spec, args)
        if rc in (0, 1):
            rec = _load_checkpoint(args.out_dir, spec.id)
            if rec is None:
                # Child failed before writing its checkpoint (exception before the
                # run); count as failure — the watchdog retries it on the next attempt.
                all_passed = False
                continue
            ver = rec["verification"]
            if not ver["ok"] or not rec["flowgraph_valid"]:
                all_passed = False
            records.append(rec)
            verifications.append({"scenario_id": spec.id, **ver})
        else:
            all_passed = False
            rec = _write_crash_checkpoint(spec, args, rc, f"child exit code {rc}")
            ver = rec["verification"]
            print(f"     [CRASH] child exited {rc}; crash checkpoint recorded, moving on\n")
            records.append(rec)
            verifications.append({"scenario_id": spec.id, **ver})
        new_runs += 1
        if args.limit and new_runs >= args.limit:
            print("\n---> --limit reached; stopping before suite completion.")
            break

    # Export Unsloth-ready SFT JSONL from all completed checkpoints (in suite order)
    n_exported = _export_jsonl(args.out_dir, selected, jsonl_name)

    print("==========================================================================")
    print(" Summary of Collected One-Shot Harness Trajectories")
    print("==========================================================================")
    print(f"{'Scenario ID':<40} {'Time':<8} {'Reqs':<6} {'Guardrails':<12} {'FG Valid':<10} {'Audit'}")
    print("-" * 90)
    for rec, ver in zip(records, verifications, strict=True):
        print(
            f"{rec['scenario_id']:<40} "
            f"{rec['elapsed_seconds']:<7.2f}s "
            f"{rec['usage']['requests']:<6} "
            f"{len(rec['guardrail_events']):<12} "
            f"{str(rec['flowgraph_valid']):<10} "
            f"{'PASS' if ver['ok'] else 'FAIL'}"
        )
    print("-" * 90)
    print(f"Newly executed: {new_runs} | Exported rows: {n_exported}")
    print(f"Exported SFT JSONL: {args.out_dir / jsonl_name}")
    print("==========================================================================")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
