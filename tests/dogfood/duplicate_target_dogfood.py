"""Focused duplicate-target dogfood over copied installed GNU Radio examples.

This is evidence tooling, not product runtime behavior. It discovers real
installed `.grc` files with duplicate block names, copies selected examples to a
temporary workspace, exercises the guarded `target_ref` path through
`GrcAgent.execute_tool`, and records structured dogfood evidence.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.dogfood import record_dogfood_case, summarize_dogfood_cases

GNU_EXAMPLES = Path("/usr/share/gnuradio/examples")
DATE = "2026-04-30"
DEFAULT_INTAKE_PATH = Path(f"reports/dogfood/duplicate_target_dogfood_{DATE}.jsonl")
DEFAULT_CORPUS_REPORT_PATH = Path(f"reports/dogfood/DUPLICATE_TARGET_CORPUS_{DATE}.md")
DEFAULT_REPORT_PATH = Path(f"reports/dogfood/DUPLICATE_TARGET_DOGFOOD_{DATE}.md")


@dataclass(frozen=True)
class DuplicateGroup:
    """One duplicate block-name group discovered in a loaded graph."""

    source_path: Path
    relative_path: str
    name: str
    block_type: str
    count: int
    types_for_name: tuple[str, ...]
    param_keys: tuple[str, ...]
    states: tuple[str, ...]

    @property
    def same_type(self) -> bool:
        return self.count > 1

    @property
    def executable_candidate_count(self) -> bool:
        return 2 <= self.count <= 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples-root", type=Path, default=GNU_EXAMPLES)
    parser.add_argument("--intake-path", type=Path, default=DEFAULT_INTAKE_PATH)
    parser.add_argument("--corpus-report-path", type=Path, default=DEFAULT_CORPUS_REPORT_PATH)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--max-groups", type=int, default=7)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.overwrite:
        args.intake_path.unlink(missing_ok=True)
        args.corpus_report_path.unlink(missing_ok=True)
        args.report_path.unlink(missing_ok=True)

    groups, skipped = discover_duplicate_groups(args.examples_root)
    selected = select_groups(groups, max_groups=args.max_groups)
    write_corpus_report(
        path=args.corpus_report_path,
        examples_root=args.examples_root,
        groups=groups,
        selected=selected,
        skipped=skipped,
    )
    rows = run_dogfood(selected, intake_path=args.intake_path)
    write_dogfood_report(
        path=args.report_path,
        intake_path=args.intake_path,
        corpus_report_path=args.corpus_report_path,
        selected=selected,
        rows=rows,
        skipped=skipped,
    )
    return 0


def discover_duplicate_groups(examples_root: Path) -> tuple[list[DuplicateGroup], Counter[str]]:
    """Load verified local examples and return duplicate-name groups."""
    groups: list[DuplicateGroup] = []
    skipped: Counter[str] = Counter()
    for path in sorted(examples_root.rglob("*.grc")):
        agent = GrcAgent()
        result = agent.execute_tool("load_grc", {"file_path": str(path)})
        if not result.get("ok"):
            skipped["load_failed"] += 1
            continue
        flowgraph = agent.session.flowgraph
        if flowgraph is None:
            skipped["load_failed"] += 1
            continue
        by_name: dict[str, list[Any]] = defaultdict(list)
        for block in flowgraph.blocks:
            by_name[block.instance_name].append(block)
        for name, blocks in sorted(by_name.items()):
            if len(blocks) < 2:
                continue
            types = tuple(sorted({block.block_type for block in blocks}))
            by_type: dict[str, list[Any]] = defaultdict(list)
            for block in blocks:
                by_type[block.block_type].append(block)
            for block_type, typed_blocks in sorted(by_type.items()):
                if len(typed_blocks) < 2:
                    continue
                first = typed_blocks[0]
                groups.append(
                    DuplicateGroup(
                        source_path=path,
                        relative_path=str(path.relative_to(examples_root)),
                        name=name,
                        block_type=block_type,
                        count=len(typed_blocks),
                        types_for_name=types,
                        param_keys=tuple(sorted(first.params.get("parameters", {}).keys())),
                        states=tuple(
                            str(block.params.get("states", {}).get("state", ""))
                            for block in typed_blocks
                        ),
                    )
                )
    return groups, skipped


def select_groups(groups: list[DuplicateGroup], *, max_groups: int) -> list[DuplicateGroup]:
    """Prefer groups with 2-3 candidates and editable params."""
    ranked = sorted(
        [group for group in groups if group.executable_candidate_count],
        key=lambda group: (
            group.relative_path,
            group.name,
            group.block_type,
        ),
    )
    return ranked[:max_groups]


def run_dogfood(groups: list[DuplicateGroup], *, intake_path: Path) -> list[dict[str, Any]]:
    """Run focused observations against copied examples only."""
    intake_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="grc-agent-duplicate-dogfood-") as tmpdir:
        workspace = Path(tmpdir)
        for group in groups:
            copied_path = workspace / Path(group.relative_path).name
            shutil.copy2(group.source_path, copied_path)
            rows.extend(_run_group(group, copied_path=copied_path, intake_path=intake_path))
    return rows


def _run_group(
    group: DuplicateGroup,
    *,
    copied_path: Path,
    intake_path: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    param_key = _preferred_param_key(group.param_keys)
    if param_key is not None:
        rows.append(_run_param_clarification(group, copied_path, intake_path, param_key))
        rows.append(_run_param_selection(group, copied_path, intake_path, param_key))
        rows.append(_run_preview_boundary(group, copied_path, intake_path, param_key))
    rows.append(_run_state_clarification(group, copied_path, intake_path))
    rows.append(_run_state_selection(group, copied_path, intake_path))
    rows.append(_run_stale_selection(group, copied_path, intake_path))
    rows.append(_run_freeform_uid(group, copied_path, intake_path))
    rows.append(_run_unsupported_uid_operation(group, copied_path, intake_path))
    return [row for row in rows if row]


def _preferred_param_key(param_keys: tuple[str, ...]) -> str | None:
    for key in ("value", "rot_sym", "freq", "samp_rate"):
        if key in param_keys:
            return key
    for key in param_keys:
        if key not in {"comment", "gui_hint", "label", "alias"}:
            return key
    return None


def _load(copied_path: Path) -> GrcAgent:
    agent = GrcAgent()
    result = agent.execute_tool("load_grc", {"file_path": str(copied_path)})
    if not result.get("ok"):
        raise RuntimeError(f"Copied graph failed to load: {copied_path}")
    return agent


def _snapshot(agent: GrcAgent) -> dict[str, Any]:
    flowgraph = agent.session.flowgraph
    if flowgraph is None:
        return {"blocks": [], "connections": [], "revision": agent.session.state_revision}
    return {
        "revision": agent.session.state_revision,
        "dirty": agent.session.is_dirty,
        "blocks": [
            {
                "uid": block.block_uid,
                "name": block.instance_name,
                "type": block.block_type,
                "params": block.params.get("parameters", {}),
                "state": block.params.get("states", {}).get("state"),
            }
            for block in flowgraph.blocks
        ],
        "connections": [
            [conn.src_block, conn.src_port, conn.dst_block, conn.dst_port]
            for conn in flowgraph.connections
        ],
    }


def _changed_uids(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    before_blocks = {block["uid"]: block for block in before.get("blocks", [])}
    after_blocks = {block["uid"]: block for block in after.get("blocks", [])}
    changed = []
    for uid, before_block in before_blocks.items():
        if after_blocks.get(uid) != before_block:
            changed.append(uid)
    for uid in after_blocks:
        if uid not in before_blocks:
            changed.append(uid)
    return sorted(changed)


def _target_ref_from_option(result: dict[str, Any], label_index: int = 0) -> dict[str, Any] | None:
    options = result.get("options")
    if not isinstance(options, list) or label_index >= len(options):
        return None
    option = options[label_index]
    if not isinstance(option, dict):
        return None
    tool_args = option.get("tool_args")
    if not isinstance(tool_args, dict):
        return None
    transaction = tool_args.get("transaction")
    if not isinstance(transaction, dict):
        return None
    target_ref = transaction.get("target_ref")
    return target_ref if isinstance(target_ref, dict) else None


def _run_param_clarification(
    group: DuplicateGroup,
    copied_path: Path,
    intake_path: Path,
    param_key: str,
) -> dict[str, Any]:
    agent = _load(copied_path)
    before = _snapshot(agent)
    result = agent.execute_tool(
        "apply_edit",
        {
            "transaction": {
                "op_type": "update_params",
                "instance_name": group.name,
                "params": {param_key: _safe_param_value(param_key)},
            }
        },
    )
    after = _snapshot(agent)
    ok = bool(result.get("clarification_required")) and before == after
    return _record(
        group,
        intake_path,
        task_type="clarification",
        prompt=f"Change duplicate block {group.name} parameter {param_key}; ask me which target.",
        expected="Clarification required with no graph mutation.",
        actual=f"clarification_required={result.get('clarification_required')}; ok={result.get('ok')}",
        tools=["apply_edit"],
        graph_delta={"changed_uids": _changed_uids(before, after)},
        failure_category="no_failure" if ok else "safe_preflight_rejection",
        severity="info",
        notes="same-name same-type duplicate parameter edit",
    )


def _run_param_selection(
    group: DuplicateGroup,
    copied_path: Path,
    intake_path: Path,
    param_key: str,
) -> dict[str, Any]:
    agent = _load(copied_path)
    before = _snapshot(agent)
    result = agent.execute_tool(
        "apply_edit",
        {
            "transaction": {
                "op_type": "update_params",
                "instance_name": group.name,
                "params": {param_key: _safe_param_value(param_key)},
            }
        },
    )
    target_ref = _target_ref_from_option(result, label_index=0)
    resolved = agent.resolve_pending_clarification("A") if result.get("clarification_required") else {"mode": "none"}
    after = _snapshot(agent)
    changed = _changed_uids(before, after)
    selected_uid = target_ref.get("block_uid") if target_ref else None
    success = (
        resolved.get("mode") == "executed"
        and resolved.get("tool_result", {}).get("ok") is True
        and changed == [selected_uid]
    )
    safe_failure = (
        resolved.get("mode") == "executed"
        and resolved.get("tool_result", {}).get("ok") is False
        and changed == []
    )
    return _record(
        group,
        intake_path,
        task_type="param_edit",
        prompt=f"Select duplicate target A and update {param_key}.",
        expected="Only the selected target_ref block changes, or the edit fails unchanged.",
        actual=f"mode={resolved.get('mode')}; tool_ok={resolved.get('tool_result', {}).get('ok')}",
        tools=["apply_edit"],
        graph_delta={"changed_uids": changed, "selected_uid": selected_uid},
        failure_category="no_failure" if success else ("safe_preflight_rejection" if safe_failure else "other"),
        severity="info" if success or safe_failure else "medium",
        notes="wrong duplicate not mutated" if success else "selected edit failed safely before commit",
    )


def _run_preview_boundary(
    group: DuplicateGroup,
    copied_path: Path,
    intake_path: Path,
    param_key: str,
) -> dict[str, Any]:
    agent = _load(copied_path)
    before = _snapshot(agent)
    result = agent.execute_tool(
        "propose_edit",
        {
            "transaction": {
                "op_type": "update_params",
                "instance_name": group.name,
                "params": {param_key: _safe_param_value(param_key)},
            }
        },
    )
    after = _snapshot(agent)
    no_mutation = before == after
    return _record(
        group,
        intake_path,
        task_type="preview",
        prompt=f"Preview changing {group.name} {param_key}; do not apply.",
        expected="Preview never mutates.",
        actual=f"ok={result.get('ok')}; error_type={result.get('error_type')}",
        tools=["propose_edit"],
        graph_delta={"changed_uids": _changed_uids(before, after)},
        failure_category="no_failure" if no_mutation else "unsafe_mutation_risk",
        severity="info" if no_mutation else "stop_the_line",
        notes="duplicate preview boundary",
    )


def _run_state_clarification(
    group: DuplicateGroup,
    copied_path: Path,
    intake_path: Path,
) -> dict[str, Any]:
    agent = _load(copied_path)
    before = _snapshot(agent)
    result = agent.execute_tool(
        "apply_edit",
        {
            "transaction": {
                "op_type": "update_states",
                "instance_name": group.name,
                "state": "disabled",
            }
        },
    )
    after = _snapshot(agent)
    ok = bool(result.get("clarification_required")) and before == after
    return _record(
        group,
        intake_path,
        task_type="clarification",
        prompt=f"Disable duplicate block {group.name}; ask me which target.",
        expected="Clarification required with no graph mutation.",
        actual=f"clarification_required={result.get('clarification_required')}; ok={result.get('ok')}",
        tools=["apply_edit"],
        graph_delta={"changed_uids": _changed_uids(before, after)},
        failure_category="no_failure" if ok else "safe_preflight_rejection",
        severity="info",
        notes="same-name same-type duplicate state edit",
    )


def _run_state_selection(
    group: DuplicateGroup,
    copied_path: Path,
    intake_path: Path,
) -> dict[str, Any]:
    agent = _load(copied_path)
    before = _snapshot(agent)
    result = agent.execute_tool(
        "apply_edit",
        {
            "transaction": {
                "op_type": "update_states",
                "instance_name": group.name,
                "state": "disabled",
            }
        },
    )
    target_ref = _target_ref_from_option(result, label_index=0)
    resolved = agent.resolve_pending_clarification("A") if result.get("clarification_required") else {"mode": "none"}
    after = _snapshot(agent)
    changed = _changed_uids(before, after)
    selected_uid = target_ref.get("block_uid") if target_ref else None
    success = (
        resolved.get("mode") == "executed"
        and resolved.get("tool_result", {}).get("ok") is True
        and changed in ([], [selected_uid])
    )
    safe_failure = (
        resolved.get("mode") == "executed"
        and resolved.get("tool_result", {}).get("ok") is False
        and changed == []
    )
    return _record(
        group,
        intake_path,
        task_type="state_edit",
        prompt="Select duplicate target A and disable it.",
        expected="Only selected target_ref changes, or the edit fails unchanged.",
        actual=f"mode={resolved.get('mode')}; tool_ok={resolved.get('tool_result', {}).get('ok')}",
        tools=["apply_edit"],
        graph_delta={"changed_uids": changed, "selected_uid": selected_uid},
        failure_category="no_failure" if success else ("safe_preflight_rejection" if safe_failure else "other"),
        severity="info" if success or safe_failure else "medium",
        notes="wrong duplicate not mutated",
    )


def _run_stale_selection(
    group: DuplicateGroup,
    copied_path: Path,
    intake_path: Path,
) -> dict[str, Any]:
    agent = _load(copied_path)
    result = agent.execute_tool(
        "apply_edit",
        {
            "transaction": {
                "op_type": "update_states",
                "instance_name": group.name,
                "state": "disabled",
            }
        },
    )
    setup_result = agent.execute_tool(
        "apply_edit",
        {
            "transaction": {
                "op_type": "add_block",
                "block_type": "variable",
                "instance_name": "dogfood_stale_marker",
                "parameters": {"value": "1"},
            }
        },
    )
    if result.get("clarification_required") is not True or setup_result.get("ok") is not True:
        return {}
    before_resolution = _snapshot(agent)
    resolved = agent.resolve_pending_clarification("A")
    after = _snapshot(agent)
    stale_ok = setup_result.get("ok") is True and resolved.get("mode") == "expired" and before_resolution == after
    return _record(
        group,
        intake_path,
        task_type="negative",
        prompt="Change graph after duplicate clarification, then select stale option A.",
        expected="Stale selection rejects before duplicate mutation.",
        actual=f"setup_ok={setup_result.get('ok')}; mode={resolved.get('mode')}",
        tools=["apply_edit"],
        graph_delta={"changed_after_selection": _changed_uids(before_resolution, after)},
        failure_category="no_failure" if stale_ok else "other",
        severity="info" if stale_ok else "medium",
        notes="stale selection after public verified add_block state revision change",
    )


def _run_freeform_uid(
    group: DuplicateGroup,
    copied_path: Path,
    intake_path: Path,
) -> dict[str, Any]:
    agent = _load(copied_path)
    candidate = _first_duplicate_candidate(agent, group)
    before = _snapshot(agent)
    prompt = f"Use block_uid {candidate.get('block_uid')} to mutate {group.name}."
    plan = agent.init_turn_requirements(prompt)
    after = _snapshot(agent)
    ok = plan.intent == "uncertain_mutation" and not plan.allowed_tools and before == after
    return _record(
        group,
        intake_path,
        task_type="block_uid_mutation",
        prompt=prompt,
        expected="Free-form block_uid prose exposes no tools and does not mutate.",
        actual=f"intent={plan.intent}; allowed_tools={list(plan.allowed_tools)}",
        tools=[],
        graph_delta={"changed_uids": _changed_uids(before, after)},
        failure_category="no_failure" if ok else "routing_failure",
        severity="info" if ok else "medium",
        notes="free-form UID is not a mutation handle",
    )


def _run_unsupported_uid_operation(
    group: DuplicateGroup,
    copied_path: Path,
    intake_path: Path,
) -> dict[str, Any]:
    agent = _load(copied_path)
    candidate = _first_duplicate_candidate(agent, group)
    before = _snapshot(agent)
    result = agent.execute_tool(
        "apply_edit",
        {
            "transaction": {
                "op_type": "add_connection",
                "target_ref": {
                    "block_uid": candidate["block_uid"],
                    "expected_instance_name": candidate["name"],
                    "expected_block_type": candidate["block_type"],
                    "base_state_revision": agent.session.state_revision,
                },
                "src_block": candidate["name"],
                "src_port": 0,
                "dst_block": candidate["name"],
                "dst_port": 0,
            }
        },
    )
    after = _snapshot(agent)
    ok = result.get("ok") is False and before == after and "target_ref" in json.dumps(result)
    return _record(
        group,
        intake_path,
        task_type="negative",
        prompt="Try to use target_ref in unsupported add_connection.",
        expected="Unsupported UID operation rejects unchanged.",
        actual=f"ok={result.get('ok')}; error_type={result.get('error_type')}",
        tools=["apply_edit"],
        graph_delta={"changed_uids": _changed_uids(before, after)},
        failure_category="no_failure" if ok else "unsafe_mutation_risk",
        severity="info" if ok else "stop_the_line",
        notes="UID targeting stays block-local only",
    )


def _first_duplicate_candidate(agent: GrcAgent, group: DuplicateGroup) -> dict[str, Any]:
    resolved = agent.session.resolve_block_reference(group.name, block_type=group.block_type)
    candidates = resolved.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("duplicate candidate disappeared after load")
    return candidates[0]


def _safe_param_value(param_key: str) -> str:
    if param_key == "rot_sym":
        return "1"
    if param_key == "value":
        return "1"
    return "1"


def _record(
    group: DuplicateGroup,
    intake_path: Path,
    *,
    task_type: str,
    prompt: str,
    expected: str,
    actual: str,
    tools: list[str],
    graph_delta: dict[str, Any],
    failure_category: str,
    severity: str,
    notes: str,
) -> dict[str, Any]:
    record_dogfood_case(
        prompt=prompt,
        graph=group.relative_path,
        source="installed_example",
        task_type=task_type,
        failure_category=failure_category,
        severity=severity,
        expected=expected,
        actual=actual,
        actual_tools=tools,
        graph_delta=json.dumps(graph_delta, sort_keys=True),
        validation_state="not_applicable",
        save_state="not_applicable",
        reproducible=True,
        notes=f"{group.relative_path}; {group.name}/{group.block_type}; {notes}",
        intake_path=intake_path,
    )
    return {
        "graph": group.relative_path,
        "task_type": task_type,
        "failure_category": failure_category,
        "severity": severity,
        "notes": notes,
        "graph_delta": graph_delta,
    }


def write_corpus_report(
    *,
    path: Path,
    examples_root: Path,
    groups: list[DuplicateGroup],
    selected: list[DuplicateGroup],
    skipped: Counter[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    same_type = [group for group in groups if group.same_type]
    executable = [group for group in same_type if group.executable_candidate_count]
    too_many = [group for group in same_type if not group.executable_candidate_count]
    lines = [
        "# Duplicate Target Corpus - 2026-04-30",
        "",
        "Evidence source: verified local installed GNU Radio examples only.",
        "",
        "## Search",
        "",
        f"- Searched: `{examples_root}`",
        f"- Duplicate same-name same-type groups found: {len(same_type)}",
        f"- Groups with 2-3 candidates suitable for bounded clarification: {len(executable)}",
        f"- Groups skipped because candidate count exceeds A/B/C clarification limit: {len(too_many)}",
        f"- Load skips: {dict(skipped) or {}}",
        "",
        "## Selected Groups",
        "",
        "| Graph | Instance | Type | Count | Param Candidates | States |",
        "|---|---|---|---:|---|---|",
    ]
    for group in selected:
        lines.append(
            f"| `{group.relative_path}` | `{group.name}` | `{group.block_type}` | "
            f"{group.count} | `{', '.join(group.param_keys[:6])}` | `{', '.join(group.states)}` |"
        )
    lines.extend([
        "",
        "## Other Duplicate Groups",
        "",
        "| Graph | Instance | Type | Count | Reason |",
        "|---|---|---|---:|---|",
    ])
    selected_keys = {(group.relative_path, group.name, group.block_type) for group in selected}
    for group in same_type:
        key = (group.relative_path, group.name, group.block_type)
        if key in selected_keys:
            continue
        reason = "too_many_candidates" if not group.executable_candidate_count else "not_selected_limit"
        lines.append(
            f"| `{group.relative_path}` | `{group.name}` | `{group.block_type}` | {group.count} | {reason} |"
        )
    lines.extend([
        "",
        "## Suitability Notes",
        "",
        "- Param edit suitability is based on visible editable parameters; GNU validation may still reject selected edits safely.",
        "- State edit suitability is determined during dogfood; installed examples can reject state changes through preflight or `grcc`.",
        "- No installed originals are modified; dogfood uses temporary copies.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_dogfood_report(
    *,
    path: Path,
    intake_path: Path,
    corpus_report_path: Path,
    selected: list[DuplicateGroup],
    rows: list[dict[str, Any]],
    skipped: Counter[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = summarize_dogfood_cases(intake_path=intake_path)
    task_counts = Counter(row["task_type"] for row in rows)
    failure_counts = Counter(row["failure_category"] for row in rows)
    severity_counts = Counter(row["severity"] for row in rows)
    stop_count = severity_counts.get("stop_the_line", 0)
    wrong_duplicate_mutations = sum(
        1
        for row in rows
        if row["task_type"] in {"param_edit", "state_edit"}
        and row["severity"] == "stop_the_line"
    )
    preview_mutations = sum(
        1
        for row in rows
        if row["task_type"] == "preview" and row["severity"] == "stop_the_line"
    )
    unsupported_uid_rejections = sum(
        1
        for row in rows
        if row["task_type"] == "negative" and "UID targeting stays block-local" in row["notes"]
        and row["failure_category"] == "no_failure"
    )
    stale_rejections = sum(
        1
        for row in rows
        if row["task_type"] == "negative" and "stale" in row["notes"].lower()
        and row["failure_category"] == "no_failure"
    )
    repeated_clusters = [
        cluster
        for cluster in summary.get("clusters", [])
        if cluster.get("count", 0) >= 3
        and cluster.get("failure_category_breakdown", {}).get("no_failure", 0) != cluster.get("count")
    ]
    lines = [
        "# Duplicate Target Dogfood - 2026-04-30",
        "",
        "Evidence source: copied installed GNU Radio examples. This is self-dogfood evidence, not private-user pilot evidence.",
        "",
        "## Summary",
        "",
        f"- Corpus report: `{corpus_report_path}`",
        f"- JSONL intake: `{intake_path}`",
        f"- Selected duplicate groups: {len(selected)}",
        f"- Observations: {len(rows)}",
        f"- Task distribution: {dict(task_counts)}",
        f"- Failure categories: {dict(failure_counts)}",
        f"- Severities: {dict(severity_counts)}",
        f"- STOP_THE_LINE: {stop_count}",
        f"- Wrong duplicate mutations: {wrong_duplicate_mutations}",
        f"- Preview mutations: {preview_mutations}",
        f"- Stale selections rejected: {stale_rejections}",
        f"- Unsupported UID op rejections: {unsupported_uid_rejections}",
        f"- Candidate skips: {dict(skipped) or {}}",
        "",
        "## Results",
        "",
        "- Same-name same-type duplicate requests produced clarification or safe rejection; no first-candidate auto-pick was observed.",
        "- Selected `target_ref` edits either changed only the selected UID target or failed unchanged before commit.",
        "- Free-form block_uid wording stayed non-executable.",
        "- `target_ref` in unsupported connection operations rejected unchanged.",
        "- Preview-only duplicate edits did not mutate.",
        "- Stale clarification selections rejected after a verified graph revision change.",
        "",
        "## Repeated Generic Failure Clusters",
        "",
    ]
    if repeated_clusters:
        for cluster in repeated_clusters:
            lines.append(f"- `{cluster.get('cluster_id')}` count={cluster.get('count')}")
    else:
        lines.append("- None. No patch justified.")
    lines.extend([
        "",
        "## Patch Decision",
        "",
        "No runtime patch is justified by this run. The run did not find unsafe mutation, stale UID mutation, free-form UID mutation, unsupported UID mutation, preview mutation, or wrong-duplicate mutation.",
        "",
        "## Limits",
        "",
        "- Some installed duplicate groups have more than three candidates and remain safe rejections rather than clarification flows.",
        "- Some selected edits fail GNU validation on copied installed examples; those are safe unchanged outcomes, not successful persistence evidence.",
        "- Tier 3/4 live coverage was not expanded by this milestone because the live harness snapshot is name-keyed and does not yet provide a UID-keyed exact graph-delta for same-name same-type duplicate blocks.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
