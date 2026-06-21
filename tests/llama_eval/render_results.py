"""Render live-eval result stores to human-readable Markdown reports.

Given a directory produced by the R-suite runner, this script reads each
``<label>.json`` store (the format written by ``harness.write_run_store``)
and writes a sibling ``<label>.md`` report that is easy to skim.

Usage:
    uv run python -m tests.llama_eval.render_results R_test_results
    uv run python -m tests.llama_eval.render_results path/to/one.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def _load_store(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "runs": []}
    except json.JSONDecodeError as exc:
        print(f"[warn] {path}: not valid JSON ({exc})", file=sys.stderr)
        return {"version": 1, "runs": []}
    if not isinstance(data, dict):
        return {"version": 1, "runs": []}
    data.setdefault("runs", [])
    return data


def _status_badge(status: str | None) -> str:
    s = (status or "").upper()
    if s == "PASS":
        return "**PASS**"
    if s == "FAIL":
        return "**FAIL**"
    if s in {"INFRA_FAIL", "INFRA_UNAVAILABLE"}:
        return "*INFRA*"
    return s or "-"


def _truthy(b: Any) -> str:
    if b is True:
        return "PASS"
    if b is False:
        return "FAIL"
    return "-"


def _format_args(arguments: Any, max_len: int = 240) -> str:
    if arguments is None or arguments == "":
        return "_(no args)_"
    if isinstance(arguments, str):
        text = arguments
    else:
        try:
            text = json.dumps(arguments, indent=2, sort_keys=True)
        except (TypeError, ValueError):
            text = str(arguments)
    text = text.strip()
    if len(text) > max_len:
        text = text[: max_len - 14] + "\n... [truncated]"
    return f"```json\n{text}\n```"


def _format_tool_calls(calls: list[dict[str, Any]], label: str) -> str:
    if not calls:
        return f"_{label}: none_\n"
    lines: list[str] = []
    for i, call in enumerate(calls, 1):
        if not isinstance(call, dict):
            continue
        name = str(call.get("name", "?"))
        arguments = call.get("arguments")
        if name in {"?", ""} and isinstance(call.get("function"), dict):
            name = str(call["function"].get("name", "?"))
            arguments = call["function"].get("arguments", arguments)
        result = call.get("result")
        if isinstance(arguments, dict) and "ok" in arguments and result is None:
            result = arguments
        ok_marker = ""
        if isinstance(result, dict) and "ok" in result:
            ok_marker = " " + ("[ok]" if result.get("ok") else "[error]")
        lines.append(f"{i}. **{name}**{ok_marker}")
        lines.append(_format_args(arguments))
        if result is not None and not (
            isinstance(result, dict) and set(result.keys()) <= {"ok", "error_type"}
        ):
            lines.append("  result:")
            lines.append(_format_args(result))
    return "\n".join(lines) + "\n"


def _format_dimensions(turn: dict[str, Any]) -> str:
    dims = [
        "routing_pass",
        "argument_pass",
        "tool_success_pass",
        "semantic_pass",
        "safety_pass",
        "end_state_pass",
        "recovery_pass",
        "model_contract_pass",
        "runtime_safety_pass",
        "budget_pass",
        "lint_pass",
    ]
    rows = [f"| {name} | {_truthy(turn.get(name))} |" for name in dims if name in turn]
    if not rows:
        return ""
    header = "| dimension | result |\n| --- | --- |\n"
    return header + "\n".join(rows) + "\n"


def _format_turn(turn: dict[str, Any], index: int) -> str:
    out: list[str] = [f"### Turn {index + 1}"]
    passed = turn.get("passed")
    elapsed = turn.get("elapsed_seconds")
    meta = f"_passed: {_truthy(passed)}"
    if elapsed is not None:
        meta += f" • elapsed: {elapsed}s"
    meta += "_"
    out.append(meta)
    out.append("")

    out.append("**Prompt (user)**")
    out.append("")
    out.append("```")
    out.append(str(turn.get("prompt", "")).rstrip())
    out.append("```")
    out.append("")

    assistant_text = str(turn.get("assistant_text", "")).strip()
    if assistant_text:
        out.append("**Model reply**")
        out.append("")
        out.append("```")
        out.append(assistant_text)
        out.append("```")
        out.append("")
    else:
        out.append("**Model reply**: _(empty)_\n")

    clarification = turn.get("clarification_result")
    if clarification:
        out.append("**Clarification**")
        out.append("")
        out.append(_format_args(clarification))
        out.append("")

    out.append("**Requested tool calls**")
    out.append("")
    out.append(_format_tool_calls(turn.get("requested_tool_calls", []), "Requested"))
    out.append("")

    out.append("**Executed tool calls**")
    out.append("")
    out.append(_format_tool_calls(turn.get("executed_tool_calls", []), "Executed"))
    out.append("")

    error = turn.get("error")
    if error:
        out.append("**Error**")
        out.append("")
        out.append("```")
        out.append(str(error).rstrip())
        out.append("```")
        out.append("")

    dims = _format_dimensions(turn)
    if dims:
        out.append("**Pass/fail dimensions**")
        out.append("")
        out.append(dims)
        out.append("")

    return "\n".join(out)


def _format_scenario(entry: dict[str, Any]) -> str:
    category = entry.get("category", "?")
    name = entry.get("case_name", "?")
    status = entry.get("status") or "UNKNOWN"
    expected = entry.get("expected_chain")
    actual = entry.get("actual_chain") or []
    run_index = entry.get("run_index", 0)
    timestamp = entry.get("timestamp", "")
    release_profile = (entry.get("release_metadata") or {}).get("release_profile", "")

    header = f"## {_status_badge(status)} `{category}/{name}` (run {run_index + 1})"
    if release_profile:
        header += f"  \n_profile: `{release_profile}`_"
    if timestamp:
        header += f"  \n_timestamp: `{timestamp}`_"

    out: list[str] = [header, ""]

    if expected is not None:
        out.append("**Expected tool sequence**")
        out.append("")
        out.append("```json")
        out.append(json.dumps(expected, indent=2, sort_keys=True))
        out.append("```")
        out.append("")

    out.append(f"**Actual tool sequence**: `{', '.join(actual) if actual else '_(none)_'}`")
    out.append("")

    err_type = entry.get("error_type")
    if err_type:
        out.append(f"**Error type**: `{err_type}`")
        out.append("")

    turn_results = (entry.get("run_result") or {}).get("turn_results") or entry.get(
        "turn_results"
    ) or []
    if turn_results:
        for i, turn in enumerate(turn_results):
            out.append(_format_turn(turn, i))
    else:
        out.append("_(no turn results captured)_")
        out.append("")

    return "\n".join(out)


def _format_header(label: str, runs: list[dict[str, Any]], path: Path) -> str:
    statuses = Counter((r.get("status") or "UNKNOWN").upper() for r in runs)
    categories = Counter(r.get("category", "?") for r in runs)
    total = len(runs)
    passed = statuses.get("PASS", 0)
    failed = statuses.get("FAIL", 0)
    infra = statuses.get("INFRA_FAIL", 0) + statuses.get("INFRA_UNAVAILABLE", 0)

    model_alias = ""
    server_url = ""
    if runs:
        meta = runs[0].get("release_metadata") or {}
        model_alias = str(meta.get("model_alias", ""))
        server_url = str(meta.get("server_url", ""))

    pass_rate = (passed / total) if total else 0.0
    return (
        f"# R-suite report: `{label}`\n\n"
        f"- **Source file**: `{path.name}`\n"
        f"- **Generated**: `{datetime.now().isoformat(timespec='seconds')}`\n"
        f"- **Model**: `{model_alias or '(unset)'}`\n"
        f"- **Server URL**: `{server_url or '(unset)'}`\n"
        f"- **Total runs**: {total}  \n"
        f"- **Passed**: {passed}  •  **Failed**: {failed}  •  "
        f"**Infra**: {infra}  •  **Pass rate**: {pass_rate:.1%}\n"
        f"- **By category**: "
        + ", ".join(f"`{k}`={v}" for k, v in sorted(categories.items()))
        + "\n"
    )


def render_store(path: Path) -> Path | None:
    store = _load_store(path)
    runs = store.get("runs") or []
    if not runs:
        return None
    label = path.stem
    sections = [_format_header(label, runs, path)]
    for entry in runs:
        sections.append(_format_scenario(entry))
    out = path.with_suffix(".md")
    out.write_text("\n---\n\n".join(sections) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render R-suite JSON results to Markdown reports."
    )
    parser.add_argument(
        "path",
        help="A results directory or a single .json results file.",
    )
    args = parser.parse_args(argv)

    target = Path(args.path)
    if target.is_dir():
        files = sorted(target.glob("*.json"))
    elif target.is_file():
        files = [target]
    else:
        print(f"Path not found: {target}", file=sys.stderr)
        return 1

    if not files:
        print("No JSON stores to render.", file=sys.stderr)
        return 1

    written = 0
    for path in files:
        out = render_store(path)
        if out is None:
            print(f"[skip] {path}: no runs captured")
            continue
        print(f"[ok]    {path} -> {out}")
        written += 1
    print(f"Rendered {written} report(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
