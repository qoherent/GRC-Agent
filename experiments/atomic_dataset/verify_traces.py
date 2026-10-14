"""Completeness and structural invariant verifier for harness-collected SFT traces.

Checks every collected record against:
1. Non-empty `system` instructions at `messages[0]`.
2. Single `role: "user"` prompt at `messages[1]`.
3. Every `tool_call` in intermediate assistant messages (`messages[:-1]`) is answered by a
   subsequent `role: "tool"` message with matching `tool_call_id`, and no orphaned `role: "tool"`
   messages exist.
4. Final message in `messages` has `role == "assistant"` (required for Unsloth / TRL completion loss).
5. `tools` schema list is populated and contains the domain tools plus `final_result`.
6. `canonical_messages` round-trips losslessly through `ModelMessagesTypeAdapter.validate_json`,
   and every `ToolCallPart` in `canonical_messages` is answered by a `ToolReturnPart` or `RetryPromptPart`.
7. `reasoning_content` coverage is computed and disclosed across all `role: "assistant"` turns.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)


def _check_wire_messages(messages: list[dict[str, Any]]) -> tuple[list[str], int, int]:
    """Check OpenAI wire messages invariants and return (violations, assistant_turns, with_reasoning)."""
    violations: list[str] = []
    if messages[0].get("role") != "system" or not str(messages[0].get("content") or "").strip():
        violations.append("messages[0] must be a non-empty system message")

    user_indices = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if not user_indices or user_indices[0] != 1:
        violations.append(f"expected user prompt at messages[1], found user roles at {user_indices}")

    if messages[-1].get("role") != "assistant":
        violations.append(
            f"final message must have role='assistant' for SFT completion loss, got {messages[-1].get('role')!r}"
        )

    open_call_ids: list[str] = []
    orphaned_tool_ids: list[str] = []
    assistant_turns = 0
    assistant_with_reasoning = 0

    for idx, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            assistant_turns += 1
            if str(msg.get("reasoning_content") or "").strip():
                assistant_with_reasoning += 1
            is_final_msg = idx == len(messages) - 1
            for tc in msg.get("tool_calls") or []:
                cid = str(tc.get("id") or "")
                fn_name = (tc.get("function") or {}).get("name")
                if is_final_msg and fn_name == "final_result":
                    continue
                open_call_ids.append(cid)
        elif role == "tool":
            tid = str(msg.get("tool_call_id") or "")
            if tid in open_call_ids:
                open_call_ids.remove(tid)
            else:
                orphaned_tool_ids.append(tid)

    if open_call_ids:
        violations.append(f"unanswered tool_call_ids in wire messages: {open_call_ids}")
    if orphaned_tool_ids:
        violations.append(f"orphaned tool_call_ids in wire messages: {orphaned_tool_ids}")

    return violations, assistant_turns, assistant_with_reasoning


def _check_canonical_messages(canonical_raw: Any) -> list[str]:
    """Verify canonical Pydantic AI messages deserialize and have balanced tool calls."""
    if canonical_raw is None:
        return []
    violations: list[str] = []
    try:
        canonical_msgs = ModelMessagesTypeAdapter.validate_json(json.dumps(canonical_raw))
        answered_canonical: set[str] = set()
        for m in canonical_msgs:
            if isinstance(m, ModelRequest):
                for p in m.parts:
                    if isinstance(p, (ToolReturnPart, RetryPromptPart)) and p.tool_call_id:
                        answered_canonical.add(p.tool_call_id)
        for m in canonical_msgs:
            if isinstance(m, ModelResponse):
                for p in m.parts:
                    if isinstance(p, ToolCallPart) and p.tool_call_id not in answered_canonical:
                        violations.append(
                            f"canonical ToolCallPart {p.tool_call_id} ({p.tool_name}) has no matching return/retry"
                        )
    except Exception as exc:
        violations.append(f"canonical_messages failed ModelMessagesTypeAdapter validation: {exc}")
    return violations


def verify_trace_record(record: dict[str, Any]) -> dict[str, Any]:
    """Verify a single trace record and return a structured verification report."""
    messages = record.get("messages") or []
    tools = record.get("tools") or []
    if not messages:
        return {"ok": False, "violations": ["messages list is empty"]}

    violations, assistant_turns, assistant_with_reasoning = _check_wire_messages(messages)

    tool_names = {
        str((t.get("function") or {}).get("name") or t.get("name") or "")
        for t in tools
        if isinstance(t, dict)
    }
    required_tools = {"inspect_graph", "query_knowledge", "change_graph", "generate_python", "final_result"}
    missing_tools = sorted(required_tools - tool_names)
    if missing_tools:
        violations.append(f"tools schema missing required tools: {missing_tools}")

    violations.extend(_check_canonical_messages(record.get("canonical_messages")))

    reasoning_coverage = (
        round(assistant_with_reasoning / assistant_turns, 4) if assistant_turns > 0 else 0.0
    )

    return {
        "ok": len(violations) == 0,
        "violations": violations,
        "assistant_turns": assistant_turns,
        "assistant_with_reasoning": assistant_with_reasoning,
        "reasoning_coverage": reasoning_coverage,
        "tool_schemas_count": len(tools),
        "guardrail_events_count": len(record.get("guardrail_events") or []),
        "flowgraph_valid": bool(record.get("flowgraph_valid", True)),
    }


def verify_jsonl_file(path: Path) -> tuple[bool, list[dict[str, Any]]]:
    """Verify all JSONL rows in an exported file."""
    reports: list[dict[str, Any]] = []
    all_ok = True
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        rep = verify_trace_record(row)
        rep["line"] = line_no
        rep["scenario_id"] = row.get("scenario_id", f"line_{line_no}")
        if not rep["ok"]:
            all_ok = False
        reports.append(rep)
    return all_ok, reports


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify exported SFT trajectory JSONL.")
    parser.add_argument("jsonl_path", type=Path, help="Path to SFT JSONL file")
    args = parser.parse_args()

    ok, reports = verify_jsonl_file(args.jsonl_path)
    for rep in reports:
        status = "PASS" if rep["ok"] else "FAIL"
        print(
            f"[{status}] {rep['scenario_id']}: assistant_turns={rep['assistant_turns']} "
            f"reasoning_coverage={rep['reasoning_coverage']:.2f} "
            f"tools={rep['tool_schemas_count']} guardrails={rep['guardrail_events_count']} "
            f"fg_valid={rep['flowgraph_valid']}"
        )
        for v in rep["violations"]:
            print(f"    ! {v}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
