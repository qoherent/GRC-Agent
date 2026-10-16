"""Hermetic unit tests for the Tier 2 multi-turn suite (no LLM calls).

Covers: suite invariants (multi-turn shape, uniqueness, targets, determinism),
the Unsloth export audit against multi-turn wire messages, and a scripted
two-turn session through the real harness driver (FunctionModel).
"""

from __future__ import annotations

import os
from pathlib import Path

from experiments.atomic_dataset.export_unsloth import audit_row
from experiments.atomic_dataset.harness_driver import run_harness_session
from experiments.atomic_dataset.scenarios import (
    TIER2_FAMILY_ORDER,
    TIER2_TARGETS,
    build_tier2_scenarios,
)
from experiments.atomic_dataset.verify_traces import verify_trace_record
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


def _suite() -> tuple[list, dict]:
    """Build the Tier 2 suite against the repo's cached scaffold directory."""
    repo = Path(os.environ.get("GRC_REPO_ROOT", os.getcwd()))
    return build_tier2_scenarios(scaffold_dir=repo / "experiments/atomic_dataset/fixtures/tier1")


# ---------------------------------------------------------------- suite shape

def test_tier2_suite_shape_and_determinism() -> None:
    specs, manifest = _suite()
    assert specs, "tier2 suite must not be empty"
    ids = [s.id for s in specs]
    assert len(ids) == len(set(ids)), "duplicate scenario ids"

    for s in specs:
        assert s.family in TIER2_FAMILY_ORDER
        assert len(s.turns) >= 2, f"{s.id}: must be multi-turn"
        assert s.prompt == s.turns[0]
        assert s.id.startswith(f"t2_{s.family.split('_')[1]}_"), f"{s.id} id/family mismatch"
        assert all(t.strip() for t in s.turns), f"{s.id}: empty turn"
        if s.family in ("mt_refine", "mt_rewire"):
            assert s.fixture, f"{s.id}: needs a scaffold fixture"
        if s.family in ("mt_build", "mt_correct", "mt_query"):
            assert s.fixture is not None and "empty" in s.fixture, f"{s.id}: needs the empty canvas"

    for family in TIER2_FAMILY_ORDER:
        n = sum(1 for s in specs if s.family == family)
        assert n <= TIER2_TARGETS[family], f"{family}: {n} > target {TIER2_TARGETS[family]}"
        assert manifest["actuals"][family] == n
    assert manifest["total"] == len(specs)

    specs2, manifest2 = _suite()
    assert [s.to_dict() for s in specs] == [s.to_dict() for s in specs2], "suite not deterministic"
    assert manifest["actuals"] == manifest2["actuals"]


def test_tier2_export_audit_handles_multi_turn() -> None:
    clean = {
        "flowgraph_valid": True,
        "tools": [{"function": {"name": "inspect_graph"}}, {"function": {"name": "final_result"}}],
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "turn 1"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "inspect_graph", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "{}"},
            {"role": "assistant", "content": "done 1"},
            {"role": "user", "content": "turn 2"},
            {"role": "assistant", "content": "done 2"},
        ],
    }
    assert audit_row(clean) == [], f"multi-turn clean row mis-flagged: {audit_row(clean)}"

    bad = {
        "flowgraph_valid": True,
        "tools": [{"function": {"name": "final_result"}}],
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "turn 1"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "mangled_tool", "arguments": "{}"}}],
            },
            {"role": "assistant", "content": "done 1"},
        ],
    }
    errs = audit_row(bad)
    assert any("invalid_tool_name" in e for e in errs), f"mangled tool name not caught: {errs}"


# ---------------------------------------------------------------- session run

def test_harness_session_runs_two_turns_on_one_flowgraph() -> None:
    """Two user turns share one flowgraph + conversation; turn 2 sees turn 1's work."""
    step = 0

    def scripted_model(_messages: list, _info: AgentInfo) -> ModelResponse:
        nonlocal step
        step += 1
        if step == 1:  # turn 1: ground, then finish
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="inspect_graph",
                        args={},
                        tool_call_id="call_t1_inspect",
                    )
                ]
            )
        if step == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="final_result",
                        args={"actions_taken": ["inspected"], "explanation": "Turn 1 complete."},
                        tool_call_id="call_t1_final",
                    )
                ]
            )
        if step == 3:  # turn 2: mutate the shared flowgraph
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="change_graph",
                        args={
                            "reason": "Turn 2: retune freq_350",
                            "update_params": [{"instance_name": "freq_350", "params": {"value": "380"}}],
                        },
                        tool_call_id="call_t2_change",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args={"actions_taken": ["updated freq_350"], "explanation": "Turn 2 complete."},
                    tool_call_id="call_t2_final",
                )
            ]
        )

    p1 = "Inspect the flowgraph and tell me about the frequency variable."
    p2 = "Now change freq_350 to 380."
    record = run_harness_session(
        scenario_id="test_hermetic_session",
        prompts=[p1, p2],
        override_model=FunctionModel(scripted_model),
    )

    assert record["turns"] == [p1, p2]
    assert record["turns_completed"] == 2
    assert record["prompt"] == p1

    user_msgs = [m for m in record["messages"] if m.get("role") == "user"]
    assert len(user_msgs) == 2, f"expected 2 user turns on the wire, got {len(user_msgs)}"
    assert user_msgs[0]["content"] == p1
    assert user_msgs[1]["content"] == p2

    # Turn 2's mutation lands in the shared flowgraph diff
    assert record["flowgraph_diff"]["params_changed"] == {
        "freq_350": {"value": {"before": "350", "after": "380"}}
    }
    assert record["flowgraph_valid"] is True

    verification = verify_trace_record(record)
    assert verification["ok"], f"Verifier failed: {verification['violations']}"
