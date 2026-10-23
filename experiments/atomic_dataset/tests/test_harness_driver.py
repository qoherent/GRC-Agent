"""Hermetic unit tests for the harness-driven trajectory collector and verifier."""

from __future__ import annotations

from experiments.atomic_dataset.harness_driver import run_harness_turn
from experiments.atomic_dataset.taxonomy import extract_catalog_taxonomy
from experiments.atomic_dataset.verify_traces import verify_trace_record
from pydantic_ai.messages import ModelResponse, ThinkingPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


def test_taxonomy_extracts_full_catalog() -> None:
    # Block count varies with the installed GNU Radio packages (568 on the office
    # machine's GRC 3.10.9.2); assert a lower bound that is stable across installs.
    taxonomy = extract_catalog_taxonomy()
    assert len(taxonomy) >= 500
    ids = {b["block_id"] for b in taxonomy}
    assert "analog_sig_source_x" in ids
    assert "blocks_throttle2" in ids
    assert "low_pass_filter" in ids


def test_harness_driver_captures_intermediate_calls_and_guardrails() -> None:
    """Verify that intermediate inspect_graph, query_knowledge, ModelRetry, ToolFailed,
    and final_result are all faithfully captured and pass structural verification."""
    step = 0

    def scripted_model(_messages: list, _info: AgentInfo) -> ModelResponse:
        nonlocal step
        step += 1
        if step == 1:
            return ModelResponse(
                parts=[
                    ThinkingPart(
                        content="First, inspect the flowgraph to see existing blocks.",
                        id="reasoning_content",
                        provider_name="openai",
                    ),
                    ToolCallPart(
                        tool_name="inspect_graph",
                        args={"targets": ["freq_350"]},
                        tool_call_id="call_inspect_1",
                    ),
                ]
            )
        if step == 2:
            # Trigger a ModelRetry guardrail error by referencing a non-existent block
            return ModelResponse(
                parts=[
                    ThinkingPart(
                        content="Let me try updating non_existent_block first.",
                        id="reasoning_content",
                        provider_name="openai",
                    ),
                    ToolCallPart(
                        tool_name="change_graph",
                        args={
                            "reason": "Try updating non-existent block",
                            "update_params": [
                                {"instance_name": "non_existent_block", "params": {"value": "380"}}
                            ],
                        },
                        tool_call_id="call_change_bad",
                    ),
                ]
            )
        if step == 3:
            # Trigger a ToolFailed terminal error by calling grc_interaction log mode in headless mode
            return ModelResponse(
                parts=[
                    ThinkingPart(
                        content="The block name was wrong; let me also check the run log and fix freq_350.",
                        id="reasoning_content",
                        provider_name="openai",
                    ),
                    ToolCallPart(
                        tool_name="grc_interaction",
                        args={"mode": "log"},
                        tool_call_id="call_run_log_fail",
                    ),
                    ToolCallPart(
                        tool_name="change_graph",
                        args={
                            "reason": "Set freq_350 value to 380.",
                            "update_params": [
                                {"instance_name": "freq_350", "params": {"value": "380"}}
                            ],
                        },
                        tool_call_id="call_change_good",
                    ),
                ]
            )
        return ModelResponse(
            parts=[
                ThinkingPart(
                    content="freq_350 is updated to 380 and run_log reported not wired in headless mode.",
                    id="reasoning_content",
                    provider_name="openai",
                ),
                ToolCallPart(
                    tool_name="final_result",
                    args={
                        "actions_taken": ["Updated freq_350 value to 380"],
                        "explanation": "Updated freq_350 to 380.",
                    },
                    tool_call_id="call_final",
                ),
            ]
        )

    record = run_harness_turn(
        scenario_id="test_hermetic_guardrails",
        prompt="Change freq_350 to 380 and check the run log.",
        override_model=FunctionModel(scripted_model),
    )

    verification = verify_trace_record(record)
    assert verification["ok"], f"Verifier failed: {verification['violations']}"
    assert verification["assistant_turns"] == 4
    assert verification["reasoning_coverage"] == 1.0
    assert record["flowgraph_valid"] is True
    assert record["flowgraph_diff"]["params_changed"] == {
        "freq_350": {"value": {"before": "350", "after": "380"}}
    }

    # Verify both ModelRetry (tool_model_retry) and ToolFailed (tool_failed_terminal) were captured
    guardrail_kinds = [g["kind"] for g in record["guardrail_events"]]
    assert "tool_model_retry" in guardrail_kinds
    assert "tool_failed_terminal" in guardrail_kinds

    # Verify wire messages include the retry error and ToolFailed error as role='tool'
    tool_msgs = [m for m in record["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 4  # inspect_graph, change_graph(retry), grc_interaction(failed), change_graph(ok)
    assert any("non_existent_block" in m["content"] for m in tool_msgs)
    assert any("Run-log capture is not wired up" in m["content"] for m in tool_msgs)


def test_harness_driver_captures_schema_and_output_validator_guardrails() -> None:
    """Verify schema_validation_retry and output_validator_retry (validate_flowgraph_state)."""
    step = 0

    def scripted_model(_messages: list, _info: AgentInfo) -> ModelResponse:
        nonlocal step
        step += 1
        if step == 1:
            # 1. Trigger a schema_validation_retry with malformed connection string 'bad_conn_no_colons'
            return ModelResponse(
                parts=[
                    ThinkingPart(
                        content="Trying malformed connection syntax.",
                        id="reasoning_content",
                        provider_name="openai",
                    ),
                    ToolCallPart(
                        tool_name="change_graph",
                        args={
                            "reason": "Malformed connection syntax",
                            "add_connections": ["bad_conn_no_colons"],
                        },
                        tool_call_id="call_schema_err",
                    ),
                ]
            )
        if step == 2:
            # 2. Add an unconnected source block with force=True so change_graph succeeds,
            # leaving the flowgraph invalid at turn end to trigger validate_flowgraph_state
            return ModelResponse(
                parts=[
                    ThinkingPart(
                        content="Add unconnected tone with force=True.",
                        id="reasoning_content",
                        provider_name="openai",
                    ),
                    ToolCallPart(
                        tool_name="change_graph",
                        args={
                            "reason": "Add unconnected source with force=True",
                            "add_blocks": [
                                {
                                    "block_id": "analog_sig_source_x",
                                    "instance_name": "unconnected_src",
                                    "params": {"type": "float", "freq": "1000", "amp": "0.1"},
                                }
                            ],
                            "force": True,
                        },
                        tool_call_id="call_add_forced",
                    ),
                ]
            )
        if step == 3:
            # 3. Attempt to finish turn while flowgraph is still invalid -> triggers output_validator_retry
            return ModelResponse(
                parts=[
                    ThinkingPart(
                        content="Attempting to finish before wiring unconnected_src.",
                        id="reasoning_content",
                        provider_name="openai",
                    ),
                    ToolCallPart(
                        tool_name="final_result",
                        args={
                            "actions_taken": ["Added unconnected_src"],
                            "explanation": "Premature completion.",
                        },
                        tool_call_id="call_final_premature",
                    ),
                ]
            )
        if step == 4:
            # 4. Recover from output_validator_retry by removing unconnected_src
            return ModelResponse(
                parts=[
                    ThinkingPart(
                        content="Output validator caught the unconnected block; removing it now.",
                        id="reasoning_content",
                        provider_name="openai",
                    ),
                    ToolCallPart(
                        tool_name="change_graph",
                        args={
                            "reason": "Remove unconnected_src to restore valid flowgraph",
                            "remove_blocks": ["unconnected_src"],
                        },
                        tool_call_id="call_fix_valid",
                    ),
                ]
            )
        return ModelResponse(
            parts=[
                ThinkingPart(
                    content="Flowgraph is valid again.",
                    id="reasoning_content",
                    provider_name="openai",
                ),
                ToolCallPart(
                    tool_name="final_result",
                    args={
                        "actions_taken": ["Restored valid graph"],
                        "explanation": "Recovered from validation error.",
                    },
                    tool_call_id="call_final_ok",
                ),
            ]
        )

    record = run_harness_turn(
        scenario_id="test_schema_and_output_guardrails",
        prompt="Test schema and output validator guardrails.",
        override_model=FunctionModel(scripted_model),
    )

    verification = verify_trace_record(record)
    assert verification["ok"], f"Verifier failed: {verification['violations']}"
    assert record["flowgraph_valid"] is True

    guardrail_kinds = [g["kind"] for g in record["guardrail_events"]]
    assert "schema_validation_retry" in guardrail_kinds
    assert "output_validator_retry" in guardrail_kinds

