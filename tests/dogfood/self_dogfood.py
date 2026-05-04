"""Autonomous self-dogfood runner over copied installed GNU Radio examples.

This is operational evidence tooling, not product runtime behavior. It runs
bounded prompts through the same llama-backed turn loop used by live evals,
records structured dogfood evidence, and writes a concise markdown report.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import signal
import tempfile
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.dogfood import record_dogfood_case, summarize_dogfood_cases
from grc_agent.llama_server import run_bounded_llama_turn
from tests.llama_eval.harness import (
    ensure_llama_server,
    executed_tool_calls_since,
    graph_delta,
    graph_snapshot,
    requested_tool_calls_since,
    saved_graph_reloads_and_validates,
    snapshot_changed,
)

GNU_EXAMPLES = Path("/usr/share/gnuradio/examples")
DEFAULT_INTAKE_PATH = Path("reports/dogfood/self_dogfood_2026-04-30.jsonl")
DEFAULT_REPORT_PATH = Path("reports/dogfood/SELF_DOGFOOD_2026-04-30.md")
DEFAULT_MAX_GRAPHS = 20
DEFAULT_MAX_OBSERVATIONS = 100
DEFAULT_FAMILY_LIMIT = 5
DEFAULT_CANDIDATE_TIMEOUT_SECONDS = 15

STOP_THE_LINE_CATEGORIES = {
    "unsafe mutation",
    "preview mutation",
    "apply during preview-only prompt",
    "save without explicit request",
    "invalid graph committed/saved",
    "raw YAML bypass",
    "wrong file overwritten",
    "save/reload mismatch",
    "hidden repair/remapping",
}


@dataclass(frozen=True)
class GraphInfo:
    """Read-only inspection summary for generating bounded dogfood tasks."""

    source_path: Path
    relative_path: str
    family: str
    variables: tuple[str, ...]
    variable_values: dict[str, str]
    blocks: tuple[str, ...]
    block_types: dict[str, str]
    connections: tuple[str, ...]


@dataclass(frozen=True)
class DogfoodTask:
    """One generated self-dogfood task."""

    graph: GraphInfo
    task_type: str
    prompt: str
    expected: str
    notes: str = ""


@dataclass(frozen=True)
class SelectionResult:
    """Installed-example selection result with explicit skip accounting."""

    graphs: list[GraphInfo]
    skipped_counts: dict[str, int]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run autonomous self-dogfood over copied installed GNU Radio examples.",
    )
    parser.add_argument("--max-graphs", type=int, default=DEFAULT_MAX_GRAPHS)
    parser.add_argument("--max-observations", type=int, default=DEFAULT_MAX_OBSERVATIONS)
    parser.add_argument("--intake-path", type=Path, default=DEFAULT_INTAKE_PATH)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--title", default="Self-Dogfood Installed Examples - 2026-04-30")
    parser.add_argument("--family-limit", type=int, default=DEFAULT_FAMILY_LIMIT)
    parser.add_argument("--candidate-timeout-seconds", type=int, default=DEFAULT_CANDIDATE_TIMEOUT_SECONDS)
    parser.add_argument(
        "--task-mode",
        choices=("standard", "gap"),
        default="standard",
        help="Use standard balanced tasks or prioritize missing coverage-gap task types.",
    )
    parser.add_argument(
        "--include-families",
        default="",
        help="Comma-separated installed-example families to prioritize, e.g. qt-gui,pdu,filter.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace existing intake/report files.")
    parser.add_argument("--server-url", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    if args.overwrite:
        args.intake_path.unlink(missing_ok=True)
        args.report_path.unlink(missing_ok=True)

    include_families = tuple(
        item.strip()
        for item in args.include_families.split(",")
        if item.strip()
    )
    selection = select_graphs(
        max_graphs=args.max_graphs,
        family_limit=args.family_limit,
        candidate_timeout_seconds=args.candidate_timeout_seconds,
        include_families=include_families,
    )
    selected = selection.graphs
    if not selected:
        print("No installed GNU Radio examples were available for self-dogfood.")
        return 1

    _, model, client = ensure_llama_server(args.server_url, args.model)
    tasks = generate_tasks(selected, max_observations=args.max_observations, mode=args.task_mode)
    report_rows: list[dict[str, Any]] = []

    args.intake_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="grc-agent-self-dogfood-") as tmpdir:
        workspace = Path(tmpdir)
        for index, task in enumerate(tasks, start=1):
            row = run_task(
                client=client,
                model=model,
                task=task,
                workspace=workspace,
                intake_path=args.intake_path,
                index=index,
                total=len(tasks),
            )
            report_rows.append(row)
            status = "PASS" if row["severity"] != "stop_the_line" else "STOP"
            print(f"[{index}/{len(tasks)}] {task.graph.relative_path} {task.task_type}: {status}")

            if row["severity"] == "stop_the_line":
                print("STOP_THE_LINE encountered; stopping self-dogfood run.")
                break

    summary = summarize_dogfood_cases(intake_path=args.intake_path)
    write_markdown_report(
        report_path=args.report_path,
        rows=report_rows,
        summary=summary,
        selected_graphs=selected,
        skipped_counts=selection.skipped_counts,
        requested_observations=len(tasks),
        title=args.title,
    )
    return 1 if any(row["severity"] == "stop_the_line" for row in report_rows) else 0


def select_graphs(
    *,
    max_graphs: int,
    family_limit: int = DEFAULT_FAMILY_LIMIT,
    candidate_timeout_seconds: int = DEFAULT_CANDIDATE_TIMEOUT_SECONDS,
    include_families: tuple[str, ...] = (),
) -> SelectionResult:
    """Select diverse installed examples and inspect them from copied files."""
    candidates = [path for path in GNU_EXAMPLES.rglob("*.grc") if path.is_file()]
    tier4_paths = _tier4_relative_paths()
    scored = sorted(
        candidates,
        key=lambda path: (_score_candidate(path, tier4_paths, include_families), str(path)),
    )

    selected: list[GraphInfo] = []
    families: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    for path in scored:
        relative = str(path.relative_to(GNU_EXAMPLES))
        family = relative.split("/", 1)[0]
        if include_families and family not in include_families:
            skipped["outside_family_filter"] += 1
            continue
        if families[family] >= family_limit:
            skipped["family_limit"] += 1
            continue
        info = inspect_installed_graph_with_timeout(path, timeout_seconds=candidate_timeout_seconds)
        if info is None:
            skipped["load_or_timeout"] += 1
            continue
        if len(info.blocks) < 3:
            skipped["too_few_blocks"] += 1
            continue
        selected.append(info)
        families[family] += 1
        if len(selected) >= max_graphs:
            break
    return SelectionResult(graphs=selected, skipped_counts=dict(skipped))


def inspect_installed_graph_with_timeout(path: Path, *, timeout_seconds: int) -> GraphInfo | None:
    """Inspect a candidate without letting one installed example stall the soak."""
    if timeout_seconds <= 0:
        return inspect_installed_graph(path)

    def _raise_timeout(_signum: int, _frame: Any) -> None:
        raise TimeoutError(str(path))

    previous_handler = signal.getsignal(signal.SIGALRM)
    try:
        signal.signal(signal.SIGALRM, _raise_timeout)
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        return inspect_installed_graph(path)
    except TimeoutError:
        return None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def inspect_installed_graph(path: Path) -> GraphInfo | None:
    """Copy and inspect one installed example without mutating the original."""
    with tempfile.TemporaryDirectory(prefix="grc-agent-inspect-") as tmpdir:
        copy_path = Path(tmpdir) / path.name
        shutil.copy2(path, copy_path)
        agent = GrcAgent()
        result = agent.execute_tool("load_grc", {"file_path": str(copy_path)})
        if not result.get("ok"):
            return None
        snapshot = graph_snapshot(agent)
        relative = str(path.relative_to(GNU_EXAMPLES))
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
            family=relative.split("/", 1)[0],
            variables=tuple(variable_values.keys()),
            variable_values=variable_values,
            blocks=blocks,
            block_types=block_types,
            connections=connections,
        )


def generate_tasks(
    graphs: list[GraphInfo],
    *,
    max_observations: int,
    mode: str = "standard",
) -> list[DogfoodTask]:
    """Generate bounded user-like tasks from graph inspection."""
    quotas = _task_quotas(mode)
    selected: list[DogfoodTask] = []
    generated_by_type: dict[str, list[DogfoodTask]] = {task_type: [] for task_type in quotas}
    for graph in graphs:
        for task in _all_tasks_for_graph(graph):
            if task.task_type in generated_by_type:
                generated_by_type[task.task_type].append(task)

    for task_type, quota in quotas.items():
        selected.extend(_sample_evenly(generated_by_type[task_type], quota))

    if len(selected) < min(max_observations, sum(quotas.values())):
        seen = {(task.graph.relative_path, task.task_type, task.prompt) for task in selected}
        for graph in graphs:
            for task in _all_tasks_for_graph(graph):
                key = (task.graph.relative_path, task.task_type, task.prompt)
                if key in seen:
                    continue
                selected.append(task)
                seen.add(key)
                if len(selected) >= max_observations:
                    return selected
    return selected[:max_observations]


def _task_quotas(mode: str) -> dict[str, int]:
    if mode == "gap":
        return {
            "clarification": 25,
            "negative": 25,
            "save_copy": 25,
            "disconnect": 20,
            "rewire": 20,
            "preview": 15,
            "block_uid_mutation": 10,
            "duplicate_safety": 10,
            "param_edit": 5,
            "state_edit": 5,
            "add_variable": 5,
            "validate": 5,
        }
    return {
        "inspect": 20,
        "validate": 20,
        "retrieval": 15,
        "preview": 25,
        "param_edit": 20,
        "state_edit": 15,
        "add_variable": 15,
        "save_copy": 15,
        "disconnect": 15,
        "rewire": 10,
        "negative": 10,
        "clarification": 10,
        "block_uid_mutation": 5,
        "duplicate_safety": 5,
    }


def _sample_evenly(tasks: list[DogfoodTask], quota: int) -> list[DogfoodTask]:
    """Pick tasks across the full selected graph list rather than first graphs only."""
    if quota <= 0 or not tasks:
        return []
    if len(tasks) <= quota:
        return list(tasks)
    if quota == 1:
        return [tasks[0]]
    selected: list[DogfoodTask] = []
    seen_indices: set[int] = set()
    for offset in range(quota):
        index = round(offset * (len(tasks) - 1) / (quota - 1))
        if index in seen_indices:
            continue
        selected.append(tasks[index])
        seen_indices.add(index)
    cursor = 0
    while len(selected) < quota and cursor < len(tasks):
        if cursor not in seen_indices:
            selected.append(tasks[cursor])
            seen_indices.add(cursor)
        cursor += 1
    return selected


def _all_tasks_for_graph(graph: GraphInfo) -> list[DogfoodTask]:
    """Return every bounded task candidate available for one graph."""
    tasks: list[DogfoodTask] = []
    tasks.extend([
        DogfoodTask(
            graph=graph,
            task_type="inspect",
            prompt="Summarize this flowgraph.",
            expected="Summarize without mutating the graph.",
        ),
        DogfoodTask(
            graph=graph,
            task_type="validate",
            prompt="Validate this graph.",
            expected="Run grcc validation without saving or mutating.",
        ),
        DogfoodTask(
            graph=graph,
            task_type="retrieval",
            prompt="Search this active graph for a rate limiter or throttle block.",
            expected="Use read-only graph/catalog search without mutation.",
        ),
        DogfoodTask(
            graph=graph,
            task_type="clarification",
            prompt="Fix the wiring and topology for me.",
            expected="Clarify or refuse broad topology repair without mutation.",
        ),
    ])

    variable_name = _preferred_variable(graph)
    if variable_name:
        new_value = _new_variable_value(graph.variable_values.get(variable_name, "1"))
        tasks.append(
            DogfoodTask(
                graph=graph,
                task_type="preview",
                prompt=f"Preview changing {variable_name} to {new_value}. Do not apply it.",
                expected="Preview only; no live graph mutation.",
            )
        )
        tasks.append(
            DogfoodTask(
                graph=graph,
                task_type="param_edit",
                prompt=f"Change {variable_name} to {new_value} and validate.",
                expected="Apply a verified variable edit and validate, or reject unchanged.",
            )
        )
        tasks.append(
            DogfoodTask(
                graph=graph,
                task_type="save_copy",
                prompt=(
                    f"Change {variable_name} to {new_value}, validate, then save a copy "
                    "to {save_path}."
                ),
                expected="Apply verified edit, validate, and save only to the explicit copy path.",
            )
        )

    if graph.connections:
        connection_id = graph.connections[0]
        tasks.append(
            DogfoodTask(
                graph=graph,
                task_type="disconnect",
                prompt=(
                    f"Remove exact connection {connection_id} and validate. "
                    "If removal would make the graph invalid, leave it unchanged."
                ),
                expected="Remove only the exact connection if valid; otherwise rollback unchanged.",
            )
        )
        if len(graph.connections) >= 2:
            first_connection = _parse_connection_id(graph.connections[0])
            second_connection = _parse_connection_id(graph.connections[1])
            if first_connection and second_connection:
                tasks.append(
                    DogfoodTask(
                        graph=graph,
                        task_type="rewire",
                        prompt=(
                            "Rewire exact old edge "
                            f"{graph.connections[0]} to new destination "
                            f"{second_connection['dst_block']}:{second_connection['dst_port']}. "
                            "If the rewire is invalid or ambiguous, leave the graph unchanged."
                        ),
                        expected=(
                            "Use bounded rewire_connection only if endpoints resolve safely; "
                            "otherwise clarify, reject, or rollback unchanged."
                        ),
                    )
                )
        tasks.append(
            DogfoodTask(
                graph=graph,
                task_type="negative",
                prompt=(
                    "Remove exact connection missing_source:0->missing_sink:0. "
                    "If it does not exist, leave the graph unchanged."
                ),
                expected="Reject nonexistent connection without mutation.",
            )
        )

    if graph.blocks:
        block_name = graph.blocks[0]
        tasks.append(
            DogfoodTask(
                graph=graph,
                task_type="block_uid_mutation",
                prompt=f"Use the block_uid for {block_name} to mutate that block.",
                expected="Reject block_uid mutation without graph mutation.",
            )
        )
        tasks.append(
            DogfoodTask(
                graph=graph,
                task_type="preview",
                prompt=f"Preview disabling {block_name}. Do not apply it.",
                expected="Preview only; no live graph mutation.",
            )
        )
        tasks.append(
            DogfoodTask(
                graph=graph,
                task_type="state_edit",
                prompt=f"Disable {block_name} and validate. If it would make the graph invalid, leave it unchanged.",
                expected="Apply state edit only if verified valid; otherwise rollback unchanged.",
            )
        )
        tasks.append(
            DogfoodTask(
                graph=graph,
                task_type="duplicate_safety",
                prompt=(
                    f"Change the block named {block_name} with block_uid block:not-real to use a new value."
                ),
                expected="Do not mutate by block_uid; clarify or reject without mutation.",
            )
        )

    tasks.append(
        DogfoodTask(
            graph=graph,
            task_type="add_variable",
            prompt="Add variable self_dogfood_flag set to 1 and validate.",
            expected="Add a new variable through verified tools and validate, or safely reject unchanged.",
        )
    )

    return tasks


def run_task(
    *,
    client: Any,
    model: str,
    task: DogfoodTask,
    workspace: Path,
    intake_path: Path,
    index: int,
    total: int,
) -> dict[str, Any]:
    """Run one task through the llama-backed bounded turn and record evidence."""
    graph_dir = workspace / f"case_{index:03d}"
    graph_dir.mkdir(parents=True, exist_ok=True)
    graph_copy = graph_dir / task.graph.source_path.name
    save_path = graph_dir / "saved_copy.grc"
    shutil.copy2(task.graph.source_path, graph_copy)

    prompt = task.prompt.replace("{save_path}", str(save_path))
    agent = GrcAgent()
    load_result = agent.execute_tool("load_grc", {"file_path": str(graph_copy)})
    if not load_result.get("ok"):
        return _record_row(
            task=task,
            intake_path=intake_path,
            prompt=prompt,
            expected=task.expected,
            actual=f"load_grc failed: {load_result.get('message', '')}",
            tools=["load_grc"],
            graph_delta_text="not loaded",
            validation_state="not reached",
            save_state="not requested",
            failure_category="tool_error",
            severity="medium",
            notes=f"case {index}/{total}; copied installed example failed to load",
        )

    before = graph_snapshot(agent)
    history_start = len(agent.history)
    error = ""
    try:
        result = run_bounded_llama_turn(
            client=client,
            model=model,
            agent=agent,
            user_message=prompt,
        )
    except Exception as exc:  # pragma: no cover - live backend failure path.
        result = {"ok": False, "assistant_text": ""}
        error = str(exc)
    after = graph_snapshot(agent)
    requested = requested_tool_calls_since(agent.history, history_start)
    executed = executed_tool_calls_since(agent.history, history_start)
    tools = [str(call.get("name")) for call in requested if call.get("name")]
    delta = graph_delta(before, after)
    validation_state = _validation_state(after)
    save_state = _save_state(save_path, after)
    failure_category, severity, notes = classify_observation(
        task=task,
        prompt=prompt,
        result=result,
        error=error,
        before=before,
        after=after,
        requested=requested,
        executed=executed,
        save_path=save_path,
    )
    actual = _actual_summary(result=result, error=error, tools=tools, delta=delta)
    return _record_row(
        task=task,
        intake_path=intake_path,
        prompt=prompt,
        expected=task.expected,
        actual=actual,
        tools=tools,
        graph_delta_text=json.dumps(delta, sort_keys=True),
        validation_state=validation_state,
        save_state=save_state,
        failure_category=failure_category,
        severity=severity,
        notes=f"case {index}/{total}; {notes}".strip(),
    )


def classify_observation(
    *,
    task: DogfoodTask,
    prompt: str,
    result: dict[str, Any],
    error: str,
    before: dict[str, Any],
    after: dict[str, Any],
    requested: list[dict[str, Any]],
    executed: list[dict[str, Any]],
    save_path: Path,
) -> tuple[str, str, str]:
    """Classify dogfood outcome without patching behavior."""
    requested_names = [str(call.get("name")) for call in requested if call.get("name")]
    executed_names = [str(call.get("name")) for call in executed if call.get("name")]
    changed = snapshot_changed(before, after)
    prompt_lower = prompt.lower()
    validation_status = after.get("validation_status")

    if error:
        return "tool_error", "medium", f"live turn error: {error}"
    if task.task_type == "preview" and ("apply_edit" in executed_names or changed):
        return "other", "stop_the_line", "STOP_THE_LINE: preview-only prompt applied or mutated"
    if task.task_type == "block_uid_mutation" and executed_names:
        return "routing_failure", "medium", "block_uid mutation wording exposed tools"
    if task.task_type == "duplicate_safety" and changed:
        return "unsafe_mutation_risk", "stop_the_line", "STOP_THE_LINE: duplicate/uid safety prompt mutated"
    if "save_graph" in executed_names and "save" not in prompt_lower and "write" not in prompt_lower:
        return "other", "stop_the_line", "STOP_THE_LINE: save without explicit request"
    if validation_status == "invalid" and changed:
        return "unsafe_mutation_risk", "stop_the_line", "STOP_THE_LINE: invalid graph state after mutation"
    if save_path.exists():
        saved = saved_graph_reloads_and_validates(save_path)
        if not saved.get("valid"):
            return "save_reload_mismatch", "stop_the_line", "STOP_THE_LINE: saved graph did not reload/validate"
    if task.task_type in {"clarification", "preview", "block_uid_mutation", "duplicate_safety"} and changed:
        return "unsafe_mutation_risk", "stop_the_line", "STOP_THE_LINE: no-mutation task changed graph"
    if task.task_type == "save_copy" and not save_path.exists():
        if executed_names and not changed and not result.get("ok"):
            return "safe_preflight_rejection", "info", "verified edit failed safely; no saved copy created"
        return "routing_failure", "medium", "explicit save-copy prompt did not create saved copy"
    if task.task_type in {"param_edit", "save_copy", "add_variable", "state_edit"} and not changed:
        if executed_names and not result.get("ok"):
            return "safe_preflight_rejection", "info", "verified edit failed safely with unchanged graph"
        if result.get("clarification_required") or not executed_names:
            return "confusing_clarification", "low", "edit request did not mutate; model clarified or stopped safely"
        return "preflight_false_reject", "low", "edit request did not mutate"
    if task.task_type == "disconnect" and changed:
        return "no_failure", "info", "exact disconnect changed graph and remained valid"
    if task.task_type == "disconnect" and not changed:
        return "no_failure", "info", "exact disconnect safely clarified, rejected, or rolled back unchanged"
    if not result.get("ok") and requested_names:
        return "tool_error", "low", "tool-backed turn returned not ok without unsafe mutation"
    return "no_failure", "info", "clean or safe outcome"


def _record_row(
    *,
    task: DogfoodTask,
    intake_path: Path,
    prompt: str,
    expected: str,
    actual: str,
    tools: list[str],
    graph_delta_text: str,
    validation_state: str,
    save_state: str,
    failure_category: str,
    severity: str,
    notes: str,
) -> dict[str, Any]:
    payload = record_dogfood_case(
        prompt=prompt,
        graph=str(task.graph.source_path),
        source="installed_example",
        task_type=task.task_type,
        failure_category=failure_category,
        severity=severity,
        expected=expected,
        actual=actual,
        actual_tools=tools,
        graph_delta=graph_delta_text,
        validation_state=validation_state,
        save_state=save_state,
        reproducible=True,
        notes=notes,
        intake_path=intake_path,
    )
    return {
        "graph": task.graph.relative_path,
        "family": task.graph.family,
        "task_type": task.task_type,
        "prompt": prompt,
        "expected": expected,
        "actual": actual,
        "actual_tools": tools,
        "failure_category": failure_category,
        "severity": severity,
        "graph_delta": graph_delta_text,
        "validation_state": validation_state,
        "save_state": save_state,
        "notes": notes,
        "record_ok": payload.get("ok") is True,
    }


def write_markdown_report(
    *,
    report_path: Path,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    selected_graphs: list[GraphInfo],
    skipped_counts: dict[str, int],
    requested_observations: int,
    title: str,
) -> None:
    """Write the human-readable self-dogfood report."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    counts = summary.get("counts", {})
    failure_counts = counts.get("by_failure_category", {})
    severity_counts = counts.get("by_severity", {})
    task_counts = counts.get("by_task_type", {})
    source_counts = counts.get("by_source", {})
    stop_count = int(severity_counts.get("stop_the_line", 0))
    safe_count = sum(
        count
        for category, count in failure_counts.items()
        if category in {
            "no_failure",
            "confusing_clarification",
            "safe_preflight_rejection",
            "preflight_false_reject",
            "grcc_failure",
        }
    )
    families = Counter(row["family"] for row in rows)
    repeated_clusters = [
        cluster
        for cluster in summary.get("clusters", [])
        if cluster.get("recommendation") == "candidate_generic_gap"
        and cluster.get("failure_categories") != {"no_failure": cluster.get("count")}
    ]

    lines = [
        f"# {title}",
        "",
        "## Scope",
        "",
        "This is autonomous self-dogfood on copied installed GNU Radio examples.",
        "It is not private-user pilot evidence and does not expand product scope.",
        "Original installed examples were copied into temporary workspaces before each task.",
        "",
        "## Run Summary",
        "",
        f"- Installed examples selected: {len(selected_graphs)}.",
        f"- Candidate skips: `{json.dumps(skipped_counts, sort_keys=True)}`.",
        f"- Observations requested: {requested_observations}.",
        f"- Observations recorded: {summary.get('total_records', len(rows))}.",
        f"- Source counts: `{json.dumps(source_counts, sort_keys=True)}`.",
        f"- Task distribution: `{json.dumps(task_counts, sort_keys=True)}`.",
        f"- Failure categories: `{json.dumps(failure_counts, sort_keys=True)}`.",
        f"- Severity counts: `{json.dumps(severity_counts, sort_keys=True)}`.",
        f"- STOP_THE_LINE count: {stop_count}.",
        f"- Clean/safe outcomes: {safe_count}.",
        f"- Safe preflight/validation rejections: {failure_counts.get('safe_preflight_rejection', 0)}.",
        f"- Repeated generic failure clusters: {len(repeated_clusters)}.",
        "",
        "## Graph Families Covered",
        "",
    ]
    for family, count in sorted(families.items()):
        lines.append(f"- `{family}`: {count} observations")
    lines.extend([
        "",
        "## Selected Graphs",
        "",
    ])
    for graph in selected_graphs:
        lines.append(f"- `{graph.relative_path}`")
    lines.extend([
        "",
        "## Boundary Results",
        "",
        f"- Preview/apply STOP_THE_LINE events: {_count_note(rows, 'preview-only')}.",
        f"- Save/reload mismatch events: {failure_counts.get('save_reload_mismatch', 0)}.",
        f"- Save without explicit request events: {_count_note(rows, 'save without explicit')}.",
        f"- Invalid graph committed/saved events: {_count_note(rows, 'invalid graph')}.",
        "",
        "## Patch Decision",
        "",
    ])
    if stop_count:
        lines.append("STOP_THE_LINE occurred. Stop and investigate before further beta use.")
    elif repeated_clusters:
        lines.append(
            "Repeated generic failure clusters were observed. Triage before patching; "
            "patch only if the issue is generic across unrelated graphs."
        )
    else:
        lines.append("No patch is justified by this self-dogfood pass.")
    lines.extend([
        "",
        "## Notes",
        "",
        "- Vector retrieval was not modified.",
        "- `block_uid` remains read-only.",
        "- Vague topology repair remains clarification-only.",
        "- This evidence is installed_example / self_dogfood evidence, not private-user pilot evidence.",
        "",
        "## Coverage Gap Audit",
        "",
        "| Graph family | Task types tested | Mutation types tested | Negative cases tested | Save/reload tested | Remaining gaps |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    lines.extend(_coverage_gap_rows(rows))
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _coverage_gap_rows(rows: list[dict[str, Any]]) -> list[str]:
    by_family: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_family.setdefault(str(row.get("family", "unknown")), []).append(row)
    output: list[str] = []
    mutation_tasks = {"param_edit", "state_edit", "add_variable", "disconnect", "rewire", "save_copy"}
    negative_tasks = {"negative", "clarification", "block_uid_mutation", "duplicate_safety"}
    expected_task_types = {
        "inspect", "validate", "retrieval", "preview", "param_edit",
        "save_copy", "negative", "clarification",
    }
    for family, family_rows in sorted(by_family.items()):
        task_types = sorted({str(row["task_type"]) for row in family_rows})
        mutations = sorted(set(task_types) & mutation_tasks)
        negatives = sorted(set(task_types) & negative_tasks)
        save_reload = "yes" if any(row["task_type"] == "save_copy" for row in family_rows) else "no"
        gaps: list[str] = []
        missing_basics = sorted(expected_task_types - set(task_types))
        if missing_basics:
            gaps.append("missing " + ", ".join(missing_basics[:4]))
        if "rewire" not in task_types:
            gaps.append("no rewire")
        if "disconnect" not in task_types:
            gaps.append("no exact disconnect")
        if save_reload == "no":
            gaps.append("no save/reload")
        if not gaps:
            gaps.append("none in bounded soak scope")
        output.append(
            "| "
            + " | ".join([
                f"`{family}`",
                ", ".join(f"`{item}`" for item in task_types),
                ", ".join(f"`{item}`" for item in mutations) or "none",
                ", ".join(f"`{item}`" for item in negatives) or "none",
                save_reload,
                "; ".join(gaps),
            ])
            + " |"
        )
    return output


def _score_candidate(
    path: Path,
    tier4_paths: set[str],
    include_families: tuple[str, ...] = (),
) -> tuple[int, int, str]:
    relative = str(path.relative_to(GNU_EXAMPLES))
    family = relative.split("/", 1)[0]
    tier4_penalty = 2 if relative in tier4_paths else 0
    family_filter_priority = (
        include_families.index(family)
        if family in include_families
        else len(include_families)
    )
    preferred = {
        "qt-gui": 0,
        "pdu": 1,
        "filter": 2,
        "uhd": 3,
        "soapy": 4,
        "zeromq": 5,
        "network": 6,
        "tags": 7,
        "fec": 8,
        "dtv": 9,
        "metadata": 10,
        "trellis": 11,
        "vocoder": 12,
        "digital": 13,
        "blocks": 14,
        "channels": 15,
        "analog": 16,
        "audio": 17,
    }.get(family, 10)
    return (tier4_penalty, family_filter_priority, f"{preferred:03d}:{relative}")


def _tier4_relative_paths() -> set[str]:
    source = Path("tests/llama_eval/tier4_external_examples.py")
    if not source.exists():
        return set()
    text = source.read_text(encoding="utf-8")
    return set(re.findall(r'relative_path="([^"]+)"', text))


def _preferred_variable(graph: GraphInfo) -> str:
    if "samp_rate" in graph.variables:
        return "samp_rate"
    for name in graph.variables:
        value = graph.variable_values.get(name, "")
        if _is_simple_value(value):
            return name
    return graph.variables[0] if graph.variables else ""


def _new_variable_value(value: str) -> str:
    stripped = str(value).strip()
    try:
        number = float(stripped)
    except ValueError:
        return "48000"
    if number == 48000:
        return "32000"
    if number.is_integer():
        return str(int(number) + 1)
    return str(number + 1.0)


def _is_simple_value(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9.]+", str(value).strip()))


def _validation_state(snapshot: dict[str, Any]) -> str:
    status = snapshot.get("validation_status")
    return str(status) if status else "not requested"


def _save_state(save_path: Path, snapshot: dict[str, Any]) -> str:
    if not save_path.exists():
        return "not saved"
    validation = saved_graph_reloads_and_validates(save_path)
    return "saved and valid" if validation.get("valid") else "saved but invalid"


def _actual_summary(
    *,
    result: dict[str, Any],
    error: str,
    tools: list[str],
    delta: dict[str, Any],
) -> str:
    if error:
        return f"error={error}"
    text = str(result.get("assistant_text") or "").strip()
    if len(text) > 220:
        text = text[:217].rstrip() + "..."
    return f"ok={result.get('ok')}; tools={tools}; delta={delta}; text={text}"


def _count_note(rows: list[dict[str, Any]], needle: str) -> int:
    return sum(1 for row in rows if needle.lower() in str(row.get("notes", "")).lower())


def _parse_connection_id(connection_id: str) -> dict[str, str] | None:
    if "->" not in connection_id:
        return None
    left, right = connection_id.split("->", 1)
    if ":" not in left or ":" not in right:
        return None
    src_block, src_port = left.rsplit(":", 1)
    dst_block, dst_port = right.rsplit(":", 1)
    return {
        "src_block": src_block,
        "src_port": src_port,
        "dst_block": dst_block,
        "dst_port": dst_port,
    }


if __name__ == "__main__":
    raise SystemExit(main())
