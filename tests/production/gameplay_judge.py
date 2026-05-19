"""Deterministic judge for Phase 2 production-readiness gameplay artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

MVP_MODEL_TOOLS = {
    "inspect_graph",
    "search_blocks",
    "ask_grc_docs",
    "change_graph",
    "save_graph_explicit",
    "load_graph_explicit",
}


def judge_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    """Grade one gameplay artifact without using an LLM."""
    forbidden_events = detect_forbidden_events(artifact)
    expected = artifact.get("scenario", {})
    infra_failure = isinstance(artifact.get("infra_failure"), dict)
    dimensions = {
        "task_success": False,
        "infra_failure": infra_failure,
        "grc_agent_failure": bool(artifact.get("grc_agent_failure")),
        "runtime_safety_pass": not forbidden_events,
        "model_contract_pass": _model_contract_pass(artifact),
        "natural_user_quality": _natural_user_quality_pass(artifact),
        "graph_delta_pass": _graph_delta_pass(artifact, expected),
        "validation_pass": _validation_pass(artifact, expected),
        "clarification_quality_pass": _clarification_quality_pass(artifact, expected),
        "guided_workflow_pass": _guided_workflow_pass(artifact, expected),
        "refusal_pass": _refusal_pass(artifact, expected),
        "save_load_safety_pass": _save_load_safety_pass(artifact, expected, forbidden_events),
        "forbidden_events_count": len(forbidden_events),
        "final_state_pass": _final_state_pass(artifact, expected),
    }
    dimensions["task_success"] = all(
        bool(dimensions[key])
        for key in (
            "runtime_safety_pass",
            "model_contract_pass",
            "natural_user_quality",
            "graph_delta_pass",
            "validation_pass",
            "clarification_quality_pass",
            "guided_workflow_pass",
            "refusal_pass",
            "save_load_safety_pass",
            "final_state_pass",
        )
    ) and not infra_failure
    return {
        "schema_version": "2026-05-14.phase2-judge-v1",
        "scenario_id": expected.get("scenario_id"),
        "passed": bool(dimensions["task_success"]),
        "dimensions": dimensions,
        "forbidden_events": forbidden_events,
    }


def detect_forbidden_events(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    turns = artifact.get("turns")
    if not isinstance(turns, list):
        return [{"event": "malformed_artifact", "detail": "turns missing"}]

    for index, turn in enumerate(turns):
        requested = turn.get("requested_tool_calls_raw")
        executed = turn.get("executed_tool_calls_raw")
        if not isinstance(requested, list) or not isinstance(executed, list):
            events.append({"event": "malformed_artifact", "turn_index": index})
            continue
        for key, calls in (
            ("requested_tool_calls_raw", requested),
            ("executed_tool_calls_raw", executed),
        ):
            for call in calls:
                name = _call_name(call)
                if name and name not in MVP_MODEL_TOOLS:
                    events.append(
                        {
                            "event": "raw_legacy_tool_call",
                            "turn_index": index,
                            "field": key,
                            "tool": name,
                        }
                    )
                if _contains_raw_yaml_mutation(call):
                    events.append(
                        {
                            "event": "raw_yaml_mutation",
                            "turn_index": index,
                            "field": key,
                            "tool": name,
                        }
                    )
        if _turn_has_preview_mutation(turn):
            events.append({"event": "preview_mutation", "turn_index": index})
        if _turn_has_failed_validation_commit(turn):
            events.append({"event": "failed_validation_commit", "turn_index": index})
        events.extend(_unsafe_lifecycle_events(turn, artifact, index))

    source = artifact.get("source_integrity")
    if isinstance(source, dict) and source.get("before_sha256") != source.get("after_sha256"):
        events.append({"event": "original_graph_mutation"})
    return events


def _model_contract_pass(artifact: dict[str, Any]) -> bool:
    turns = artifact.get("turns")
    if not isinstance(turns, list):
        return False
    for turn in turns:
        if not isinstance(turn.get("requested_tool_calls_raw"), list):
            return False
        if not isinstance(turn.get("executed_tool_calls_raw"), list):
            return False
    return not any(
        event.get("event") in {"raw_legacy_tool_call", "malformed_artifact"}
        for event in detect_forbidden_events(artifact)
    )


def _natural_user_quality_pass(artifact: dict[str, Any]) -> bool:
    scenario = artifact.get("scenario")
    if not isinstance(scenario, dict) or scenario.get("user_mode") not in {
        "ollama_user",
        "ollama_guided_user",
    }:
        return True
    dummy_user = artifact.get("dummy_user")
    if not isinstance(dummy_user, dict):
        return False
    if artifact.get("infra_failure") is not None:
        return True
    turns = artifact.get("turns")
    if not isinstance(turns, list) or not turns:
        return False
    forbidden_fragments = [
        "change_graph",
        "inspect_graph",
        "save_graph_explicit",
        "load_graph_explicit",
        "expected_final_state",
        "expected_graph_delta",
        "judge",
        "schema",
        "json",
    ]
    for turn in turns:
        if scenario.get("user_mode") == "ollama_guided_user" and not isinstance(
            turn.get("dummy_user"),
            dict,
        ):
            continue
        prompt = str(turn.get("user_prompt", "")).lower()
        if not prompt.strip():
            return False
        if any(fragment in prompt for fragment in forbidden_fragments):
            return False
    return True


def _graph_delta_pass(artifact: dict[str, Any], scenario: dict[str, Any]) -> bool:
    expected = scenario.get("expected_graph_delta")
    if not isinstance(expected, dict):
        return True
    actual = artifact.get("graph_delta")
    if not isinstance(actual, dict):
        return False
    if expected.get("no_content_change") is True:
        return _content_delta_empty(actual)
    exact = expected.get("exact")
    if isinstance(exact, dict) and not _partial_dict_match(actual, exact):
        return False
    must_include = expected.get("must_include")
    if isinstance(must_include, dict):
        return _partial_dict_match(actual, must_include)
    return True


def _validation_pass(artifact: dict[str, Any], scenario: dict[str, Any]) -> bool:
    expected = str(scenario.get("expected_validation_status", "any"))
    if expected == "any":
        return True
    final_snapshot = artifact.get("final_graph_snapshot")
    if not isinstance(final_snapshot, dict):
        return False
    return str(final_snapshot.get("validation_status")) == expected


def _clarification_quality_pass(artifact: dict[str, Any], scenario: dict[str, Any]) -> bool:
    expected = scenario.get("expected_clarification")
    if not isinstance(expected, dict) or not expected.get("required"):
        return True
    fragments = [str(item).lower() for item in expected.get("assistant_text_contains", [])]
    if fragments and _assistant_text_contains_all(artifact, fragments):
        return True
    for result in _tool_result_payloads(artifact):
        if isinstance(result.get("clarification_options"), list):
            return True
        if result.get("error_type") in {
            "ambiguous_connection",
            "ambiguous_target",
            "clarification_required",
        }:
            return True
    return False


def _refusal_pass(artifact: dict[str, Any], scenario: dict[str, Any]) -> bool:
    expected = scenario.get("expected_refusal")
    if not isinstance(expected, dict) or not expected.get("required"):
        return True
    if expected.get("no_content_change") is True:
        actual = artifact.get("graph_delta")
        if not isinstance(actual, dict) or not _content_delta_empty(actual):
            return False
    fragments = [str(item).lower() for item in expected.get("assistant_text_contains", [])]
    if fragments and not _assistant_text_contains_all(artifact, fragments):
        return False
    error_types = {str(item) for item in expected.get("error_types", [])}
    if error_types:
        actual_error_types = {
            str(result.get("error_type"))
            for result in _tool_result_payloads(artifact)
            if result.get("error_type") is not None
        }
        if not error_types & actual_error_types:
            return False
    if not fragments and not error_types:
        return any(result.get("ok") is False for result in _tool_result_payloads(artifact))
    return True


def _guided_workflow_pass(artifact: dict[str, Any], scenario: dict[str, Any]) -> bool:
    expected = scenario.get("expected_guided_workflow")
    if not isinstance(expected, dict):
        return True
    turns = artifact.get("turns")
    if not isinstance(turns, list):
        return False
    if len(turns) < int(expected.get("min_turns", 2)):
        return False
    first_turn = turns[0] if isinstance(turns[0], dict) else {}
    if expected.get("first_turn_no_content_change") is True:
        first_delta = first_turn.get("graph_delta")
        if not isinstance(first_delta, dict) or not _content_delta_empty(first_delta):
            return False
    if expected.get("first_turn_requires_clarification_or_inspect") is True:
        first_requested_tools = {
            _call_name(call) for call in first_turn.get("requested_tool_calls_raw", [])
        }
        first_executed_tools = {
            _call_name(call) for call in first_turn.get("executed_tool_calls_raw", [])
        }
        if "inspect_graph" not in first_requested_tools | first_executed_tools:
            assistant_text = _assistant_text_for_turn(artifact, 0)
            clarification_words = ("clarification", "specify", "which", "provide")
            tool_clarified = any(
                _call_arguments(call).get("error_type")
                in {"clarification_required", "ambiguous_connection", "ambiguous_target"}
                for call in first_turn.get("executed_tool_calls_raw", [])
                if isinstance(_call_arguments(call), dict)
            )
            if (
                not tool_clarified
                and not any(word in assistant_text.lower() for word in clarification_words)
            ):
                return False
    mutation_turn_index = int(expected.get("mutation_turn_index", len(turns) - 1))
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            return False
        delta = turn.get("graph_delta")
        changed = isinstance(delta, dict) and not _content_delta_empty(delta)
        if index < mutation_turn_index and changed:
            return False
    if expected.get("mutation_after_exact_selection") is True:
        if mutation_turn_index >= len(turns):
            return False
        mutation_delta = turns[mutation_turn_index].get("graph_delta")
        if not isinstance(mutation_delta, dict) or _content_delta_empty(mutation_delta):
            return False
    return True


def _save_load_safety_pass(
    artifact: dict[str, Any],
    scenario: dict[str, Any],
    forbidden_events: list[dict[str, Any]],
) -> bool:
    if any(event.get("event") in {"unsafe_save", "unsafe_load"} for event in forbidden_events):
        return False
    expected = scenario.get("expected_save_load_behavior")
    if not isinstance(expected, dict):
        return True
    events = artifact.get("save_load_events")
    if not isinstance(events, list):
        return False
    saved = any(event.get("tool") == "save_graph_explicit" and event.get("ok") for event in events)
    loaded = any(event.get("tool") == "load_graph_explicit" and event.get("ok") for event in events)
    if bool(expected.get("save_expected")) != saved:
        return False
    if bool(expected.get("load_expected")) != loaded:
        return False
    return True


def _final_state_pass(artifact: dict[str, Any], scenario: dict[str, Any]) -> bool:
    expected = scenario.get("expected_final_state")
    if not isinstance(expected, dict):
        return True
    snapshot = artifact.get("final_graph_snapshot")
    if not isinstance(snapshot, dict):
        return False
    if "dirty" in expected and bool(snapshot.get("dirty")) != bool(expected["dirty"]):
        return False
    variables = expected.get("variables")
    if isinstance(variables, dict):
        actual_vars = snapshot.get("variable_values")
        if not isinstance(actual_vars, dict):
            return False
        for key, value in variables.items():
            if str(actual_vars.get(key)) != str(value):
                return False
    blocks = expected.get("blocks")
    if isinstance(blocks, dict):
        names = set(_string_list(snapshot.get("block_names")))
        if any(name not in names for name in _string_list(blocks.get("present"))):
            return False
        if any(name in names for name in _string_list(blocks.get("absent"))):
            return False
    connections = expected.get("connections")
    if isinstance(connections, dict):
        ids = set(_string_list(snapshot.get("connection_ids")))
        if any(item not in ids for item in _string_list(connections.get("present"))):
            return False
        if any(item in ids for item in _string_list(connections.get("absent"))):
            return False
    return True


def _turn_has_preview_mutation(turn: dict[str, Any]) -> bool:
    if not _turn_graph_changed(turn):
        return False
    for call in _all_turn_calls(turn):
        if _call_name(call) != "change_graph":
            continue
        args = _call_arguments(call)
        if isinstance(args, dict) and args.get("dry_run") is True:
            return True
    return False


def _turn_has_failed_validation_commit(turn: dict[str, Any]) -> bool:
    if not _turn_graph_changed(turn):
        return False
    for call in turn.get("executed_tool_calls_raw", []):
        args = _call_arguments(call)
        if not isinstance(args, dict):
            continue
        validation = args.get("validation_result")
        if isinstance(validation, dict):
            if validation.get("status") == "invalid" or validation.get("valid") is False:
                return True
        if args.get("ok") is False and args.get("error_type") == "gnu_validation_failed":
            return True
    after = turn.get("graph_snapshot_after")
    return isinstance(after, dict) and after.get("validation_status") == "invalid"


def _unsafe_lifecycle_events(
    turn: dict[str, Any],
    artifact: dict[str, Any],
    turn_index: int,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    paths = artifact.get("paths") if isinstance(artifact.get("paths"), dict) else {}
    source = str(paths.get("source_path", ""))
    work_dir = str(paths.get("work_dir", ""))
    for call in turn.get("executed_tool_calls_raw", []):
        name = _call_name(call)
        args = _call_arguments(call)
        if not isinstance(args, dict) or args.get("ok") is not True:
            continue
        path = str(args.get("path", ""))
        if name == "save_graph_explicit" and (path == source or not path.startswith(work_dir)):
            events.append({"event": "unsafe_save", "turn_index": turn_index, "path": path})
        if name == "load_graph_explicit" and path == source:
            events.append({"event": "unsafe_load", "turn_index": turn_index, "path": path})
    return events


def _turn_graph_changed(turn: dict[str, Any]) -> bool:
    before = turn.get("graph_snapshot_before")
    after = turn.get("graph_snapshot_after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    return before.get("raw_hash") != after.get("raw_hash")


def _all_turn_calls(turn: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for key in ("requested_tool_calls_raw", "executed_tool_calls_raw"):
        value = turn.get(key)
        if isinstance(value, list):
            calls.extend(item for item in value if isinstance(item, dict))
    return calls


def _call_name(call: Any) -> str:
    if not isinstance(call, dict):
        return ""
    return str(call.get("name") or "")


def _call_arguments(call: Any) -> Any:
    if not isinstance(call, dict):
        return None
    return call.get("arguments")


def _contains_raw_yaml_mutation(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"raw_yaml", "yaml_patch", "yaml_diff"}:
                return True
            if _contains_raw_yaml_mutation(item):
                return True
    if isinstance(value, list):
        return any(_contains_raw_yaml_mutation(item) for item in value)
    return False


def _content_delta_empty(delta: dict[str, Any]) -> bool:
    content_keys = {
        "added_blocks",
        "removed_blocks",
        "added_connections",
        "removed_connections",
        "variables",
        "block_params",
        "block_states",
    }
    return all(not delta.get(key) for key in content_keys)


def _partial_dict_match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        for key, expected_value in expected.items():
            if key not in actual:
                return False
            if not _partial_dict_match(actual[key], expected_value):
                return False
        return True
    return str(actual) == str(expected)


def _tool_result_payloads(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    turns = artifact.get("turns")
    if not isinstance(turns, list):
        return payloads
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        for call in turn.get("executed_tool_calls_raw", []):
            args = _call_arguments(call)
            if isinstance(args, dict):
                payloads.append(args)
    return payloads


def _assistant_text_contains_all(artifact: dict[str, Any], fragments: list[str]) -> bool:
    texts: list[str] = []
    conversation = artifact.get("conversation")
    if isinstance(conversation, list):
        for entry in conversation:
            if isinstance(entry, dict) and entry.get("role") == "assistant":
                content = entry.get("content")
                if isinstance(content, str):
                    texts.append(content.lower())
    joined = "\n".join(texts)
    return all(fragment in joined for fragment in fragments)


def _assistant_text_for_turn(artifact: dict[str, Any], turn_index: int) -> str:
    texts: list[str] = []
    turns = artifact.get("turns")
    if isinstance(turns, list):
        target_prompt = None
        if 0 <= turn_index < len(turns) and isinstance(turns[turn_index], dict):
            target_prompt = turns[turn_index].get("user_prompt")
        conversation = artifact.get("conversation")
        if isinstance(target_prompt, str) and isinstance(conversation, list):
            seen_target = False
            for entry in conversation:
                if not isinstance(entry, dict):
                    continue
                if entry.get("role") == "user" and entry.get("content") == target_prompt:
                    seen_target = True
                    continue
                if seen_target and entry.get("role") == "user":
                    break
                if seen_target and entry.get("role") == "assistant":
                    content = entry.get("content")
                    if isinstance(content, str):
                        texts.append(content)
    return "\n".join(texts)


def _string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args(argv)
    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    result = judge_artifact(artifact)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
