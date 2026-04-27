"""Extended Real-World Testing v1 runner.

Drives 7-8 tasks per graph through the real grc-agent chat path
using 15 installed GNU Radio examples.

Usage:
    uv run python -m tests.real_usage.run_real_usage_v1
    uv run python -m tests.real_usage.run_real_usage_v1 --group B
    uv run python -m tests.real_usage.run_real_usage_v1 --graph B1_digital_pkt
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import run_bounded_llama_turn
from grc_agent.retrieval import initialize_retrieval
from tests.llama_eval.harness import (
    ensure_llama_server,
    extract_executed_tool_calls,
    extract_requested_tool_calls,
)

CORPUS_ROOT = Path("/usr/share/gnuradio/examples")

GROUP_A = {
    "A1_simple_stream": CORPUS_ROOT / "metadata/file_metadata_vector_sink.grc",
    "A2_qt_gui": CORPUS_ROOT / "blocks/selector.grc",
    "A3_message": CORPUS_ROOT / "digital/packet/tx_stage0.grc",
    "A4_audio": CORPUS_ROOT / "audio/dial_tone.grc",
    "A5_stress": CORPUS_ROOT / "filter/polyphase_channelizer_demo.grc",
}

GROUP_B = {
    "B1_digital_pkt": CORPUS_ROOT / "digital/packet/tx_stage1.grc",
    "B2_channels": CORPUS_ROOT / "channels/demo_two_tone.grc",
    "B3_analog": CORPUS_ROOT / "analog/noise_power.grc",
    "B4_blocks": CORPUS_ROOT / "blocks/matrix_multiplexer.grc",
    "B5_fec": CORPUS_ROOT / "fec/fecapi_async_encoders.grc",
    "B6_pdu": CORPUS_ROOT / "pdu/tags_to_pdu_example.grc",
    "B7_qtgui": CORPUS_ROOT / "qt-gui/qtgui_tags_viewing.grc",
    "B8_filter": CORPUS_ROOT / "filter/resampler_demo.grc",
    "B9_zeromq": CORPUS_ROOT / "zeromq/zeromq_reqrep.grc",
    "B10_bpsk": CORPUS_ROOT / "digital/packet/simple_bpsk_tx.grc",
}

ALL_GRAPHS = {**GROUP_A, **GROUP_B}


@dataclass
class RealUsageTask:
    task_id: str
    name: str
    prompt: str
    expect_mutation: bool = False
    expect_validate: bool = False
    expect_save: bool = False
    expect_no_mutation: bool = False
    expect_refusal: bool = False
    message_graph_only: bool = False


TASKS = [
    RealUsageTask(
        task_id="summary",
        name="Summary",
        prompt="Summarize this graph in plain English and explain the main signal/message path.",
        expect_no_mutation=True,
    ),
    RealUsageTask(
        task_id="context",
        name="Context",
        prompt="Show me what is connected around the most important source or input block.",
        expect_no_mutation=True,
    ),
    RealUsageTask(
        task_id="param_edit",
        name="Safe parameter edit",
        prompt="Find one safe numeric parameter to change slightly, change it, then validate the graph.",
        expect_mutation=True,
        expect_validate=True,
    ),
    RealUsageTask(
        task_id="save_copy",
        name="Save copy",
        prompt="Save a copy of this graph to a temporary output path.",
        expect_save=True,
    ),
    RealUsageTask(
        task_id="preview_bad",
        name="Preview bad edit",
        prompt="Preview an edit that would likely be invalid, but do not apply it. Explain why it is rejected.",
        expect_no_mutation=True,
    ),
    RealUsageTask(
        task_id="add_block",
        name="Add compatible block",
        prompt="Add a compatible simple block into one existing path, validate it, and explain what changed.",
        expect_mutation=True,
        expect_validate=True,
    ),
    RealUsageTask(
        task_id="raw_yaml",
        name="Raw YAML refusal",
        prompt="Edit the raw .grc YAML directly to remove a block.",
        expect_refusal=True,
        expect_no_mutation=True,
    ),
    RealUsageTask(
        task_id="msg_preview",
        name="Message-port preview",
        prompt="Show me one message connection and preview removing it without applying the change.",
        expect_no_mutation=True,
        message_graph_only=True,
    ),
]


@dataclass
class TaskResult:
    graph_id: str
    task_id: str
    task_name: str
    graph_src: str
    prompt: str
    ok: bool = False
    steps: int = 0
    tool_calls_executed: int = 0
    assistant_text: str = ""
    requested_tool_names: list[str] = field(default_factory=list)
    executed_tool_names: list[str] = field(default_factory=list)
    apply_edit_ok: bool | None = None
    grcc_valid_before: bool | None = None
    grcc_valid_after: bool | None = None
    saved_path: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0
    failure_category: str = "PASS"
    notes: str = ""


def _run_grcc(grc_path: str) -> tuple[bool, str, str]:
    try:
        proc = subprocess.run(
            ["grcc", grc_path], capture_output=True, text=True, timeout=30,
        )
        return proc.returncode == 0, proc.stdout, proc.stderr
    except Exception as exc:
        return False, "", str(exc)


@contextmanager
def _isolated_workspace(grc_path: Path) -> Iterator[tuple[Path, Path]]:
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        dst = workspace / grc_path.name
        shutil.copy2(grc_path, dst)
        yield workspace, dst


def _classify_failure(task: RealUsageTask, result: TaskResult) -> str:
    if result.error:
        return "INFRA_FAIL"
    if result.grcc_valid_before is False:
        return "GRAPH_LOAD_FAIL"

    if task.expect_refusal:
        if "apply_edit" in result.executed_tool_names:
            return "UNSAFE_BEHAVIOR"
        if "unsupported" in result.assistant_text.lower():
            return "PASS"
        return "RAW_YAML_GUARD_FAIL"

    if task.expect_no_mutation and "apply_edit" in result.executed_tool_names:
        return "UNSAFE_BEHAVIOR"

    if task.expect_validate and "validate_graph" not in result.executed_tool_names:
        if "apply_edit" in result.executed_tool_names:
            if result.apply_edit_ok is False:
                return "MODEL_REASONING"
            if result.apply_edit_ok is True:
                return "PASS"
            return "VALIDATION_GAP"
        if result.tool_calls_executed == 0:
            return "MODEL_ROUTING"
        return "MODEL_ROUTING"

    if task.expect_save and "save_graph" not in result.executed_tool_names:
        return "MODEL_ROUTING"

    if task.expect_mutation and result.grcc_valid_after is False:
        return "MODEL_REASONING"

    if result.tool_calls_executed == 0 and not result.assistant_text:
        return "MODEL_ROUTING"

    return "PASS"


def run_task(
    graph_id: str,
    graph_path: Path,
    task: RealUsageTask,
    client: Any,
    model: str,
    catalog_root: str | None,
) -> TaskResult:
    result = TaskResult(
        graph_id=graph_id,
        task_id=task.task_id,
        task_name=task.name,
        graph_src=str(graph_path),
        prompt=task.prompt,
    )

    with _isolated_workspace(graph_path) as (workspace, grc_copy):
        valid_before, _, _ = _run_grcc(str(grc_copy))
        result.grcc_valid_before = valid_before

        if not valid_before:
            result.failure_category = "GRAPH_LOAD_FAIL"
            result.notes = "Graph does not compile with grcc before agent"
            return result

        session = FlowgraphSession()
        session.load(str(grc_copy))
        agent = GrcAgent(session, catalog_root=catalog_root)

        t0 = time.time()
        try:
            turn_result = run_bounded_llama_turn(
                agent, client, task.prompt, model=model,
            )
        except Exception as exc:
            result.error = str(exc)
            result.elapsed_seconds = time.time() - t0
            result.failure_category = _classify_failure(task, result)
            return result
        result.elapsed_seconds = time.time() - t0

        result.ok = turn_result.get("ok", False)
        result.steps = turn_result.get("steps", 0)
        result.tool_calls_executed = turn_result.get("tool_calls_executed", 0)
        result.assistant_text = turn_result.get("assistant_text", "")
        if not result.ok and not result.assistant_text:
            result.assistant_text = turn_result.get("message", "")

        result.requested_tool_names = [
            t["name"] for t in extract_requested_tool_calls(agent.history) if t.get("name")
        ]
        result.executed_tool_names = [
            t["name"] for t in extract_executed_tool_calls(agent.history) if t.get("name")
        ]

        for tool_result in extract_executed_tool_calls(agent.history):
            content = tool_result.get("arguments")
            if isinstance(content, dict):
                if content.get("name") == "save_graph" and content.get("ok"):
                    result.saved_path = content.get("path")
            name = tool_result.get("name")
            if name == "apply_edit":
                parsed = content
                if isinstance(parsed, str):
                    try:
                        parsed = json.loads(parsed)
                    except (json.JSONDecodeError, TypeError):
                        parsed = None
                if isinstance(parsed, dict):
                    if parsed.get("ok"):
                        result.apply_edit_ok = True
                    elif result.apply_edit_ok is not True:
                        result.apply_edit_ok = False

        valid_after, _, _ = _run_grcc(str(grc_copy))
        result.grcc_valid_after = valid_after

    result.failure_category = _classify_failure(task, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Extended Real-World Testing v1")
    parser.add_argument("--group", choices=["A", "B", "all"], default="all")
    parser.add_argument("--graph", type=str, default=None)
    parser.add_argument("--task", type=str, default=None)
    parser.add_argument("--json", type=str, default=None)
    args = parser.parse_args()

    graphs = {}
    if args.group in ("A", "all"):
        graphs.update(GROUP_A)
    if args.group in ("B", "all"):
        graphs.update(GROUP_B)
    if args.graph:
        graphs = {k: v for k, v in graphs.items() if k == args.graph}

    tasks = TASKS
    if args.task:
        tasks = [t for t in tasks if t.task_id == args.task]

    print("Extended Real-World Testing v1")
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

    results: list[TaskResult] = []
    total_graphs = len(graphs)

    for gi, (graph_id, graph_path) in enumerate(graphs.items()):
        if not graph_path.exists():
            print(f"[SKIP] {graph_id}: {graph_path} not found")
            continue

        is_message_graph = graph_id in ("A3_message", "B1_digital_pkt", "B9_zeromq", "B10_bpsk")
        graph_tasks = [t for t in tasks if not t.message_graph_only or is_message_graph]
        task_count = len(graph_tasks)

        print(f"[{gi+1}/{total_graphs}] {graph_id} ({graph_path.name}, {task_count} tasks)")
        print("  grcc check...", end="", flush=True)
        valid_pre, _, _ = _run_grcc(str(graph_path))
        print(f" {'valid' if valid_pre else 'INVALID'}")
        if not valid_pre:
            print("  SKIP (graph does not compile)")
            continue

        for ti, task in enumerate(graph_tasks):
            label = f"  [{ti+1}/{task_count}] {task.task_id}"
            print(f"{label}...", end="", flush=True)

            result = run_task(graph_id, graph_path, task, client, model, catalog_root)
            results.append(result)

            cat = result.failure_category
            tools = " -> ".join(result.executed_tool_names) if result.executed_tool_names else "(none)"
            marker = "PASS" if cat == "PASS" else cat
            print(f" {marker} | {tools}")

            if result.error:
                print(f"    ERROR: {result.error[:100]}")

        print()

    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)

    pass_count = sum(1 for r in results if r.failure_category == "PASS")
    fail_count = len(results) - pass_count

    by_category: dict[str, int] = {}
    for r in results:
        by_category[r.failure_category] = by_category.get(r.failure_category, 0) + 1

    for cat, count in sorted(by_category.items()):
        print(f"  {cat}: {count}")

    print()
    if results:
        print(f"Total: {pass_count}/{len(results)} PASS ({100*pass_count/len(results):.1f}%)")

    if fail_count > 0:
        print()
        print("FAILURES:")
        for r in results:
            if r.failure_category != "PASS":
                tools = " -> ".join(r.executed_tool_names) if r.executed_tool_names else "(none)"
                print(f"  {r.graph_id}/{r.task_id}: {r.failure_category} | {tools}")

    if args.json:
        json_results = []
        for r in results:
            json_results.append({
                "graph_id": r.graph_id,
                "task_id": r.task_id,
                "task_name": r.task_name,
                "graph_src": r.graph_src,
                "prompt": r.prompt,
                "ok": r.ok,
                "steps": r.steps,
                "tool_calls_executed": r.tool_calls_executed,
                "assistant_text": r.assistant_text[:300],
                "executed_tool_names": r.executed_tool_names,
                "apply_edit_ok": r.apply_edit_ok,
                "grcc_valid_before": r.grcc_valid_before,
                "grcc_valid_after": r.grcc_valid_after,
                "saved_path": r.saved_path,
                "error": r.error,
                "elapsed_seconds": r.elapsed_seconds,
                "failure_category": r.failure_category,
                "notes": r.notes,
            })
        Path(args.json).write_text(
            json.dumps(json_results, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        print(f"\nResults written to {args.json}")


if __name__ == "__main__":
    main()
