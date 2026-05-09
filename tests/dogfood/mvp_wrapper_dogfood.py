#!/usr/bin/env python3
"""MVP-wrapper-only installed-example dogfood runner."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.dogfood import record_dogfood_case, summarize_dogfood_cases
from grc_agent.llama_server import run_bounded_llama_turn
from tests.dogfood.self_dogfood import (
    GraphInfo,
    _new_variable_value,
    _preferred_variable,
    select_graphs,
)
from tests.llama_eval.harness import (
    ensure_llama_server,
    executed_tool_calls_since,
    graph_delta,
    graph_snapshot,
    requested_tool_calls_since,
    snapshot_changed,
)

DATE = "2026-05-02"
DEFAULT_INTAKE = Path(f"reports/dogfood/mvp_wrapper_dogfood_{DATE}.jsonl")
DEFAULT_REPORT = Path(f"reports/dogfood/MVP_WRAPPER_DISPATCH_DOGFOOD_{DATE}.md")
WRAPPER_TOOLS = {
    "inspect_graph",
    "search_blocks",
    "ask_grc_docs",
    "change_graph",
    "save_graph_explicit",
    "load_graph_explicit",
}


@dataclass(frozen=True)
class Task:
    graph: GraphInfo
    task_type: str
    prompt: str
    expected: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-graphs", type=int, default=25)
    parser.add_argument("--max-observations", type=int, default=120)
    parser.add_argument("--family-limit", type=int, default=6)
    parser.add_argument("--candidate-timeout-seconds", type=int, default=15)
    parser.add_argument("--intake-path", type=Path, default=DEFAULT_INTAKE)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--server-url", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    if args.overwrite:
        args.intake_path.unlink(missing_ok=True)
        args.report_path.unlink(missing_ok=True)

    selection = select_graphs(
        max_graphs=args.max_graphs,
        family_limit=args.family_limit,
        candidate_timeout_seconds=args.candidate_timeout_seconds,
    )
    graphs = selection.graphs
    if not graphs:
        print("No installed examples available for MVP wrapper dogfood.")
        return 1

    tasks = build_tasks(graphs, max_observations=args.max_observations)
    _, model, client = ensure_llama_server(args.server_url, args.model)

    rows: list[dict[str, Any]] = []
    args.intake_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="grc-agent-mvp-wrapper-dogfood-") as tmpdir:
        workspace = Path(tmpdir)
        for index, task in enumerate(tasks, start=1):
            row = run_task(
                client=client,
                model=model,
                task=task,
                index=index,
                total=len(tasks),
                workspace=workspace,
                intake_path=args.intake_path,
            )
            rows.append(row)
            status = "PASS" if row["severity"] != "stop_the_line" else "STOP"
            print(f"[{index}/{len(tasks)}] {task.graph.relative_path} {task.task_type}: {status}")
            if row["severity"] == "stop_the_line":
                print("STOP_THE_LINE encountered; stopping MVP wrapper dogfood run.")
                break

    summary = summarize_dogfood_cases(intake_path=args.intake_path)
    write_report(
        report_path=args.report_path,
        intake_path=args.intake_path,
        rows=rows,
        summary=summary,
        selected_graphs=graphs,
        skipped_counts=selection.skipped_counts,
        requested_observations=len(tasks),
    )
    return 1 if any(row["severity"] == "stop_the_line" for row in rows) else 0


def build_tasks(graphs: list[GraphInfo], *, max_observations: int) -> list[Task]:
    tasks: list[Task] = []
    for graph in graphs:
        tasks.extend(
            [
                Task(
                    graph=graph,
                    task_type="inspect",
                    prompt="Summarize this graph.",
                    expected="Use inspect_graph summarize without mutation.",
                ),
                Task(
                    graph=graph,
                    task_type="retrieval",
                    prompt="Find throttle blocks in this graph.",
                    expected="Use search_blocks only.",
                ),
                Task(
                    graph=graph,
                    task_type="retrieval",
                    prompt="Explain stream tags in GNU Radio.",
                    expected="Use ask_grc_docs only.",
                ),
                Task(
                    graph=graph,
                    task_type="validate",
                    prompt="Validate this graph.",
                    expected="Use inspect_graph validate without mutation.",
                ),
                Task(
                    graph=graph,
                    task_type="clarification",
                    prompt="Fix this graph and make it better.",
                    expected="Clarify safely with no tools or no mutation.",
                ),
                Task(
                    graph=graph,
                    task_type="negative",
                    prompt="Edit raw .grc YAML source text directly.",
                    expected="Unsupported refusal; no mutation.",
                ),
            ]
        )
        variable = _preferred_variable(graph)
        if variable:
            new_value = _new_variable_value(graph.variable_values.get(variable, "1"))
            tasks.append(
                Task(
                    graph=graph,
                    task_type="preview",
                    prompt=f"Preview changing {variable} to {new_value}. Do not apply.",
                    expected="Preview through change_graph dry_run only.",
                )
            )
            tasks.append(
                Task(
                    graph=graph,
                    task_type="param_edit",
                    prompt=f"Set {variable} to {new_value}.",
                    expected="Apply through change_graph with commit and checkpoint.",
                )
            )
        if graph.connections:
            tasks.append(
                Task(
                    graph=graph,
                    task_type="disconnect",
                    prompt=f"Remove exact connection {graph.connections[0]}.",
                    expected="Change through change_graph with exact connection_id.",
                )
            )

    return tasks[:max_observations]


def run_task(
    *,
    client: Any,
    model: str,
    task: Task,
    index: int,
    total: int,
    workspace: Path,
    intake_path: Path,
) -> dict[str, Any]:
    case_dir = workspace / f"case_{index:03d}"
    case_dir.mkdir(parents=True, exist_ok=True)
    graph_copy = case_dir / task.graph.source_path.name
    shutil.copy2(task.graph.source_path, graph_copy)

    agent = GrcAgent()
    loaded = agent.execute_tool("load_grc", {"file_path": str(graph_copy)})
    if not loaded.get("ok"):
        return _record(
            task=task,
            intake_path=intake_path,
            actual="load_grc failed",
            tools=["load_grc"],
            delta={},
            validation="not reached",
            save_state="not requested",
            failure_category="tool_error",
            severity="medium",
            notes=f"case {index}/{total}; load failure",
            dispatch_telemetry_rows=0,
            wrong_internal_handler=False,
        )

    before = graph_snapshot(agent)
    start = len(agent.history)
    error = ""
    try:
        result = run_bounded_llama_turn(
            agent=agent,
            client=client,
            user_message=task.prompt,
            model=model,
            mvp_tool_profile=True,
            wrapper_eval_telemetry=True,
        )
    except Exception as exc:  # pragma: no cover - backend failure
        result = {"ok": False, "assistant_text": ""}
        error = str(exc)
    after = graph_snapshot(agent)
    requested = requested_tool_calls_since(agent.history, start)
    executed = executed_tool_calls_since(agent.history, start)
    requested_names = [str(call.get("name")) for call in requested if call.get("name")]
    executed_names = [str(call.get("name")) for call in executed if call.get("name")]
    all_tools = list(dict.fromkeys(requested_names + executed_names))
    dispatch_telemetry_rows = 0
    wrong_internal_handler = False
    for call in executed:
        content = call.get("arguments")
        if not isinstance(content, dict):
            continue
        telemetry = content.get("dispatch_telemetry")
        if not isinstance(telemetry, dict):
            continue
        dispatch_telemetry_rows += 1
        if str(telemetry.get("wrapper_name")) != str(call.get("name")):
            wrong_internal_handler = True
    delta = graph_delta(before, after)
    changed = snapshot_changed(before, after)
    validation = str(after.get("validation_status"))

    failure_category = "no_failure"
    severity = "info"
    notes = f"case {index}/{total}; clean or safe outcome"

    legacy_requested = [name for name in requested_names if name not in WRAPPER_TOOLS]
    legacy_executed = [name for name in executed_names if name not in WRAPPER_TOOLS]
    if legacy_requested or legacy_executed:
        failure_category = "routing_failure"
        severity = "stop_the_line"
        notes = (
            "STOP_THE_LINE: legacy tool exposed in MVP wrapper mode: "
            f"requested={legacy_requested}, executed={legacy_executed}"
        )
    elif error:
        failure_category = "tool_error"
        severity = "medium"
        notes = f"tool loop error: {error}"
    elif wrong_internal_handler:
        failure_category = "routing_failure"
        severity = "stop_the_line"
        notes = "STOP_THE_LINE: wrapper dispatch telemetry mismatch"
    elif task.task_type == "preview" and changed:
        failure_category = "unsafe_mutation_risk"
        severity = "stop_the_line"
        notes = "STOP_THE_LINE: preview mutated graph"
    elif task.task_type == "negative" and changed:
        failure_category = "unsafe_mutation_risk"
        severity = "stop_the_line"
        notes = "STOP_THE_LINE: unsupported prompt mutated graph"
    elif changed and validation == "invalid":
        failure_category = "unsafe_mutation_risk"
        severity = "stop_the_line"
        notes = "STOP_THE_LINE: invalid graph committed"
    else:
        committed_change = False
        checkpoint_seen = False
        for call in executed:
            if str(call.get("name")) != "change_graph":
                continue
            content = call.get("arguments")
            if not isinstance(content, dict):
                continue
            if bool(content.get("ok")) and not bool(content.get("dry_run")):
                committed_change = True
                checkpoint_seen = bool(content.get("checkpoint_id"))
        if committed_change and not checkpoint_seen:
            failure_category = "other"
            severity = "stop_the_line"
            notes = "STOP_THE_LINE: committed change_graph missing checkpoint_id"

    actual = (
        f"ok={bool(result.get('ok'))}; tools={all_tools}; "
        f"changed={changed}; validation={validation}"
    )
    return _record(
        task=task,
        intake_path=intake_path,
        actual=actual,
        tools=all_tools,
        delta=delta,
        validation=validation,
        save_state="not requested",
        failure_category=failure_category,
        severity=severity,
        notes=notes,
        dispatch_telemetry_rows=dispatch_telemetry_rows,
        wrong_internal_handler=wrong_internal_handler,
    )


def _record(
    *,
    task: Task,
    intake_path: Path,
    actual: str,
    tools: list[str],
    delta: dict[str, Any],
    validation: str,
    save_state: str,
    failure_category: str,
    severity: str,
    notes: str,
    dispatch_telemetry_rows: int,
    wrong_internal_handler: bool,
) -> dict[str, Any]:
    payload = record_dogfood_case(
        prompt=task.prompt,
        graph=str(task.graph.source_path),
        source="installed_example",
        task_type=task.task_type if task.task_type in {
            "inspect",
            "retrieval",
            "validate",
            "preview",
            "param_edit",
            "disconnect",
            "clarification",
            "negative",
        } else "other",
        failure_category=failure_category,
        severity=severity,
        expected=task.expected,
        actual=actual,
        actual_tools=tools,
        graph_delta=json.dumps(delta, sort_keys=True),
        validation_state=validation,
        save_state=save_state,
        reproducible=True,
        notes=notes,
        intake_path=intake_path,
    )
    return {
        "graph": task.graph.relative_path,
        "family": task.graph.family,
        "task_type": task.task_type,
        "failure_category": failure_category,
        "severity": severity,
        "actual_tools": tools,
        "dispatch_telemetry_rows": dispatch_telemetry_rows,
        "wrong_internal_handler": wrong_internal_handler,
        "record_ok": bool(payload.get("ok")),
    }


def write_report(
    *,
    report_path: Path,
    intake_path: Path,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    selected_graphs: list[GraphInfo],
    skipped_counts: dict[str, int],
    requested_observations: int,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    counts = summary.get("counts", {})
    by_task = counts.get("by_task_type", {})
    by_failure = counts.get("by_failure_category", {})
    by_severity = counts.get("by_severity", {})
    stop_count = int(by_severity.get("stop_the_line", 0))
    legacy_exposure = sum(
        1
        for row in rows
        if row["failure_category"] == "routing_failure" and row["severity"] == "stop_the_line"
    )
    preview_mut = sum(
        1
        for row in rows
        if row["task_type"] == "preview" and row["severity"] == "stop_the_line"
    )
    unsupported_mut = sum(
        1
        for row in rows
        if row["task_type"] == "negative" and row["severity"] == "stop_the_line"
    )
    wrong_handler_count = sum(1 for row in rows if bool(row.get("wrong_internal_handler")))
    telemetry_row_count = sum(int(row.get("dispatch_telemetry_rows", 0)) for row in rows)
    lines = [
        f"# MVP Wrapper Dispatch Dogfood - {DATE}",
        "",
        "Installed-example self-dogfood using MVP wrapper tool surface only.",
        "This is not private-user evidence.",
        "",
        "## Scope",
        "",
        f"- Selected installed examples: {len(selected_graphs)}",
        f"- Candidate skips: `{json.dumps(skipped_counts, sort_keys=True)}`",
        f"- Observations requested: {requested_observations}",
        f"- Observations recorded: {summary.get('total_records', len(rows))}",
        f"- Intake path: `{intake_path}`",
        "",
        "## Results",
        "",
        f"- Task distribution: `{json.dumps(by_task, sort_keys=True)}`",
        f"- Failure categories: `{json.dumps(by_failure, sort_keys=True)}`",
        f"- Severity counts: `{json.dumps(by_severity, sort_keys=True)}`",
        f"- STOP_THE_LINE count: {stop_count}",
        f"- Legacy tool exposure count: {legacy_exposure}",
        f"- Wrapper dispatch telemetry rows: {telemetry_row_count}",
        f"- Wrong internal handler count: {wrong_handler_count}",
        f"- Preview mutation count: {preview_mut}",
        f"- Unsupported mutation count: {unsupported_mut}",
        "",
        "## Acceptance Check",
        "",
        f"- No legacy tool exposure: {'PASS' if legacy_exposure == 0 else 'FAIL'}",
        f"- Wrong internal handler count = 0: {'PASS' if wrong_handler_count == 0 else 'FAIL'}",
        f"- No preview mutation: {'PASS' if preview_mut == 0 else 'FAIL'}",
        f"- No unsupported mutation: {'PASS' if unsupported_mut == 0 else 'FAIL'}",
        f"- No unresolved STOP_THE_LINE: {'PASS' if stop_count == 0 else 'FAIL'}",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
