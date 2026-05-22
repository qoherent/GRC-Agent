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
from grc_agent.config import load_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import LlamaServerClient, run_bounded_llama_turn
from tests.llama_eval.harness import (
    executed_tool_calls_since,
    graph_delta,
    graph_snapshot,
    requested_tool_calls_since,
)
from tests.production.gameplay_judge import judge_artifact
from tests.production.ollama_readiness import readiness_report
from tests.production.ollama_user_client import (
    OllamaUserClient,
    OllamaUserClientError,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "corpus_manifest.json"
DEFAULT_ARTIFACT_DIR = Path("/tmp/grc_agent_gameplay")
DEFAULT_OLLAMA_SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios_ollama"
SECRET_MARKERS = ("ollama_key", "OLLAMA_API_KEY")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_scenario(
    *,
    scenario_path: Path,
    artifact_path: Path | None = None,
    manifest_path: Path = DEFAULT_MANIFEST,
    keep_workdir: bool = True,
    enable_ollama_network: bool = False,
    ollama_cloud_mode: bool = True,
    ollama_model: str | None = None,
    ollama_base_url: str | None = None,
    ollama_temperature: float = 0.2,
    ollama_seed: int | None = None,
    max_turns_override: int | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    scenario = load_json(scenario_path)
    if max_turns_override is not None:
        scenario = dict(scenario)
        scenario["max_turns"] = max_turns_override
        if scenario.get("user_mode") in {"ollama_user", "ollama_guided_user"}:
            scenario["max_user_turns"] = max_turns_override
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

    infra_failure: dict[str, Any] | None = None
    dummy_user: dict[str, Any] | None = None
    mode = str(scenario.get("user_mode", "scripted_user"))
    if mode == "ollama_user":
        dummy_user, infra_failure = _run_ollama_user_turns(
            scenario=scenario,
            agent=agent,
            initial_snapshot=initial_snapshot,
            work_graph_path=work_graph_path,
            source_path=source_path,
            save_path=save_path,
            conversation=conversation,
            turns=turns,
            save_load_events=save_load_events,
            enable_network=enable_ollama_network,
            cloud_mode=ollama_cloud_mode,
            model=ollama_model,
            base_url=ollama_base_url,
            temperature=ollama_temperature,
            seed=ollama_seed,
        )
    elif mode == "ollama_guided_user":
        guided_scenario = dict(scenario)
        guided_scenario["max_user_turns"] = 1
        dummy_user, infra_failure = _run_ollama_user_turns(
            scenario=guided_scenario,
            agent=agent,
            initial_snapshot=initial_snapshot,
            work_graph_path=work_graph_path,
            source_path=source_path,
            save_path=save_path,
            conversation=conversation,
            turns=turns,
            save_load_events=save_load_events,
            enable_network=enable_ollama_network,
            cloud_mode=ollama_cloud_mode,
            model=ollama_model,
            base_url=ollama_base_url,
            temperature=ollama_temperature,
            seed=ollama_seed,
        )
        if infra_failure is None:
            infra_failure = _run_direct_user_turns(
                scenario=scenario,
                agent=agent,
                work_graph_path=work_graph_path,
                source_path=source_path,
                save_path=save_path,
                conversation=conversation,
                turns=turns,
                save_load_events=save_load_events,
                start_index=len(turns),
            )
    elif mode == "direct_user":
        infra_failure = _run_direct_user_turns(
            scenario=scenario,
            agent=agent,
            work_graph_path=work_graph_path,
            source_path=source_path,
            save_path=save_path,
            conversation=conversation,
            turns=turns,
            save_load_events=save_load_events,
        )
    elif mode == "scripted_user":
        _run_scripted_turns(
            scenario=scenario,
            agent=agent,
            work_graph_path=work_graph_path,
            source_path=source_path,
            save_path=save_path,
            conversation=conversation,
            turns=turns,
            save_load_events=save_load_events,
        )
    else:
        raise ValueError(f"unsupported user_mode: {mode}")

    final_snapshot = graph_snapshot(agent)
    source_after = _sha256_file(source_path)
    artifact = {
        "schema_version": "2026-05-14.phase4-gameplay-artifact-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run": {
            "run_id": run_id,
            "scenario_id": scenario.get("scenario_id"),
            "provider": "cloud" if ollama_cloud_mode else "local",
            "model": ollama_model,
            "temperature": ollama_temperature,
            "seed": ollama_seed,
        },
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
        "dummy_user": dummy_user,
        "infra_failure": infra_failure,
        "grc_agent_failure": bool(
            isinstance(infra_failure, dict) and infra_failure.get("source") == "grc_agent"
        ),
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
    artifact["failure_category"] = classify_failure(artifact)
    sanitized = _redact(artifact)
    artifact_text = json.dumps(sanitized, indent=2, sort_keys=True)
    for marker in SECRET_MARKERS:
        if marker in artifact_text:
            raise RuntimeError(f"secret marker leaked into artifact: {marker}")
    artifact_target.write_text(artifact_text + "\n", encoding="utf-8")
    if not keep_workdir:
        shutil.rmtree(temp_root)
    return sanitized


def _run_scripted_turns(
    *,
    scenario: dict[str, Any],
    agent: GrcAgent,
    work_graph_path: Path,
    source_path: Path,
    save_path: Path,
    conversation: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    save_load_events: list[dict[str, Any]],
) -> None:
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
            _record_lifecycle_event(save_load_events, index, tool_name, result)
        _append_turn_trace(agent, turns, index, prompt, before, history_start)


def _run_ollama_user_turns(
    *,
    scenario: dict[str, Any],
    agent: GrcAgent,
    initial_snapshot: dict[str, Any],
    work_graph_path: Path,
    source_path: Path,
    save_path: Path,
    conversation: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    save_load_events: list[dict[str, Any]],
    enable_network: bool,
    cloud_mode: bool,
    model: str | None,
    base_url: str | None,
    temperature: float,
    seed: int | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    client = OllamaUserClient.from_environment(
        base_url=base_url,
        model=model,
        temperature=temperature,
        seed=seed,
        enabled=enable_network,
        cloud_mode=cloud_mode,
    )
    dummy_user = {
        "mode": "ollama_user",
        "provider": "cloud" if client.config.cloud_mode else "local",
        "cloud_used": bool(client.config.cloud_mode and enable_network),
        "network_enabled": bool(enable_network),
        "config": client.redacted_config(),
        "turns": [],
    }
    max_turns = int(scenario.get("max_user_turns", scenario.get("max_turns", 1)))
    app_config = load_app_config()
    grc_client = LlamaServerClient(
        base_url=app_config.llama.server_url,
        timeout_seconds=app_config.llama.request_timeout_seconds,
        max_tokens=app_config.llama.max_tokens,
        temperature=app_config.llama.temperature,
        enable_thinking=app_config.llama.enable_thinking,
    )
    for index in range(max_turns):
        try:
            generated = client.generate_user_turn(
                scenario_goal=str(
                    _render_templates(
                        scenario.get("scenario_goal", ""),
                        work_graph_path=work_graph_path,
                        source_path=source_path,
                        save_path=save_path,
                    )
                ),
                graph_summary=initial_snapshot,
                allowed_user_behavior=_string_list(
                    _render_templates(
                        scenario.get("allowed_user_behavior"),
                        work_graph_path=work_graph_path,
                        source_path=source_path,
                        save_path=save_path,
                    )
                ),
                forbidden_user_behavior=_string_list(
                    _render_templates(
                        scenario.get("forbidden_user_behavior"),
                        work_graph_path=work_graph_path,
                        source_path=source_path,
                        save_path=save_path,
                    )
                ),
                prior_conversation=conversation,
            )
        except OllamaUserClientError as exc:
            return dummy_user, {
                "source": "ollama_user",
                "error_type": exc.error_type,
                "message": str(exc),
            }
        prompt = _render_templates(
            str(generated["text"]),
            work_graph_path=work_graph_path,
            source_path=source_path,
            save_path=save_path,
        )
        dummy_user["turns"].append(
            {
                "turn_index": index,
                "latency_ms": generated.get("latency_ms"),
                "usage": generated.get("usage", {}),
                "prompt_chars": generated.get("prompt_chars"),
                "response_chars": generated.get("response_chars"),
            }
        )
        before = graph_snapshot(agent)
        history_start = len(agent.history)
        result: dict[str, Any] = {}
        error_text = ""
        try:
            result = run_bounded_llama_turn(
                agent=agent,
                client=grc_client,
                model=app_config.llama.model,
                user_message=prompt,
                advisor_enabled=app_config.agent.advisor_enabled,
                advisor_limited_advisory=app_config.agent.advisor_limited_advisory,
                advisor_shadow_telemetry=app_config.agent.advisor_shadow_telemetry,
                mvp_tool_profile=True,
            )
        except Exception as exc:  # pragma: no cover - exercised by integration only.
            error_text = str(exc)
        conversation.append({"role": "user", "content": prompt})
        assistant_text = result.get("assistant_text") if isinstance(result, dict) else ""
        if isinstance(assistant_text, str) and assistant_text:
            conversation.append({"role": "assistant", "content": assistant_text})
        for call in executed_tool_calls_since(agent.history, history_start):
            _record_lifecycle_event(
                save_load_events,
                index,
                str(call.get("name", "")),
                call.get("arguments") if isinstance(call.get("arguments"), dict) else {},
            )
        turn = _append_turn_trace(agent, turns, index, prompt, before, history_start)
        turn["dummy_user"] = dummy_user["turns"][-1]
        turn["agent_result"] = result
        turn["agent_error"] = error_text
        if error_text:
            return dummy_user, {
                "source": "grc_agent",
                "error_type": "agent_turn_error",
                "message": error_text,
            }
    return dummy_user, None


def _run_direct_user_turns(
    *,
    scenario: dict[str, Any],
    agent: GrcAgent,
    work_graph_path: Path,
    source_path: Path,
    save_path: Path,
    conversation: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    save_load_events: list[dict[str, Any]],
    start_index: int = 0,
) -> dict[str, Any] | None:
    turn_specs = scenario.get("scripted_user_turns")
    if not isinstance(turn_specs, list):
        raise ValueError("direct_user scenario scripted_user_turns must be a list")
    max_turns = int(scenario.get("max_turns", len(turn_specs)))
    if len(turn_specs) > max_turns:
        raise ValueError("scenario has more scripted turns than max_turns")

    app_config = load_app_config()
    grc_client = LlamaServerClient(
        base_url=app_config.llama.server_url,
        timeout_seconds=app_config.llama.request_timeout_seconds,
        max_tokens=app_config.llama.max_tokens,
        temperature=app_config.llama.temperature,
        enable_thinking=app_config.llama.enable_thinking,
    )
    for offset, turn_spec in enumerate(turn_specs):
        index = start_index + offset
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
        before = graph_snapshot(agent)
        history_start = len(agent.history)
        result: dict[str, Any] = {}
        error_text = ""
        try:
            result = run_bounded_llama_turn(
                agent=agent,
                client=grc_client,
                model=app_config.llama.model,
                user_message=str(prompt),
                advisor_enabled=app_config.agent.advisor_enabled,
                advisor_limited_advisory=app_config.agent.advisor_limited_advisory,
                advisor_shadow_telemetry=app_config.agent.advisor_shadow_telemetry,
                mvp_tool_profile=True,
            )
        except Exception as exc:  # pragma: no cover - exercised by integration only.
            error_text = str(exc)
        conversation.append({"role": "user", "content": prompt})
        assistant_text = result.get("assistant_text") if isinstance(result, dict) else ""
        if isinstance(assistant_text, str) and assistant_text:
            conversation.append({"role": "assistant", "content": assistant_text})
        for call in executed_tool_calls_since(agent.history, history_start):
            _record_lifecycle_event(
                save_load_events,
                index,
                str(call.get("name", "")),
                call.get("arguments") if isinstance(call.get("arguments"), dict) else {},
            )
        turn = _append_turn_trace(agent, turns, index, str(prompt), before, history_start)
        turn["agent_result"] = result
        turn["agent_error"] = error_text
        if error_text:
            return {
                "source": "grc_agent",
                "error_type": "agent_turn_error",
                "message": error_text,
            }
    return None


FAILURE_CATEGORIES = {
    "dummy_user_invalid_request",
    "dummy_user_underspecified",
    "dummy_user_missing_value",
    "dummy_user_missing_target",
    "graph_context_missing",
    "grc_agent_should_have_resolved_samp_rate",
    "grc_agent_correct_clarification",
    "judge_too_strict",
    "grc_agent_no_tool_call",
    "grc_agent_wrong_tool",
    "grc_agent_missing_arg",
    "runtime_refusal_safe",
    "runtime_bug",
    "judge_bug",
    "infra_failure",
    "timeout",
    "secret_redaction_failure",
    "forbidden_event",
    "passed",
}


def classify_failure(artifact: dict[str, Any]) -> str:
    """Assign one deterministic failure category for aggregate reporting."""
    if _artifact_has_secret_marker(artifact):
        return "secret_redaction_failure"
    if artifact.get("infra_failure") is not None:
        failure = artifact.get("infra_failure")
        if isinstance(failure, dict) and failure.get("error_type") in {
            "timeout",
            "network_error",
        }:
            return "timeout"
        return "infra_failure"
    judge = artifact.get("judge")
    if not isinstance(judge, dict):
        return "judge_bug"
    if judge.get("passed") is True:
        return "passed"
    if judge.get("forbidden_events"):
        return "forbidden_event"
    dimensions = judge.get("dimensions")
    if not isinstance(dimensions, dict):
        return "judge_bug"
    if dimensions.get("natural_user_quality") is False:
        return "dummy_user_invalid_request"
    expected = artifact.get("scenario")
    expected_tools = _expected_model_tools(expected if isinstance(expected, dict) else {})
    actual_tools = [
        str(tool)
        for turn in artifact.get("turns", [])
        if isinstance(turn, dict)
        for tool in turn.get("executed_tools", [])
    ]
    if isinstance(expected, dict):
        set_param_label = _classify_set_param_prompt_failure(artifact, expected)
        if set_param_label:
            return set_param_label
    if expected_tools and not actual_tools:
        return "grc_agent_no_tool_call"
    if expected_tools and not set(actual_tools).issubset(expected_tools):
        return "grc_agent_wrong_tool"
    if _has_invalid_request_tool_result(artifact):
        return "grc_agent_missing_arg"
    if _has_safe_refusal(artifact):
        return "runtime_refusal_safe"
    if dimensions.get("graph_delta_pass") is False or dimensions.get("final_state_pass") is False:
        return "dummy_user_underspecified"
    if artifact.get("grc_agent_failure") is True:
        return "runtime_bug"
    return "judge_bug"


def _classify_set_param_prompt_failure(
    artifact: dict[str, Any],
    scenario: dict[str, Any],
) -> str:
    expected_state = scenario.get("expected_final_state")
    if not isinstance(expected_state, dict):
        return ""
    variables = expected_state.get("variables")
    if not isinstance(variables, dict) or not variables:
        return ""
    prompt_text = "\n".join(
        str(turn.get("user_prompt", ""))
        for turn in artifact.get("turns", [])
        if isinstance(turn, dict)
    ).lower()
    expected_values = {str(value).lower() for value in variables.values()}
    value_present = any(value in prompt_text for value in expected_values)
    target_present = any(str(name).lower() in prompt_text for name in variables)
    sample_rate_present = "sample rate" in prompt_text
    graph_variables = artifact.get("initial_graph_snapshot", {}).get("variable_values")
    graph_has_samp_rate = isinstance(graph_variables, dict) and "samp_rate" in graph_variables
    graph_delta = artifact.get("graph_delta")
    wrong_mutation = isinstance(graph_delta, dict) and (
        graph_delta.get("variables") or graph_delta.get("block_params")
    )
    if not value_present:
        return "dummy_user_missing_value"
    if not graph_has_samp_rate:
        return "graph_context_missing"
    if sample_rate_present and value_present and graph_has_samp_rate and not target_present:
        return "grc_agent_should_have_resolved_samp_rate"
    if not target_present and not wrong_mutation:
        return "dummy_user_missing_target"
    return ""


def _artifact_has_secret_marker(artifact: dict[str, Any]) -> bool:
    text = json.dumps(artifact, sort_keys=True)
    return any(marker in text for marker in SECRET_MARKERS)


def _expected_model_tools(scenario: dict[str, Any]) -> set[str]:
    capabilities = {
        str(item) for item in scenario.get("allowed_capabilities", []) if isinstance(item, str)
    }
    tools: set[str] = set()
    if "R0_READ_ONLY" in capabilities:
        tools.update({"inspect_graph", "search_blocks", "ask_grc_docs"})
    if "R1_SET_PARAM_ONLY" in capabilities:
        tools.add("change_graph")
    if any(
        capability
        in {
            "R1_SET_STATE",
            "R2_DISCONNECT",
            "R3_REWIRE",
            "R4A_INSERT_BLOCK_ON_CONNECTION",
            "R4B_REMOVE_BLOCK",
            "R4C_ADD_VARIABLE",
            "Tier5_ADVERSARIAL",
        }
        for capability in capabilities
    ):
        tools.add("change_graph")
    if "R5_SAVE_LOAD" in capabilities:
        tools.update({"save_graph_explicit", "load_graph_explicit"})
    return tools


def _has_invalid_request_tool_result(artifact: dict[str, Any]) -> bool:
    for turn in artifact.get("turns", []):
        if not isinstance(turn, dict):
            continue
        for call in turn.get("executed_tool_calls_raw", []):
            args = call.get("arguments") if isinstance(call, dict) else None
            if isinstance(args, dict) and args.get("error_type") == "invalid_request":
                return True
    return False


def _has_safe_refusal(artifact: dict[str, Any]) -> bool:
    if artifact.get("graph_delta") not in ({}, None):
        return False
    for turn in artifact.get("turns", []):
        if not isinstance(turn, dict):
            continue
        for call in turn.get("executed_tool_calls_raw", []):
            args = call.get("arguments") if isinstance(call, dict) else None
            if isinstance(args, dict) and args.get("ok") is False:
                return True
    return False


def _append_turn_trace(
    agent: GrcAgent,
    turns: list[dict[str, Any]],
    index: int,
    prompt: str,
    before: dict[str, Any],
    history_start: int,
) -> dict[str, Any]:
    after = graph_snapshot(agent)
    requested = requested_tool_calls_since(agent.history, history_start)
    executed = executed_tool_calls_since(agent.history, history_start)
    turn = {
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
        "graph_delta": graph_delta(before, after),
        "validation_results": _validation_results(executed),
    }
    turns.append(turn)
    return turn


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


def _record_lifecycle_event(
    save_load_events: list[dict[str, Any]],
    turn_index: int,
    tool_name: str,
    result: dict[str, Any],
) -> None:
    if tool_name in {"save_graph_explicit", "load_graph_explicit"}:
        save_load_events.append(
            {
                "turn_index": turn_index,
                "tool": tool_name,
                "ok": bool(result.get("ok")),
                "path": result.get("path"),
            }
        )


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


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


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
        "failure_category": artifact.get("failure_category"),
        "artifact_path": artifact.get("paths", {}).get("artifact_path"),
    }


def run_repeated_ollama_config(
    *,
    config_path: Path,
    artifact_dir: Path,
    enable_ollama_network: bool,
    provider_override: str | None = None,
    model_override: str | None = None,
    temperature_override: float | None = None,
    n_runs_override: int | None = None,
    seed_override: int | None = None,
    max_turns_override: int | None = None,
    scenario_dir: Path = DEFAULT_OLLAMA_SCENARIO_DIR,
) -> dict[str, Any]:
    config = load_json(config_path)
    provider = provider_override or str(config.get("provider", "cloud"))
    model = model_override or str(config.get("model", "gemma3:4b"))
    temperature = (
        float(temperature_override)
        if temperature_override is not None
        else float(config.get("temperature", 0.0))
    )
    n_runs = int(n_runs_override) if n_runs_override is not None else int(config.get("n_runs", 1))
    seed = seed_override if seed_override is not None else config.get("seed")
    base_seed = int(seed) if seed is not None else None
    max_turns = (
        int(max_turns_override)
        if max_turns_override is not None
        else config.get("max_turns")
    )
    scenarios = config.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("config scenarios must be a non-empty list")
    if n_runs < 1:
        raise ValueError("n_runs must be >= 1")
    if provider not in {"local", "cloud"}:
        raise ValueError("provider must be local or cloud")

    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, Any]] = []
    for scenario_id in scenarios:
        scenario_path = scenario_dir / f"{scenario_id}.json"
        if not scenario_path.exists():
            raise FileNotFoundError(scenario_path)
        for run_index in range(1, n_runs + 1):
            run_seed = base_seed + run_index - 1 if base_seed is not None else None
            run_id = f"{scenario_id}_run_{run_index:02d}"
            artifact_path = artifact_dir / f"{run_id}.json"
            artifacts.append(
                run_scenario(
                    scenario_path=scenario_path,
                    artifact_path=artifact_path,
                    enable_ollama_network=enable_ollama_network,
                    ollama_cloud_mode=provider == "cloud",
                    ollama_model=model,
                    ollama_temperature=temperature,
                    ollama_seed=run_seed,
                    max_turns_override=int(max_turns) if max_turns is not None else None,
                    run_id=run_id,
                )
            )

    report = aggregate_ollama_runs(
        artifacts,
        config={
            "config_path": str(config_path),
            "scenario_dir": str(scenario_dir),
            "model": model,
            "provider": provider,
            "temperature": temperature,
            "n_runs": n_runs,
            "seed": base_seed,
            "max_turns": max_turns,
            "scenarios": [str(item) for item in scenarios],
            "network_enabled": bool(enable_ollama_network),
        },
        artifact_dir=artifact_dir,
    )
    report_path = artifact_dir / "aggregate_report.json"
    report_text = json.dumps(_redact(report), indent=2, sort_keys=True)
    for marker in SECRET_MARKERS:
        if marker in report_text:
            raise RuntimeError(f"secret marker leaked into aggregate report: {marker}")
    report_path.write_text(report_text + "\n", encoding="utf-8")
    return report


def aggregate_ollama_runs(
    artifacts: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    scenarios: dict[str, dict[str, Any]] = {}
    failures: dict[str, int] = {}
    total_turns = 0
    total_tool_calls = 0
    total_latency = 0
    latency_count = 0
    raw_legacy_attempts = 0
    failed_validation_commits = 0
    forbidden_event_count = 0
    runtime_safety_passes = 0
    model_contract_passes = 0

    for artifact in artifacts:
        scenario_id = str(artifact.get("scenario", {}).get("scenario_id", "unknown"))
        summary = scenarios.setdefault(
            scenario_id,
            {
                "runs": 0,
                "passes": 0,
                "failures": 0,
                "pass_rate": 0.0,
                "artifact_paths": [],
            },
        )
        summary["runs"] += 1
        judge = artifact.get("judge") if isinstance(artifact.get("judge"), dict) else {}
        if judge.get("passed") is True:
            summary["passes"] += 1
        else:
            summary["failures"] += 1
        paths = artifact.get("paths") if isinstance(artifact.get("paths"), dict) else {}
        summary["artifact_paths"].append(paths.get("artifact_path"))
        category = str(artifact.get("failure_category") or "judge_bug")
        failures[category] = failures.get(category, 0) + 1
        dimensions = judge.get("dimensions") if isinstance(judge.get("dimensions"), dict) else {}
        runtime_safety_passes += int(dimensions.get("runtime_safety_pass") is True)
        model_contract_passes += int(dimensions.get("model_contract_pass") is True)
        for event in judge.get("forbidden_events", []):
            if not isinstance(event, dict):
                continue
            forbidden_event_count += 1
            if event.get("event") == "raw_legacy_tool_call":
                raw_legacy_attempts += 1
            if event.get("event") == "failed_validation_commit":
                failed_validation_commits += 1
        turns = artifact.get("turns") if isinstance(artifact.get("turns"), list) else []
        total_turns += len(turns)
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            calls = turn.get("requested_tool_calls_raw")
            if isinstance(calls, list):
                total_tool_calls += len(calls)
        dummy = artifact.get("dummy_user") if isinstance(artifact.get("dummy_user"), dict) else {}
        for turn in dummy.get("turns", []) if isinstance(dummy.get("turns"), list) else []:
            if isinstance(turn, dict) and isinstance(turn.get("latency_ms"), int):
                total_latency += int(turn["latency_ms"])
                latency_count += 1

    total_runs = len(artifacts)
    for summary in scenarios.values():
        summary["pass_rate"] = (
            summary["passes"] / summary["runs"] if summary["runs"] else 0.0
        )
    return {
        "schema_version": "2026-05-15.phase5-ollama-aggregate-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "total_runs": total_runs,
        "overall_pass_rate": (
            sum(1 for artifact in artifacts if artifact.get("judge", {}).get("passed") is True)
            / total_runs
            if total_runs
            else 0.0
        ),
        "scenarios": scenarios,
        "failure_categories": failures,
        "runtime_safety_rate": runtime_safety_passes / total_runs if total_runs else 0.0,
        "model_contract_rate": model_contract_passes / total_runs if total_runs else 0.0,
        "forbidden_event_count": forbidden_event_count,
        "raw_legacy_attempt_count": raw_legacy_attempts,
        "failed_validation_commit_count": failed_validation_commits,
        "average_turns": total_turns / total_runs if total_runs else 0.0,
        "average_tool_calls": total_tool_calls / total_runs if total_runs else 0.0,
        "average_dummy_user_latency_ms": (
            total_latency / latency_count if latency_count else None
        ),
        "artifact_dir": str(artifact_dir),
        "aggregate_path": str(artifact_dir / "aggregate_report.json"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--scenario-dir", type=Path, default=DEFAULT_OLLAMA_SCENARIO_DIR)
    parser.add_argument("--n-runs", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--max-turns", type=int)
    parser.add_argument("--provider", choices=("local", "cloud"))
    parser.add_argument(
        "--enable-ollama-network",
        action="store_true",
        help="Allow ollama_user scenarios to call Ollama. Disabled by default.",
    )
    parser.add_argument(
        "--ollama-local",
        action="store_true",
        help="Use local Ollama API mode for dummy-user generation.",
    )
    parser.add_argument("--ollama-model")
    parser.add_argument("--model", dest="model")
    parser.add_argument("--ollama-base-url")
    args = parser.parse_args(argv)
    model = args.model or args.ollama_model
    if args.config is not None:
        artifact_dir = args.artifact_dir or DEFAULT_ARTIFACT_DIR
        report = run_repeated_ollama_config(
            config_path=args.config,
            artifact_dir=artifact_dir,
            enable_ollama_network=bool(args.enable_ollama_network),
            provider_override=args.provider,
            model_override=model,
            temperature_override=args.temperature,
            n_runs_override=args.n_runs,
            seed_override=args.seed,
            max_turns_override=args.max_turns,
            scenario_dir=args.scenario_dir,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["overall_pass_rate"] == 1.0 else 1
    if args.scenario is None:
        parser.error("--scenario is required unless --config is provided")
    n_runs = int(args.n_runs or 1)
    if n_runs < 1:
        parser.error("--n-runs must be >= 1")
    if n_runs > 1:
        artifact_dir = args.artifact_dir or DEFAULT_ARTIFACT_DIR
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifacts = []
        scenario_id = load_json(args.scenario).get("scenario_id", args.scenario.stem)
        for run_index in range(1, n_runs + 1):
            run_seed = args.seed + run_index - 1 if args.seed is not None else None
            run_id = f"{scenario_id}_run_{run_index:02d}"
            artifacts.append(
                run_scenario(
                    scenario_path=args.scenario,
                    artifact_path=artifact_dir / f"{run_id}.json",
                    manifest_path=args.manifest,
                    enable_ollama_network=bool(args.enable_ollama_network),
                    ollama_cloud_mode=(args.provider or "cloud") == "cloud",
                    ollama_model=model,
                    ollama_base_url=args.ollama_base_url,
                    ollama_temperature=(
                        args.temperature if args.temperature is not None else 0.2
                    ),
                    ollama_seed=run_seed,
                    max_turns_override=args.max_turns,
                    run_id=run_id,
                )
            )
        report = aggregate_ollama_runs(
            artifacts,
            config={
                "model": model,
                "provider": args.provider or "cloud",
                "temperature": args.temperature if args.temperature is not None else 0.2,
                "n_runs": n_runs,
                "seed": args.seed,
                "max_turns": args.max_turns,
                "scenarios": [str(scenario_id)],
                "network_enabled": bool(args.enable_ollama_network),
            },
            artifact_dir=artifact_dir,
        )
        report_path = artifact_dir / "aggregate_report.json"
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["overall_pass_rate"] == 1.0 else 1
    artifact = run_scenario(
        scenario_path=args.scenario,
        artifact_path=args.artifact,
        manifest_path=args.manifest,
        enable_ollama_network=bool(args.enable_ollama_network),
        ollama_cloud_mode=(args.provider or ("local" if args.ollama_local else "cloud"))
        == "cloud",
        ollama_model=model,
        ollama_base_url=args.ollama_base_url,
        ollama_temperature=args.temperature if args.temperature is not None else 0.2,
        ollama_seed=args.seed,
        max_turns_override=args.max_turns,
    )
    summary = _summary(artifact)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
