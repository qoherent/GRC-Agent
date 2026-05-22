"""Run a real GRC Agent demo against a copied GNU Radio example graph.

The demo deliberately uses the normal bounded llama.cpp-backed GRC Agent turn
loop and records the same raw requested/executed tool history used by the live
eval harness. It does not synthesize tool calls or graph deltas.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from grc_agent.agent import GrcAgent  # noqa: E402
from grc_agent.config import load_app_config  # noqa: E402
from grc_agent.flowgraph_session import FlowgraphSession  # noqa: E402
from grc_agent.llama_server import LlamaServerClient, run_bounded_llama_turn  # noqa: E402
from tests.llama_eval.harness import (  # noqa: E402
    executed_tool_calls_since,
    graph_delta,
    graph_snapshot,
    requested_tool_calls_since,
)
from tests.production.gameplay_judge import detect_forbidden_events  # noqa: E402

DEMO_ARTIFACT_SCHEMA_VERSION = "2026-05-21.demo-artifact-v1"
DEFAULT_GRAPH = Path("/usr/share/gnuradio/examples/audio/dial_tone.grc")
DEFAULT_WORKDIR = Path("/tmp/grc_agent_demo")
DEFAULT_COPY_NAME = "dial_tone_demo.grc"
DEFAULT_ARTIFACT_NAME = "demo_artifact.json"
DEFAULT_DEBUG_BUNDLE_NAME = "debug_bundle.json"
SECRET_TEXT_MARKERS = (
    "ollama_key",
    "OLLAMA_API_KEY",
    "Authorization",
    "Bearer ",
)
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(api[_-]?key|token|secret|authorization)\s*[:=]\s*['\"]?[A-Za-z0-9_.:/+=-]{12,}"
)


class DemoError(RuntimeError):
    """Raised when the real demo cannot safely proceed."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_command(command: list[str], *, cwd: Path | None = None, timeout_seconds: int = 120) -> dict[str, Any]:
    started = time.monotonic()
    env = dict(os.environ)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returncode": None,
            "timeout": True,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": exc.stdout if isinstance(exc.stdout, str) else "",
            "stderr": exc.stderr if isinstance(exc.stderr, str) else "",
        }
    return {
        "command": command,
        "returncode": completed.returncode,
        "timeout": False,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def load_json_stdout(command_result: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(str(command_result.get("stdout") or ""))
    except json.JSONDecodeError as exc:
        raise DemoError(f"command output was not JSON: {command_result.get('command')}") from exc
    if not isinstance(payload, dict):
        raise DemoError(f"command output was not a JSON object: {command_result.get('command')}")
    return payload


def validate_health_ready(health: dict[str, Any], *, dry_run_docs_only: bool = False) -> None:
    if dry_run_docs_only:
        return
    if health.get("status") != "ok":
        raise DemoError(f"health status is not ok: {health.get('status_reasons')}")
    if health.get("llama_context_verified") is not True:
        raise DemoError("llama context is not verified")
    actual_context = health.get("llama_actual_context_tokens")
    desired_context = health.get("llama_desired_context_tokens")
    if not isinstance(actual_context, int) or not isinstance(desired_context, int):
        raise DemoError("health did not report numeric actual/desired context")
    if actual_context < desired_context:
        raise DemoError(f"llama context below desired: actual={actual_context} desired={desired_context}")


def scan_secret_text(text: str) -> list[str]:
    hits = [marker for marker in SECRET_TEXT_MARKERS if marker in text]
    if SECRET_ASSIGNMENT_PATTERN.search(text):
        hits.append("api_key_like_assignment")
    return sorted(set(hits))


def scan_secret_files(paths: list[Path]) -> dict[str, Any]:
    bad_files: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = scan_secret_text(text)
        if hits:
            bad_files.append({"path": str(path), "hits": hits})
    return {
        "files_scanned": [str(path) for path in paths],
        "bad_count": len(bad_files),
        "bad_files": bad_files,
    }


def verify_source_unchanged(source_path: Path, before_hash: str) -> dict[str, Any]:
    after_hash = sha256_file(source_path)
    return {
        "source_path": str(source_path),
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "unchanged": before_hash == after_hash,
    }


def validate_demo_artifact(artifact: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if artifact.get("schema_version") != DEMO_ARTIFACT_SCHEMA_VERSION:
        errors.append("unexpected_schema_version")
    if not isinstance(artifact.get("steps"), list) or not artifact.get("steps"):
        errors.append("steps_missing")
    if not isinstance(artifact.get("health"), dict):
        errors.append("health_missing")
    source_integrity = artifact.get("source_integrity")
    if not isinstance(source_integrity, dict) or source_integrity.get("unchanged") is not True:
        errors.append("source_integrity_failed")
    safety = artifact.get("safety_requirements")
    if not isinstance(safety, dict):
        errors.append("safety_requirements_missing")
    else:
        for key in (
            "original_graph_not_mutated",
            "validation_succeeded",
            "explicit_save",
            "debug_bundle_generated",
            "no_secrets_in_artifacts",
        ):
            if safety.get(key) is not True:
                errors.append(f"safety_{key}_failed")
        for key in ("raw_legacy_attempts", "failed_validation_commits"):
            if safety.get(key) != 0:
                errors.append(f"safety_{key}_nonzero")
    return errors


def default_demo_steps(work_graph_path: Path) -> list[dict[str, str]]:
    graph_path = str(work_graph_path)
    return [
        {
            "label": "Inspect Graph",
            "prompt": "Inspect the copied dial tone graph. Summarize the variables, blocks, connections, and validation status.",
        },
        {
            "label": "Set Sample Rate",
            "prompt": "Change the sample rate to 48000 on this copied graph.",
        },
        {
            "label": "Add Variable",
            "prompt": "Add a variable named demo_gain with value 0.25 to this copied graph.",
        },
        {
            "label": "Guided Insert Request",
            "prompt": (
                "I want to insert a throttle on the connection from the signal source to the add block. "
                "Inspect or ask for the exact option before mutating if needed."
            ),
        },
        {
            "label": "Guided Insert Selection",
            "prompt": (
                "Call change_graph now with operation_kind insert_block, dry_run false, "
                "connection_id analog_sig_source_x_0:0->blocks_add_xx:0, block_id blocks_throttle2, "
                "instance_name blocks_throttle2_demo, and insert_params {type: float, samples_per_second: 48000}."
            ),
        },
        {
            "label": "Explicit Save",
            "prompt": f"Save the copied graph explicitly to {graph_path}.",
        },
        {
            "label": "Load Saved Graph",
            "prompt": f"Load the saved copied graph from {graph_path}.",
        },
        {
            "label": "Inspect Final State",
            "prompt": "Inspect the final copied graph state after reload.",
        },
    ]


def compact_assistant_text(result: dict[str, Any], history_slice: list[dict[str, Any]]) -> str:
    text = result.get("assistant_text") if isinstance(result.get("assistant_text"), str) else ""
    if text:
        return " ".join(text.split())[:600]
    for entry in reversed(history_slice):
        if entry.get("role") == "assistant" and isinstance(entry.get("content"), str):
            content = " ".join(str(entry["content"]).split())
            if content:
                return content[:600]
    return ""


def validation_results_from_executed(executed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for call in executed:
        payload = call.get("arguments")
        if not isinstance(payload, dict):
            continue
        validation = payload.get("validation_result")
        if isinstance(validation, dict):
            results.append({"tool": call.get("name"), "validation_result": validation})
    return results


def lifecycle_events_from_executed(
    executed: list[dict[str, Any]],
    *,
    turn_index: int,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for call in executed:
        tool_name = str(call.get("name") or "")
        if tool_name not in {"save_graph_explicit", "load_graph_explicit"}:
            continue
        payload = call.get("arguments")
        path = payload.get("path") if isinstance(payload, dict) else None
        events.append(
            {
                "turn_index": turn_index,
                "tool": tool_name,
                "ok": bool(isinstance(payload, dict) and payload.get("ok") is True),
                "path": path,
            }
        )
    return events


def run_agent_turn(
    *,
    agent: GrcAgent,
    client: LlamaServerClient,
    model: str,
    prompt: str,
    label: str,
    turn_index: int,
) -> dict[str, Any]:
    before = graph_snapshot(agent)
    history_start = len(agent.history)
    started = time.monotonic()
    result: dict[str, Any] = {}
    error_text = ""
    try:
        result = run_bounded_llama_turn(
            agent=agent,
            client=client,
            model=model,
            user_message=prompt,
            advisor_enabled=False,
            advisor_limited_advisory=False,
            advisor_shadow_telemetry=True,
            mvp_tool_profile=True,
        )
    except Exception as exc:  # pragma: no cover - exercised by live demo.
        error_text = str(exc)
    elapsed = round(time.monotonic() - started, 3)
    history_slice = agent.history[history_start:]
    after = graph_snapshot(agent)
    requested = requested_tool_calls_since(agent.history, history_start)
    executed = executed_tool_calls_since(agent.history, history_start)
    return {
        "turn_index": turn_index,
        "label": label,
        "user_prompt": prompt,
        "assistant_summary": compact_assistant_text(result, history_slice),
        "agent_result": result,
        "agent_error": error_text,
        "duration_seconds": elapsed,
        "requested_tool_calls_raw": requested,
        "normalized_args": [
            {"name": call.get("name"), "arguments": call.get("arguments")}
            for call in requested
        ],
        "executed_tool_calls_raw": executed,
        "executed_tools": [call.get("name") for call in executed],
        "tool_results": [
            {"name": call.get("name"), "result": call.get("arguments")}
            for call in executed
        ],
        "graph_snapshot_before": before,
        "graph_snapshot_after": after,
        "graph_revision_before": before.get("state_revision"),
        "graph_revision_after": after.get("state_revision"),
        "graph_delta": graph_delta(before, after),
        "mutation": before.get("raw_hash") != after.get("raw_hash"),
        "validation_results": validation_results_from_executed(executed),
        "save_load_events": lifecycle_events_from_executed(executed, turn_index=turn_index),
    }


def build_safety_summary(
    *,
    artifact: dict[str, Any],
    debug_bundle_path: Path,
    secret_scan: dict[str, Any],
) -> dict[str, Any]:
    forbidden_events = detect_forbidden_events(artifact)
    raw_legacy_attempts = sum(
        1 for event in forbidden_events if event.get("event") == "raw_legacy_tool_call"
    )
    failed_validation_commits = sum(
        1 for event in forbidden_events if event.get("event") == "failed_validation_commit"
    )
    final_snapshot = artifact.get("final_graph_snapshot")
    final_validation_status = (
        final_snapshot.get("validation_status") if isinstance(final_snapshot, dict) else None
    )
    validation_succeeded = final_validation_status in {"valid", "unknown"} or any(
        isinstance(item, dict)
        and isinstance(item.get("validation_result"), dict)
        and item["validation_result"].get("valid") is True
        for item in artifact.get("validation_results", [])
    )
    return {
        "original_graph_not_mutated": bool(
            artifact.get("source_integrity", {}).get("unchanged") is True
        ),
        "copied_graph_used": bool(
            artifact.get("paths", {}).get("work_graph_path")
            and artifact.get("paths", {}).get("work_graph_path")
            != artifact.get("paths", {}).get("source_graph_path")
        ),
        "validation_succeeded": bool(validation_succeeded),
        "explicit_save": any(
            event.get("tool") == "save_graph_explicit" and event.get("ok") is True
            for event in artifact.get("save_load_events", [])
        ),
        "raw_legacy_attempts": raw_legacy_attempts,
        "failed_validation_commits": failed_validation_commits,
        "debug_bundle_generated": debug_bundle_path.exists(),
        "no_secrets_in_artifacts": secret_scan.get("bad_count") == 0,
        "forbidden_events": forbidden_events,
    }


def build_demo_flow_errors(artifact: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for step in artifact.get("steps", []):
        if isinstance(step, dict) and step.get("agent_error"):
            errors.append(f"agent_turn_error:{step.get('turn_index')}:{step.get('label')}")
    final_snapshot = artifact.get("final_graph_snapshot")
    if not isinstance(final_snapshot, dict):
        return ["final_snapshot_missing"]
    variables = final_snapshot.get("variable_values")
    if not isinstance(variables, dict) or str(variables.get("samp_rate")) != "48000":
        errors.append("samp_rate_not_48000")
    if not isinstance(variables, dict) or str(variables.get("demo_gain")) != "0.25":
        errors.append("demo_gain_missing_or_wrong")
    block_names = final_snapshot.get("block_names")
    if not isinstance(block_names, list) or "blocks_throttle2_demo" not in block_names:
        errors.append("demo_throttle_block_missing")
    connection_ids = final_snapshot.get("connection_ids")
    if not isinstance(connection_ids, list):
        errors.append("connection_ids_missing")
    else:
        required_connections = {
            "analog_sig_source_x_0:0->blocks_throttle2_demo:0",
            "blocks_throttle2_demo:0->blocks_add_xx:0",
        }
        missing = sorted(required_connections - {str(item) for item in connection_ids})
        if missing:
            errors.append(f"demo_throttle_connections_missing:{','.join(missing)}")
        if "analog_sig_source_x_0:0->blocks_add_xx:0" in connection_ids:
            errors.append("original_insert_connection_still_present")
    if final_snapshot.get("validation_status") != "valid":
        errors.append("final_graph_not_valid")
    requested_insert = False
    executed_insert = False
    for step in artifact.get("steps", []):
        if not isinstance(step, dict):
            continue
        for call in step.get("requested_tool_calls_raw", []):
            if not isinstance(call, dict):
                continue
            args = call.get("arguments")
            if (
                call.get("name") == "change_graph"
                and isinstance(args, dict)
                and args.get("operation_kind") == "insert_block"
            ):
                requested_insert = True
        for call in step.get("executed_tool_calls_raw", []):
            if not isinstance(call, dict):
                continue
            result = call.get("arguments")
            if (
                call.get("name") == "change_graph"
                and isinstance(result, dict)
                and result.get("operation_kind") == "insert_block"
                and result.get("ok") is True
            ):
                executed_insert = True
    if not requested_insert:
        errors.append("insert_tool_call_not_requested")
    if not executed_insert:
        errors.append("insert_tool_call_not_executed_ok")
    save_tools = {
        str(event.get("tool")): event
        for event in artifact.get("save_load_events", [])
        if isinstance(event, dict)
    }
    if save_tools.get("save_graph_explicit", {}).get("ok") is not True:
        errors.append("explicit_save_missing")
    if save_tools.get("load_graph_explicit", {}).get("ok") is not True:
        errors.append("explicit_load_missing")
    return errors


def run_demo(
    *,
    graph_path: Path,
    workdir: Path,
    artifact_path: Path | None = None,
    before_screenshot: Path | None = None,
    after_screenshot: Path | None = None,
    dry_run_docs_only: bool = False,
    max_tokens: int = 768,
    request_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    health_command = run_command(["uv", "run", "grc-agent", "health"], timeout_seconds=60)
    health = load_json_stdout(health_command)
    validate_health_ready(health, dry_run_docs_only=dry_run_docs_only)

    if dry_run_docs_only:
        artifact = {
            "schema_version": DEMO_ARTIFACT_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dry_run_docs_only": True,
            "health": health,
            "steps": [],
            "source_integrity": {"unchanged": True},
            "safety_requirements": {
                "original_graph_not_mutated": True,
                "validation_succeeded": False,
                "explicit_save": False,
                "raw_legacy_attempts": 0,
                "failed_validation_commits": 0,
                "debug_bundle_generated": False,
                "no_secrets_in_artifacts": True,
            },
        }
        target = artifact_path or workdir / DEFAULT_ARTIFACT_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return artifact

    if not graph_path.exists():
        raise DemoError(f"demo graph does not exist: {graph_path}")
    workdir.mkdir(parents=True, exist_ok=True)
    work_graph_path = workdir / DEFAULT_COPY_NAME
    debug_bundle_path = workdir / DEFAULT_DEBUG_BUNDLE_NAME
    artifact_target = artifact_path or workdir / DEFAULT_ARTIFACT_NAME
    artifact_target.parent.mkdir(parents=True, exist_ok=True)

    source_before_hash = sha256_file(graph_path)
    if work_graph_path.exists():
        work_graph_path.unlink()
    shutil.copy2(graph_path, work_graph_path)

    session = FlowgraphSession()
    session.load(work_graph_path)
    agent = GrcAgent(session)
    config = load_app_config()
    client = LlamaServerClient(
        base_url=config.llama.server_url,
        timeout_seconds=request_timeout_seconds or config.llama.request_timeout_seconds,
        max_tokens=min(config.llama.max_tokens, max_tokens),
        temperature=config.llama.temperature,
        enable_thinking=config.llama.enable_thinking,
    )
    client.require_model_alias(config.llama.model)

    initial_snapshot = graph_snapshot(agent)
    steps: list[dict[str, Any]] = []
    save_load_events: list[dict[str, Any]] = []
    for index, step in enumerate(default_demo_steps(work_graph_path)):
        print(f"demo step {index + 1}: {step['label']}", file=sys.stderr, flush=True)
        turn = run_agent_turn(
            agent=agent,
            client=client,
            model=config.llama.model,
            prompt=step["prompt"],
            label=step["label"],
            turn_index=index,
        )
        steps.append(turn)
        save_load_events.extend(turn["save_load_events"])

    final_snapshot = graph_snapshot(agent)
    debug_command = run_command(
        ["uv", "run", "grc-agent", "debug-bundle", "--output", str(debug_bundle_path)],
        timeout_seconds=120,
    )
    source_integrity = verify_source_unchanged(graph_path, source_before_hash)
    artifact: dict[str, Any] = {
        "schema_version": DEMO_ARTIFACT_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dry_run_docs_only": False,
        "classification": (
            "Release-validated subset + beta-validated graph operations; not production-ready"
        ),
        "health_command": health_command,
        "health": health,
        "runtime": {
            "cpu_llama_path_required": True,
            "llama_server_url": health.get("llama_server_url"),
            "model": health.get("llama_model"),
            "actual_context_tokens": health.get("llama_actual_context_tokens"),
            "desired_context_tokens": health.get("llama_desired_context_tokens"),
            "context_verified": health.get("llama_context_verified"),
        },
        "paths": {
            "source_graph_path": str(graph_path),
            "workdir": str(workdir),
            "work_graph_path": str(work_graph_path),
            "final_graph_path": str(work_graph_path),
            "artifact_path": str(artifact_target),
            "debug_bundle_path": str(debug_bundle_path),
            "before_screenshot": str(before_screenshot) if before_screenshot else None,
            "after_screenshot": str(after_screenshot) if after_screenshot else None,
        },
        "screenshots": {
            "before": {
                "path": str(before_screenshot) if before_screenshot else None,
                "exists": bool(before_screenshot and before_screenshot.exists()),
            },
            "after": {
                "path": str(after_screenshot) if after_screenshot else None,
                "exists": bool(after_screenshot and after_screenshot.exists()),
            },
        },
        "initial_graph_snapshot": initial_snapshot,
        "final_graph_snapshot": final_snapshot,
        "graph_delta": graph_delta(initial_snapshot, final_snapshot),
        "steps": steps,
        "turns": steps,
        "validation_results": [
            result for step in steps for result in step.get("validation_results", [])
        ],
        "save_load_events": save_load_events,
        "source_integrity": source_integrity,
        "debug_bundle": {
            "path": str(debug_bundle_path),
            "generated": debug_bundle_path.exists(),
            "command": debug_command,
        },
    }
    secret_scan = scan_secret_files([artifact_target, debug_bundle_path])
    artifact["safety_requirements"] = build_safety_summary(
        artifact=artifact,
        debug_bundle_path=debug_bundle_path,
        secret_scan=secret_scan,
    )
    artifact["demo_flow_errors"] = build_demo_flow_errors(artifact)
    artifact["secret_scan"] = secret_scan
    errors = validate_demo_artifact(artifact)
    artifact["artifact_validation_errors"] = errors
    text = json.dumps(artifact, indent=2, sort_keys=True)
    secret_hits = scan_secret_text(text)
    if secret_hits:
        raise DemoError(f"secret markers would be written to artifact: {secret_hits}")
    artifact_target.write_text(text + "\n", encoding="utf-8")

    final_secret_scan = scan_secret_files([artifact_target, debug_bundle_path])
    if final_secret_scan["bad_count"]:
        raise DemoError(f"secret markers found after artifact write: {final_secret_scan}")
    artifact["secret_scan"] = final_secret_scan
    artifact["artifact_validation_errors"] = validate_demo_artifact(artifact)
    artifact["demo_flow_errors"] = build_demo_flow_errors(artifact)
    artifact_target.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if artifact["artifact_validation_errors"]:
        raise DemoError(f"demo artifact validation failed: {artifact['artifact_validation_errors']}")
    if artifact["demo_flow_errors"]:
        raise DemoError(f"demo flow did not complete requested operations: {artifact['demo_flow_errors']}")
    return artifact


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the real GRC Agent demo pipeline.")
    parser.add_argument("--graph", default=str(DEFAULT_GRAPH), help="Source .grc graph to copy.")
    parser.add_argument("--workdir", default=str(DEFAULT_WORKDIR), help="Demo working directory.")
    parser.add_argument("--artifact", help="Output artifact path. Defaults to workdir/demo_artifact.json.")
    parser.add_argument("--before-screenshot", help="Optional before screenshot path.")
    parser.add_argument("--after-screenshot", help="Optional after screenshot path.")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=768,
        help="Per-turn llama max_tokens cap for the demo client.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=None,
        help="Optional demo-only llama request timeout override.",
    )
    parser.add_argument(
        "--dry-run-docs-only",
        action="store_true",
        help="Only write a non-demo docs artifact; bypasses health readiness refusal.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        artifact = run_demo(
            graph_path=Path(args.graph).expanduser().resolve(strict=False),
            workdir=Path(args.workdir).expanduser().resolve(strict=False),
            artifact_path=Path(args.artifact).expanduser().resolve(strict=False)
            if args.artifact
            else None,
            before_screenshot=Path(args.before_screenshot).expanduser().resolve(strict=False)
            if args.before_screenshot
            else None,
            after_screenshot=Path(args.after_screenshot).expanduser().resolve(strict=False)
            if args.after_screenshot
            else None,
            dry_run_docs_only=args.dry_run_docs_only,
            max_tokens=max(128, int(args.max_tokens)),
            request_timeout_seconds=args.request_timeout_seconds,
        )
    except DemoError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2
    summary = {
        "ok": True,
        "artifact_path": artifact.get("paths", {}).get("artifact_path"),
        "final_graph_path": artifact.get("paths", {}).get("final_graph_path"),
        "steps": len(artifact.get("steps", [])),
        "forbidden_events": artifact.get("safety_requirements", {}).get("forbidden_events", []),
        "secret_scan_bad_count": artifact.get("secret_scan", {}).get("bad_count"),
        "classification": artifact.get("classification"),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
