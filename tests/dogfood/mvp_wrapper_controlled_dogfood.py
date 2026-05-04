#!/usr/bin/env python3
"""Controlled MVP-wrapper dogfood on copied graphs."""

from __future__ import annotations

import argparse
from collections import Counter
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

DATE = "2026-05-03"
DEFAULT_INTAKE = Path(f"reports/dogfood/mvp_wrapper_controlled_{DATE}.jsonl")
DEFAULT_REPORT = Path(f"reports/dogfood/MVP_WRAPPER_CONTROLLED_DOGFOOD_{DATE}.md")
WRAPPER_TOOLS = {"inspect_graph", "search_blocks", "search_help", "change_graph"}
TASK_TARGETS = {
    "inspect_graph": 25,
    "search_blocks": 25,
    "search_help": 15,
    "preview_change": 25,
    "commit_change": 25,
    "clarification": 10,
    "unsupported": 10,
}


@dataclass(frozen=True)
class Task:
    graph: GraphInfo
    task_group: str
    task_type: str
    prompt: str
    expected: str
    repeat_same_prompt: bool = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-observations", type=int, default=160)
    parser.add_argument("--max-graphs", type=int, default=30)
    parser.add_argument("--family-limit", type=int, default=6)
    parser.add_argument("--candidate-timeout-seconds", type=int, default=15)
    parser.add_argument("--intake-path", type=Path, default=DEFAULT_INTAKE)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--user-graphs-dir", type=Path, default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--server-url", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    if args.overwrite:
        args.intake_path.unlink(missing_ok=True)
        args.report_path.unlink(missing_ok=True)

    availability = discover_user_graphs(args.user_graphs_dir)
    if len(availability["graphs"]) >= 5:
        graphs = availability["graphs"]
        source = "user_graph"
        source_note = "Copied user/workspace graphs were available and used."
        skipped_counts: dict[str, int] = {}
    else:
        selection = select_graphs(
            max_graphs=args.max_graphs,
            family_limit=args.family_limit,
            candidate_timeout_seconds=args.candidate_timeout_seconds,
        )
        graphs = selection.graphs
        source = "installed_example"
        source_note = (
            "No sufficient copied user/workspace graph corpus (>=5) was explicitly "
            "available for this run; used copied installed examples only."
        )
        skipped_counts = selection.skipped_counts

    if not graphs:
        print("No graphs available for controlled MVP wrapper dogfood.")
        return 1

    tasks = build_controlled_tasks(graphs, max_observations=args.max_observations)
    _, model, client = ensure_llama_server(args.server_url, args.model)

    rows: list[dict[str, Any]] = []
    args.intake_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="grc-agent-mvp-wrapper-controlled-") as tmpdir:
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
                source=source,
            )
            rows.append(row)
            status = "PASS" if row["severity"] != "stop_the_line" else "STOP"
            print(f"[{index}/{len(tasks)}] {task.graph.relative_path} {task.task_group}: {status}")
            if row["severity"] == "stop_the_line":
                print("STOP_THE_LINE encountered; stopping controlled run.")
                break

    summary = summarize_dogfood_cases(intake_path=args.intake_path)
    write_report(
        report_path=args.report_path,
        intake_path=args.intake_path,
        rows=rows,
        summary=summary,
        selected_graphs=graphs,
        source=source,
        source_note=source_note,
        skipped_counts=skipped_counts,
        requested_observations=len(tasks),
        user_graph_count=len(availability["graphs"]),
        user_graph_dir=availability["searched_dir"],
    )
    return 1 if any(row["severity"] == "stop_the_line" for row in rows) else 0


def discover_user_graphs(user_graphs_dir: Path | None) -> dict[str, Any]:
    if user_graphs_dir is None:
        return {"graphs": [], "searched_dir": None}
    root = user_graphs_dir.expanduser().resolve()
    if not root.is_dir():
        return {"graphs": [], "searched_dir": str(root)}
    paths = sorted(path for path in root.rglob("*.grc") if path.is_file())
    graphs: list[GraphInfo] = []
    for path in paths:
        info = inspect_copied_graph(path, base_dir=root)
        if info is not None:
            graphs.append(info)
    return {"graphs": graphs, "searched_dir": str(root)}


def inspect_copied_graph(path: Path, *, base_dir: Path) -> GraphInfo | None:
    with tempfile.TemporaryDirectory(prefix="grc-agent-inspect-copied-") as tmpdir:
        copy_path = Path(tmpdir) / path.name
        shutil.copy2(path, copy_path)
        agent = GrcAgent()
        result = agent.execute_tool("load_grc", {"file_path": str(copy_path)})
        if not result.get("ok"):
            return None
        snapshot = graph_snapshot(agent)
        relative = str(path.relative_to(base_dir))
        variable_values = {
            str(name): str(value)
            for name, value in (snapshot.get("variable_values") or {}).items()
        }
        blocks = tuple(str(name) for name in snapshot.get("block_names", []) if name)
        blocks_by_name = snapshot.get("blocks_by_name") or {}
        block_types = {
            str(name): str(details.get("type", ""))
            for name, details in blocks_by_name.items()
            if isinstance(details, dict)
        }
        connections = tuple(str(item) for item in snapshot.get("connection_ids", []) if item)
        return GraphInfo(
            source_path=path,
            relative_path=relative,
            family=relative.split("/", 1)[0] if "/" in relative else "user",
            variables=tuple(variable_values.keys()),
            variable_values=variable_values,
            blocks=blocks,
            block_types=block_types,
            connections=connections,
        )


def build_controlled_tasks(graphs: list[GraphInfo], *, max_observations: int) -> list[Task]:
    buckets: dict[str, list[Task]] = {key: [] for key in TASK_TARGETS}
    for graph in graphs:
        buckets["inspect_graph"].extend(_inspect_tasks(graph))
        buckets["search_blocks"].extend(_search_blocks_tasks(graph))
        buckets["search_help"].extend(_search_help_tasks(graph))
        buckets["preview_change"].extend(_preview_tasks(graph))
        buckets["commit_change"].extend(_commit_tasks(graph))
        buckets["clarification"].extend(_clarification_tasks(graph))
        buckets["unsupported"].extend(_unsupported_tasks(graph))

    tasks: list[Task] = []
    for key, quota in TASK_TARGETS.items():
        tasks.extend(_sample_evenly(buckets[key], quota))
    return tasks[:max_observations]


def _sample_evenly(tasks: list[Task], quota: int) -> list[Task]:
    if quota <= 0 or not tasks:
        return []
    if len(tasks) <= quota:
        return list(tasks)
    if quota == 1:
        return [tasks[0]]
    selected: list[Task] = []
    used: set[int] = set()
    for offset in range(quota):
        index = round(offset * (len(tasks) - 1) / (quota - 1))
        if index in used:
            continue
        selected.append(tasks[index])
        used.add(index)
    cursor = 0
    while len(selected) < quota and cursor < len(tasks):
        if cursor not in used:
            selected.append(tasks[cursor])
            used.add(cursor)
        cursor += 1
    return selected


def _inspect_tasks(graph: GraphInfo) -> list[Task]:
    target = graph.blocks[0] if graph.blocks else "samp_rate"
    return [
        Task(
            graph=graph,
            task_group="inspect_graph",
            task_type="inspect",
            prompt="Summarize this graph.",
            expected="read-only summarize via inspect_graph",
        ),
        Task(
            graph=graph,
            task_group="inspect_graph",
            task_type="validate",
            prompt="Validate this graph.",
            expected="read-only validation via inspect_graph",
        ),
        Task(
            graph=graph,
            task_group="inspect_graph",
            task_type="inspect",
            prompt=f"Show context around {target}.",
            expected="read-only context via inspect_graph",
        ),
    ]


def _search_blocks_tasks(graph: GraphInfo) -> list[Task]:
    return [
        Task(
            graph=graph,
            task_group="search_blocks",
            task_type="retrieval",
            prompt="Search blocks for exact id blocks_throttle2.",
            expected="search_blocks exact match path",
        ),
        Task(
            graph=graph,
            task_group="search_blocks",
            task_type="retrieval",
            prompt="Find a block that limits sample rate of a stream.",
            expected="search_blocks conceptual semantic+lexical path",
        ),
        Task(
            graph=graph,
            task_group="search_blocks",
            task_type="retrieval",
            prompt="Find a block that limits sample rate of a stream.",
            expected="search_blocks repeated conceptual query should permit cache-hit behavior",
            repeat_same_prompt=True,
        ),
    ]


def _search_help_tasks(graph: GraphInfo) -> list[Task]:
    return [
        Task(
            graph=graph,
            task_group="search_help",
            task_type="retrieval",
            prompt="Explain GNU Radio stream tags briefly.",
            expected="search_help explanation-only output",
        ),
        Task(
            graph=graph,
            task_group="search_help",
            task_type="retrieval",
            prompt="Find docs help for rate limiting in GNU Radio.",
            expected="search_help explanation-only output",
        ),
    ]


def _preview_tasks(graph: GraphInfo) -> list[Task]:
    variable = _preferred_variable(graph)
    if variable:
        value = _new_variable_value(graph.variable_values.get(variable, "1"))
        return [
            Task(
                graph=graph,
                task_group="preview_change",
                task_type="preview",
                prompt=f"Preview changing {variable} to {value}. Do not apply.",
                expected="change_graph dry_run only; no mutation",
            ),
            Task(
                graph=graph,
                task_group="preview_change",
                task_type="preview",
                prompt=f"Draft an edit for {variable}={value}; do not commit.",
                expected="change_graph dry_run only; no mutation",
            ),
        ]
    if graph.blocks:
        name = graph.blocks[0]
        return [
            Task(
                graph=graph,
                task_group="preview_change",
                task_type="preview",
                prompt=f"Preview disabling {name}. Don't apply.",
                expected="change_graph dry_run only; no mutation",
            )
        ]
    return []


def _parse_connection(connection_id: str) -> dict[str, Any] | None:
    parts = connection_id.split("->", 1)
    if len(parts) != 2 or ":" not in parts[0] or ":" not in parts[1]:
        return None
    src_block, src_port = parts[0].rsplit(":", 1)
    dst_block, dst_port = parts[1].rsplit(":", 1)
    return {
        "src_block": src_block,
        "src_port": src_port,
        "dst_block": dst_block,
        "dst_port": dst_port,
    }


def _commit_tasks(graph: GraphInfo) -> list[Task]:
    tasks: list[Task] = []
    variable = _preferred_variable(graph)
    if variable:
        value = _new_variable_value(graph.variable_values.get(variable, "1"))
        tasks.append(
            Task(
                graph=graph,
                task_group="commit_change",
                task_type="param_edit",
                prompt=f"Set {variable} to {value} and validate.",
                expected="committed change_graph mutation with checkpoint on success",
            )
        )
    tasks.append(
        Task(
            graph=graph,
            task_group="commit_change",
            task_type="add_variable",
            prompt="Add variable mvp_wrapper_flag set to 1 and validate.",
            expected="committed change_graph variable mutation with checkpoint on success",
        )
    )
    if graph.blocks:
        block_name = graph.blocks[0]
        tasks.append(
            Task(
                graph=graph,
                task_group="commit_change",
                task_type="state_edit",
                prompt=f"Disable block {block_name} and validate.",
                expected="committed change_graph state edit with checkpoint on success",
            )
        )
    if graph.connections:
        conn = graph.connections[0]
        tasks.append(
            Task(
                graph=graph,
                task_group="commit_change",
                task_type="disconnect",
                prompt=f"Remove exact connection {conn}.",
                expected="exact disconnect via change_graph path",
            )
        )
        parsed_old = _parse_connection(conn)
        if parsed_old is not None:
            if len(graph.connections) >= 2:
                parsed_new = _parse_connection(graph.connections[1])
                if parsed_new is not None:
                    tasks.append(
                        Task(
                            graph=graph,
                            task_group="commit_change",
                            task_type="rewire",
                            prompt=(
                                f"Rewire exact connection {conn} to "
                                f"{parsed_old['src_block']}:{parsed_old['src_port']}->"
                                f"{parsed_new['dst_block']}:{parsed_new['dst_port']}."
                            ),
                            expected="exact rewire via change_graph path",
                        )
                    )
            tasks.append(
                Task(
                    graph=graph,
                    task_group="commit_change",
                    task_type="other",
                    prompt=f"Insert exact block blocks_throttle2 on connection {conn}.",
                    expected="exact insertion via change_graph path or safe rejection",
                )
            )
    return tasks


def _clarification_tasks(graph: GraphInfo) -> list[Task]:
    tasks = [
        Task(
            graph=graph,
            task_group="clarification",
            task_type="clarification",
            prompt="Insert a compatible filter somewhere in this graph.",
            expected="clarification for ambiguous placement",
        ),
        Task(
            graph=graph,
            task_group="clarification",
            task_type="clarification",
            prompt="Fix this graph and make it better.",
            expected="clarification-only; no mutation",
        ),
    ]
    if graph.blocks:
        tasks.append(
            Task(
                graph=graph,
                task_group="clarification",
                task_type="clarification",
                prompt=f"Change the duplicate block named {graph.blocks[0]}.",
                expected="clarify ambiguous target or refuse safely",
            )
        )
    return tasks


def _unsupported_tasks(graph: GraphInfo) -> list[Task]:
    return [
        Task(
            graph=graph,
            task_group="unsupported",
            task_type="negative",
            prompt="Edit raw .grc YAML source text directly.",
            expected="refuse unsupported request with no mutation",
        ),
        Task(
            graph=graph,
            task_group="unsupported",
            task_type="negative",
            prompt="Undo the last change.",
            expected="refuse unsupported request with no mutation",
        ),
        Task(
            graph=graph,
            task_group="unsupported",
            task_type="negative",
            prompt="Export this graph as Python code now.",
            expected="refuse unsupported request with no mutation",
        ),
    ]


def run_task(
    *,
    client: Any,
    model: str,
    task: Task,
    index: int,
    total: int,
    workspace: Path,
    intake_path: Path,
    source: str,
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
            source=source,
            actual="load_grc failed",
            tools=["load_grc"],
            delta={},
            validation="not reached",
            checkpoint_state="not reached",
            failure_category="tool_error",
            severity="medium",
            notes=f"case {index}/{total}; load failure",
            dispatch_rows=0,
            wrong_handler=False,
            cache_hits=0,
            cache_misses=0,
        )

    before = graph_snapshot(agent)
    history_start = len(agent.history)
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
        if task.repeat_same_prompt:
            _ = run_bounded_llama_turn(
                agent=agent,
                client=client,
                user_message=task.prompt,
                model=model,
                mvp_tool_profile=True,
                wrapper_eval_telemetry=True,
            )
            # Cacheable probes must be non-debug/non-enrich by contract.
            probe_query = "limit sample rate of stream"
            probe_k = 5
            cache_key = agent._search_blocks_cache_key(query=probe_query, k=probe_k)
            had_before = agent._search_blocks_cache_get(cache_key) is not None
            _ = agent.execute_tool(
                "search_blocks",
                {"query": probe_query, "k": probe_k},
            )
            has_after_first = agent._search_blocks_cache_get(cache_key) is not None
            _ = agent.execute_tool(
                "search_blocks",
                {"query": probe_query, "k": probe_k},
            )
            has_after_second = agent._search_blocks_cache_get(cache_key) is not None
            cache_probe_misses = 0 if had_before else 1
            cache_probe_hits = 1 if has_after_first and has_after_second else 0
            result.setdefault("_cache_probe_hits", 0)
            result.setdefault("_cache_probe_misses", 0)
            result["_cache_probe_hits"] += cache_probe_hits
            result["_cache_probe_misses"] += cache_probe_misses
    except Exception as exc:  # pragma: no cover
        result = {"ok": False, "assistant_text": ""}
        error = str(exc)

    after = graph_snapshot(agent)
    requested = requested_tool_calls_since(agent.history, history_start)
    executed = executed_tool_calls_since(agent.history, history_start)
    requested_names = [str(call.get("name")) for call in requested if call.get("name")]
    executed_names = [str(call.get("name")) for call in executed if call.get("name")]
    all_tools = list(dict.fromkeys(requested_names + executed_names))
    delta = graph_delta(before, after)
    changed = snapshot_changed(before, after)
    validation = str(after.get("validation_status"))

    dispatch_rows = 0
    wrong_handler = False
    internal_handlers: list[str] = []
    cache_hits = 0
    cache_misses = 0
    checkpoint_seen = False
    for call in executed:
        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            continue
        telemetry = arguments.get("dispatch_telemetry")
        if not isinstance(telemetry, dict):
            continue
        dispatch_rows += 1
        if str(telemetry.get("wrapper_name")) != str(call.get("name")):
            wrong_handler = True
        handlers = telemetry.get("internal_handler_called") or []
        if isinstance(handlers, list):
            internal_handlers.extend(str(item) for item in handlers)
            cache_hits += sum(1 for item in handlers if item == "search_blocks_cache(hit)")
            cache_misses += sum(1 for item in handlers if item == "search_blocks_cache(miss)")
        if str(call.get("name")) == "change_graph":
            if bool(arguments.get("ok")) and not bool(arguments.get("dry_run")):
                checkpoint_seen = checkpoint_seen or bool(arguments.get("checkpoint_id"))
    cache_hits += int(result.get("_cache_probe_hits", 0))
    cache_misses += int(result.get("_cache_probe_misses", 0))

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
    elif wrong_handler:
        failure_category = "routing_failure"
        severity = "stop_the_line"
        notes = "STOP_THE_LINE: wrapper dispatch telemetry mismatch"
    elif "save_graph" in executed_names:
        failure_category = "unsafe_mutation_risk"
        severity = "stop_the_line"
        notes = "STOP_THE_LINE: save_graph exposed in MVP path"
    elif task.task_group == "preview_change" and changed:
        failure_category = "unsafe_mutation_risk"
        severity = "stop_the_line"
        notes = "STOP_THE_LINE: preview mutated graph"
    elif task.task_group == "unsupported" and changed:
        failure_category = "unsafe_mutation_risk"
        severity = "stop_the_line"
        notes = "STOP_THE_LINE: unsupported request mutated graph"
    elif changed and validation == "invalid":
        failure_category = "unsafe_mutation_risk"
        severity = "stop_the_line"
        notes = "STOP_THE_LINE: invalid graph committed"
    elif task.task_group == "commit_change" and changed and not checkpoint_seen:
        failure_category = "save_reload_mismatch"
        severity = "stop_the_line"
        notes = "STOP_THE_LINE: committed change missing checkpoint_id"

    checkpoint_state = (
        "committed_with_checkpoint"
        if checkpoint_seen
        else ("not_committed" if not changed else "committed_missing_checkpoint")
    )
    actual = (
        f"ok={bool(result.get('ok'))}; tools={all_tools}; changed={changed}; "
        f"validation={validation}; cache_hits={cache_hits}; cache_misses={cache_misses}; "
        f"handlers={sorted(set(internal_handlers))[:6]}"
    )
    return _record(
        task=task,
        intake_path=intake_path,
        source=source,
        actual=actual,
        tools=all_tools,
        delta=delta,
        validation=validation,
        checkpoint_state=checkpoint_state,
        failure_category=failure_category,
        severity=severity,
        notes=notes,
        dispatch_rows=dispatch_rows,
        wrong_handler=wrong_handler,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        internal_handlers=internal_handlers,
    )


def _record(
    *,
    task: Task,
    intake_path: Path,
    source: str,
    actual: str,
    tools: list[str],
    delta: dict[str, Any],
    validation: str,
    checkpoint_state: str,
    failure_category: str,
    severity: str,
    notes: str,
    dispatch_rows: int,
    wrong_handler: bool,
    cache_hits: int,
    cache_misses: int,
    internal_handlers: list[str],
) -> dict[str, Any]:
    payload = record_dogfood_case(
        prompt=task.prompt,
        graph=str(task.graph.source_path),
        source=source,
        task_type=task.task_type,
        failure_category=failure_category,
        severity=severity,
        expected=task.expected,
        actual=actual,
        actual_tools=tools,
        graph_delta=json.dumps(delta, sort_keys=True),
        validation_state=validation,
        save_state=checkpoint_state,
        reproducible=True,
        notes=notes,
        intake_path=intake_path,
    )
    return {
        "graph": task.graph.relative_path,
        "family": task.graph.family,
        "task_group": task.task_group,
        "task_type": task.task_type,
        "failure_category": failure_category,
        "severity": severity,
        "actual_tools": tools,
        "dispatch_rows": dispatch_rows,
        "wrong_handler": wrong_handler,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "internal_handlers": sorted(set(str(item) for item in internal_handlers)),
        "checkpoint_state": checkpoint_state,
        "record_ok": bool(payload.get("ok")),
    }


def write_report(
    *,
    report_path: Path,
    intake_path: Path,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    selected_graphs: list[GraphInfo],
    source: str,
    source_note: str,
    skipped_counts: dict[str, int],
    requested_observations: int,
    user_graph_count: int,
    user_graph_dir: str | None,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    counts = summary.get("counts", {})
    by_task = counts.get("by_task_type", {})
    by_failure = counts.get("by_failure_category", {})
    by_severity = counts.get("by_severity", {})
    stop_count = int(by_severity.get("stop_the_line", 0))
    wrapper_usage: Counter[str] = Counter()
    internal_handler_usage: Counter[str] = Counter()
    for row in rows:
        for tool in row.get("actual_tools", []):
            wrapper_usage[str(tool)] += 1
        actual = row.get("actual_tools", [])
        _ = actual
    legacy_exposure = sum(
        1
        for row in rows
        if row["failure_category"] == "routing_failure" and row["severity"] == "stop_the_line"
    )
    wrong_handler_count = sum(1 for row in rows if bool(row.get("wrong_handler")))
    preview_mut = sum(
        1
        for row in rows
        if row["task_group"] == "preview_change" and row["severity"] == "stop_the_line"
    )
    unsupported_mut = sum(
        1
        for row in rows
        if row["task_group"] == "unsupported" and row["severity"] == "stop_the_line"
    )
    invalid_commit = sum(
        1
        for row in rows
        if row["severity"] == "stop_the_line"
        and row["failure_category"] in {"unsafe_mutation_risk", "save_reload_mismatch"}
    )
    checkpoint_missing = sum(
        1 for row in rows if row.get("checkpoint_state") == "committed_missing_checkpoint"
    )
    cache_hits = sum(int(row.get("cache_hits", 0)) for row in rows)
    cache_misses = sum(int(row.get("cache_misses", 0)) for row in rows)
    task_group_counts = Counter(str(row.get("task_group", "")) for row in rows)
    for row in rows:
        for handler in row.get("internal_handlers", []) or []:
            internal_handler_usage[str(handler)] += 1
    repeated_clusters = [
        cluster
        for cluster in summary.get("clusters", [])
        if cluster.get("recommendation") == "candidate_generic_gap"
    ]

    lines = [
        f"# MVP Wrapper Controlled Dogfood - {DATE}",
        "",
        source_note,
        "",
        "## Corpus Availability",
        "",
        f"- Copied user/workspace graphs discovered: {user_graph_count}",
        f"- User graph search directory: `{user_graph_dir}`",
        f"- Source used for this run: `{source}`",
        "",
        "## Scope",
        "",
        f"- Graphs selected: {len(selected_graphs)}",
        f"- Candidate skips: `{json.dumps(skipped_counts, sort_keys=True)}`",
        f"- Observations requested: {requested_observations}",
        f"- Observations recorded: {summary.get('total_records', len(rows))}",
        f"- Intake path: `{intake_path}`",
        "",
        "## Results",
        "",
        f"- Task distribution: `{json.dumps(by_task, sort_keys=True)}`",
        f"- Task-group distribution: `{json.dumps(dict(task_group_counts), sort_keys=True)}`",
        f"- Wrapper usage distribution: `{json.dumps(dict(wrapper_usage), sort_keys=True)}`",
        f"- Internal handler distribution (wrapper-level): `{json.dumps(dict(internal_handler_usage), sort_keys=True)}`",
        f"- Failure categories: `{json.dumps(by_failure, sort_keys=True)}`",
        f"- Severity counts: `{json.dumps(by_severity, sort_keys=True)}`",
        f"- Clarification count: {task_group_counts.get('clarification', 0)}",
        f"- Unsupported/refusal count: {task_group_counts.get('unsupported', 0)}",
        f"- STOP_THE_LINE count: {stop_count}",
        f"- Legacy tool exposure count: {legacy_exposure}",
        f"- Wrong internal handler count: {wrong_handler_count}",
        f"- Preview mutation count: {preview_mut}",
        f"- Unsupported mutation count: {unsupported_mut}",
        f"- Invalid commit/save count: {invalid_commit}",
        f"- Checkpoint missing after commit count: {checkpoint_missing}",
        f"- Search cache hits observed: {cache_hits}",
        f"- Search cache misses observed: {cache_misses}",
        f"- Repeated generic failure clusters: {len(repeated_clusters)}",
        "",
        "## Patch Decision",
        "",
        "- No patch justified." if stop_count == 0 and len(repeated_clusters) == 0 else
        "- Patch candidate(s) identified; review STOP_THE_LINE or repeated generic clusters before changes.",
        "",
        "## Acceptance Check",
        "",
        f"- Default MVP wrappers only: {'PASS' if legacy_exposure == 0 else 'FAIL'}",
        f"- Legacy exposure = 0: {'PASS' if legacy_exposure == 0 else 'FAIL'}",
        f"- Wrong handler = 0: {'PASS' if wrong_handler_count == 0 else 'FAIL'}",
        f"- Preview mutation = 0: {'PASS' if preview_mut == 0 else 'FAIL'}",
        f"- Unsupported mutation = 0: {'PASS' if unsupported_mut == 0 else 'FAIL'}",
        f"- Invalid commit = 0: {'PASS' if invalid_commit == 0 else 'FAIL'}",
        f"- Checkpoint correctness: {'PASS' if checkpoint_missing == 0 else 'FAIL'}",
        f"- No unresolved STOP_THE_LINE: {'PASS' if stop_count == 0 else 'FAIL'}",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
