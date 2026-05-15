"""Deterministic scripted gameplay runner for Phase 2 evidence artifacts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from tests.llama_eval.harness import (
    executed_tool_calls_since,
    graph_delta,
    graph_snapshot,
    requested_tool_calls_since,
)
from tests.production.gameplay_judge import judge_artifact
from tests.production.ollama_readiness import readiness_report

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "corpus_manifest.json"
DEFAULT_ARTIFACT_DIR = Path("/tmp/grc_agent_gameplay")
SECRET_MARKERS = ("ollama_key", "OLLAMA_API_KEY")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_scenario(
    *,
    scenario_path: Path,
    artifact_path: Path | None = None,
    manifest_path: Path = DEFAULT_MANIFEST,
    keep_workdir: bool = True,
) -> dict[str, Any]:
    scenario = load_json(scenario_path)
    manifest = load_json(manifest_path)
    corpus_entry = _corpus_entry(manifest, str(scenario["graph_id"]))

    artifact_target = artifact_path or _default_artifact_path(str(scenario["scenario_id"]))
    artifact_target.parent.mkdir(parents=True, exist_ok=True)

    temp_root = Path(tempfile.mkdtemp(prefix=f"grc_gameplay_{scenario['scenario_id']}_"))
    work_graph_path = temp_root / Path(str(corpus_entry["source_path"])).name
    source_path = _resolve_source_path(str(corpus_entry["source_path"]))
    source_before = _sha256_file(source_path)
    shutil.copy2(source_path, work_graph_path)
    save_path = temp_root / f"{scenario['scenario_id']}_saved.grc"

    session = FlowgraphSession()
    session.load(work_graph_path)
    agent = GrcAgent(session)
    initial_snapshot = graph_snapshot(agent)
    turns: list[dict[str, Any]] = []
    conversation: list[dict[str, Any]] = []
    save_load_events: list[dict[str, Any]] = []

    turn_specs = scenario.get("scripted_user_turns")
    if not isinstance(turn_specs, list):
        raise ValueError("scenario scripted_user_turns must be a list")
    max_turns = int(scenario.get("max_turns", len(turn_specs)))
    if len(turn_specs) > max_turns:
        raise ValueError("scenario has more scripted turns than max_turns")

    for index, turn_spec in enumerate(turn_specs):
        prompt_template = (
            turn_spec.get("user_prompt")
            if isinstance(turn_spec, dict) and turn_spec.get("user_prompt")
            else scenario.get("initial_user_prompt")
        )
        if not isinstance(prompt_template, str) or not prompt_template.strip():
            raise ValueError(f"turn {index} has no user prompt")
        prompt = _render_templates(
            prompt_template,
            work_graph_path=work_graph_path,
            source_path=source_path,
            save_path=save_path,
        )
        actions = turn_spec.get("assistant_actions") if isinstance(turn_spec, dict) else None
        if not isinstance(actions, list):
            raise ValueError(f"turn {index} assistant_actions must be a list")

        before = graph_snapshot(agent)
        history_start = len(agent.history)
        agent.init_turn_requirements(prompt)
        agent.history.append({"role": "user", "content": prompt})
        conversation.append({"role": "user", "content": prompt})

        for action in actions:
            if not isinstance(action, dict):
                raise ValueError(f"turn {index} action must be an object")
            if "text" in action:
                text = str(action["text"])
                agent.history.append({"role": "assistant", "content": text})
                conversation.append({"role": "assistant", "content": text})
                continue
            tool_name = str(action.get("tool", ""))
            kwargs = _render_templates(
                action.get("kwargs", {}),
                work_graph_path=work_graph_path,
                source_path=source_path,
                save_path=save_path,
            )
            if not tool_name or not isinstance(kwargs, dict):
                raise ValueError(f"turn {index} has invalid tool action")
            agent.history.append(
                {
                    "role": "assistant",
                    "tool_calls": [{"name": tool_name, "arguments": kwargs}],
                }
            )
            conversation.append(
                {
                    "role": "assistant",
                    "tool_calls": [{"name": tool_name, "arguments": kwargs}],
                }
            )
            result = agent.execute_tool(tool_name, kwargs, model_tool_call=True)
            agent.history.append({"role": "tool", "name": tool_name, "content": result})
            conversation.append({"role": "tool", "name": tool_name, "content": result})
            if tool_name in {"save_graph_explicit", "load_graph_explicit"}:
                save_load_events.append(
                    {
                        "turn_index": index,
                        "tool": tool_name,
                        "ok": bool(result.get("ok")),
                        "path": result.get("path"),
                    }
                )

        after = graph_snapshot(agent)
        requested = requested_tool_calls_since(agent.history, history_start)
        executed = executed_tool_calls_since(agent.history, history_start)
        turn_delta = graph_delta(before, after)
        turns.append(
            {
                "turn_index": index,
                "user_prompt": prompt,
                "requested_tool_calls_raw": requested,
                "normalized_args": [
                    {
                        "name": call.get("name"),
                        "arguments": call.get("arguments"),
                    }
                    for call in requested
                ],
                "executed_tool_calls_raw": executed,
                "executed_tools": [call.get("name") for call in executed],
                "tool_results": [
                    {
                        "name": call.get("name"),
                        "result": call.get("arguments"),
                    }
                    for call in executed
                ],
                "graph_snapshot_before": before,
                "graph_snapshot_after": after,
                "graph_revision_before": before.get("state_revision"),
                "graph_revision_after": after.get("state_revision"),
                "graph_delta": turn_delta,
                "validation_results": _validation_results(executed),
            }
        )

    final_snapshot = graph_snapshot(agent)
    source_after = _sha256_file(source_path)
    artifact = {
        "schema_version": "2026-05-14.phase3-gameplay-artifact-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scenario": scenario,
        "corpus_entry": corpus_entry,
        "paths": {
            "source_path": str(source_path),
            "work_dir": str(temp_root),
            "work_graph_path": str(work_graph_path),
            "save_path": str(save_path),
            "artifact_path": str(artifact_target),
        },
        "ollama_readiness": readiness_report(check_cloud=False),
        "conversation": conversation,
        "turns": turns,
        "initial_graph_snapshot": initial_snapshot,
        "final_graph_snapshot": final_snapshot,
        "graph_delta": graph_delta(initial_snapshot, final_snapshot),
        "validation_results": [item for turn in turns for item in turn["validation_results"]],
        "save_load_events": save_load_events,
        "source_integrity": {
            "before_sha256": source_before,
            "after_sha256": source_after,
            "unchanged": source_before == source_after,
        },
        "final_state_summary": {
            "dirty": final_snapshot.get("dirty"),
            "validation_status": final_snapshot.get("validation_status"),
            "state_revision": final_snapshot.get("state_revision"),
            "block_count": final_snapshot.get("block_count"),
            "connection_count": final_snapshot.get("connection_count"),
            "variables": final_snapshot.get("variable_values"),
        },
    }
    artifact["judge"] = judge_artifact(artifact)
    artifact["forbidden_events"] = artifact["judge"].get("forbidden_events", [])
    sanitized = _redact(artifact)
    artifact_text = json.dumps(sanitized, indent=2, sort_keys=True)
    for marker in SECRET_MARKERS:
        if marker in artifact_text:
            raise RuntimeError(f"secret marker leaked into artifact: {marker}")
    artifact_target.write_text(artifact_text + "\n", encoding="utf-8")
    if not keep_workdir:
        shutil.rmtree(temp_root)
    return sanitized


def _validation_results(executed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for call in executed:
        args = call.get("arguments")
        if not isinstance(args, dict):
            continue
        validation = args.get("validation_result")
        if isinstance(validation, dict):
            results.append({"tool": call.get("name"), "validation_result": validation})
    return results


def _corpus_entry(manifest: dict[str, Any], graph_id: str) -> dict[str, Any]:
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ValueError("manifest entries must be a list")
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id") == graph_id:
            return dict(entry)
    raise ValueError(f"unknown graph_id: {graph_id}")


def _resolve_source_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _default_artifact_path(scenario_id: str) -> Path:
    return DEFAULT_ARTIFACT_DIR / f"{scenario_id}.json"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _render_templates(
    value: Any,
    *,
    work_graph_path: Path,
    source_path: Path,
    save_path: Path,
) -> Any:
    if isinstance(value, str):
        return value.format(
            work_graph_path=str(work_graph_path),
            source_path=str(source_path),
            save_path=str(save_path),
        )
    if isinstance(value, list):
        return [
            _render_templates(
                item,
                work_graph_path=work_graph_path,
                source_path=source_path,
                save_path=save_path,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _render_templates(
                item,
                work_graph_path=work_graph_path,
                source_path=source_path,
                save_path=save_path,
            )
            for key, item in value.items()
        }
    return value


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if "authorization" in lowered or "api_key" in lowered:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _summary(artifact: dict[str, Any]) -> dict[str, Any]:
    judge = artifact.get("judge") if isinstance(artifact.get("judge"), dict) else {}
    return {
        "passed": bool(judge.get("passed")),
        "scenario_id": artifact.get("scenario", {}).get("scenario_id"),
        "turns": len(artifact.get("turns", [])),
        "tool_calls": sum(
            len(turn.get("requested_tool_calls_raw", []))
            for turn in artifact.get("turns", [])
            if isinstance(turn, dict)
        ),
        "graph_delta": artifact.get("graph_delta"),
        "mutation_count": sum(
            1
            for turn in artifact.get("turns", [])
            if isinstance(turn, dict)
            and turn.get("graph_snapshot_before", {}).get("raw_hash")
            != turn.get("graph_snapshot_after", {}).get("raw_hash")
        ),
        "validation_status": artifact.get("final_graph_snapshot", {}).get("validation_status"),
        "forbidden_events": judge.get("forbidden_events", []),
        "artifact_path": artifact.get("paths", {}).get("artifact_path"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    artifact = run_scenario(
        scenario_path=args.scenario,
        artifact_path=args.artifact,
        manifest_path=args.manifest,
    )
    summary = _summary(artifact)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
