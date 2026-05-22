"""Export a compact video timeline from a real GRC Agent demo artifact."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

TIMELINE_SCHEMA_VERSION = "2026-05-21.demo-timeline-v1"


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def first_tool_name(step: dict[str, Any]) -> str:
    executed = step.get("executed_tools")
    if isinstance(executed, list) and executed:
        return ", ".join(str(item) for item in executed if item)
    requested = step.get("requested_tool_calls_raw")
    if isinstance(requested, list) and requested:
        return ", ".join(str(call.get("name")) for call in requested if isinstance(call, dict))
    return ""


def operation_kind(step: dict[str, Any]) -> str:
    calls = step.get("requested_tool_calls_raw")
    if not isinstance(calls, list):
        return ""
    for call in calls:
        if not isinstance(call, dict):
            continue
        arguments = call.get("arguments")
        if isinstance(arguments, dict) and arguments.get("operation_kind"):
            return str(arguments["operation_kind"])
    return ""


def validation_status(step: dict[str, Any]) -> str:
    for result in step.get("validation_results", []):
        if not isinstance(result, dict):
            continue
        validation = result.get("validation_result")
        if isinstance(validation, dict):
            if validation.get("valid") is True:
                return "valid"
            if validation.get("valid") is False:
                return "invalid"
    snapshot = step.get("graph_snapshot_after")
    if isinstance(snapshot, dict) and snapshot.get("validation_status"):
        return str(snapshot["validation_status"])
    return ""


def screenshot_for_step(artifact: dict[str, Any], index: int, total: int) -> str | None:
    screenshots = artifact.get("screenshots") if isinstance(artifact.get("screenshots"), dict) else {}
    if index == 0:
        before = screenshots.get("before") if isinstance(screenshots.get("before"), dict) else {}
        if before.get("exists") is True:
            return str(before.get("path"))
    if index == total - 1:
        after = screenshots.get("after") if isinstance(screenshots.get("after"), dict) else {}
        if after.get("exists") is True:
            return str(after.get("path"))
    return None


def export_timeline(artifact: dict[str, Any]) -> dict[str, Any]:
    steps = artifact.get("steps")
    if not isinstance(steps, list):
        raise ValueError("artifact steps must be a list")
    timeline_steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        before = step.get("graph_snapshot_before") if isinstance(step.get("graph_snapshot_before"), dict) else {}
        after = step.get("graph_snapshot_after") if isinstance(step.get("graph_snapshot_after"), dict) else {}
        timeline_steps.append(
            {
                "index": index,
                "step_label": step.get("label") or f"Step {index + 1}",
                "user_prompt": step.get("user_prompt", ""),
                "assistant_summary": step.get("assistant_summary", ""),
                "tool_name": first_tool_name(step),
                "operation_kind": operation_kind(step),
                "graph_delta": step.get("graph_delta", {}),
                "validation_status": validation_status(step),
                "mutation": bool(step.get("mutation")),
                "screenshot_path": screenshot_for_step(artifact, index, len(steps)),
                "before_graph_path": before.get("path"),
                "after_graph_path": after.get("path"),
                "final_graph_path": artifact.get("paths", {}).get("final_graph_path"),
            }
        )
    return {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "title": "GRC Agent Programmatic Demo",
        "classification": artifact.get(
            "classification",
            "Release-validated subset + beta-validated graph operations; not production-ready",
        ),
        "health": {
            "status": artifact.get("health", {}).get("status"),
            "context_verified": artifact.get("health", {}).get("llama_context_verified"),
            "actual_context_tokens": artifact.get("health", {}).get("llama_actual_context_tokens"),
            "desired_context_tokens": artifact.get("health", {}).get("llama_desired_context_tokens"),
            "model_tools": artifact.get("health", {}).get("model_facing_tools"),
        },
        "paths": artifact.get("paths", {}),
        "safety_requirements": artifact.get("safety_requirements", {}),
        "steps": timeline_steps,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Remotion timeline JSON from demo artifact.")
    parser.add_argument("--artifact", required=True, help="Input demo artifact JSON path.")
    parser.add_argument("--output", required=True, help="Output demo timeline JSON path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    artifact = load_json(Path(args.artifact))
    timeline = export_timeline(artifact)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(timeline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output), "steps": len(timeline["steps"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

