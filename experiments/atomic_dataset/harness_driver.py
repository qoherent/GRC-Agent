"""Real GRC Agent harness driver for one-shot trajectory collection.

Drives single-user-prompt tasks through the production executor agent
(`build_agents_from_cfg(cfg).executor`) without ad-hoc prompt overrides or
hand-rolled OpenAI message converters.

Key guarantees:
- Uses `RecordingOpenAIChatModel` (subclassing `OpenAIChatModel`) so the exact
  `ModelRequestParameters` and `ModelSettings` assembled by all 13 harness
  capabilities and output validators are captured on the wire.
- Produces wire-accurate OpenAI `messages` and `tools` via Pydantic AI's own
  `OpenAIChatModel._map_messages` and `OpenAIChatModel._get_tool_choice`.
- Preserves lossless canonical Pydantic AI `ModelMessage` history via
  `serialize_messages` (`ModelMessagesTypeAdapter.dump_json`).
- Captures all intermediate tool calls (`inspect_graph`, `query_knowledge`,
  `change_graph`, `generate_python`, etc.), reasoning traces (`ThinkingPart` /
  `reasoning_content`), and guardrail errors (`RetryPromptPart` from schema
  validation, tool `ModelRetry`, or `validate_flowgraph_state`, plus
  `ToolReturnPart(outcome='failed')` from `ToolFailed`).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2
from pydantic_ai import ModelRequestContext, ModelSettings, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelProfile, ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolApproved
from pydantic_ai.usage import RunUsage

from grc_agent.adapter import get_platform, inspect_graph, load_flow_graph
from grc_agent.agent_factory import build_agents_from_cfg
from grc_agent.db import serialize_messages
from grc_agent.fs_tools import set_active_graph_providers


@dataclass
class RequestParametersRecorder(AbstractCapability[Any]):
    """Capability that records the exact ModelRequestParameters and ModelSettings per request."""

    last_request_parameters: ModelRequestParameters | None = None
    last_model_settings: ModelSettings | None = None

    async def before_model_request(
        self,
        ctx: RunContext[Any],  # noqa: ARG002
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        self.last_request_parameters = request_context.model_request_parameters
        self.last_model_settings = request_context.model_settings
        return request_context


class RecordingOpenAIChatModel(OpenAIChatModel):
    """OpenAIChatModel subclass that records the prepared request parameters and settings."""

    last_request_parameters: ModelRequestParameters | None = None
    last_model_settings: ModelSettings | None = None

    def prepare_request(
        self,
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> tuple[ModelSettings | None, ModelRequestParameters]:
        settings, params = super().prepare_request(model_settings, model_request_parameters)
        self.last_model_settings = settings
        self.last_request_parameters = params
        return settings, params


def sum_usage(messages: Sequence[ModelMessage]) -> dict[str, int]:
    """Aggregate model-reported token and request usage across all responses in the trajectory."""
    totals: dict[str, int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "requests": 0,
    }
    for msg in messages:
        if isinstance(msg, ModelResponse) and msg.usage is not None:
            u = msg.usage
            for field in ("input_tokens", "output_tokens", "requests"):
                val = getattr(u, field, None)
                if isinstance(val, int):
                    totals[field] += val
            details = getattr(u, "details", None)
            reasoning = (
                details.get("reasoning_tokens")
                if isinstance(details, dict)
                else getattr(details, "reasoning_tokens", None)
            )
            if isinstance(reasoning, int):
                totals["reasoning_tokens"] += reasoning
    return totals


def extract_guardrail_events(messages: Sequence[ModelMessage]) -> list[dict[str, Any]]:
    """Extract all guardrail interventions (RetryPromptPart and failed/denied ToolReturnPart)."""
    events: list[dict[str, Any]] = []
    for msg_idx, msg in enumerate(messages):
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if isinstance(part, RetryPromptPart):
                if isinstance(part.content, list):
                    kind = "schema_validation_retry"
                elif part.tool_name in (None, "final_result"):
                    kind = "output_validator_retry"
                else:
                    kind = "tool_model_retry"
                events.append({
                    "msg_index": msg_idx,
                    "kind": kind,
                    "tool_name": part.tool_name,
                    "tool_call_id": part.tool_call_id,
                    "raw_error": part.content,
                    "wire_response": part.model_response(),
                })
            elif isinstance(part, ToolReturnPart) and part.outcome in ("failed", "denied"):
                kind = "tool_failed_terminal" if part.outcome == "failed" else "tool_denied"
                events.append({
                    "msg_index": msg_idx,
                    "kind": kind,
                    "tool_name": part.tool_name,
                    "tool_call_id": part.tool_call_id,
                    "outcome": part.outcome,
                    "raw_error": part.content,
                    "wire_response": part.model_response_str(),
                })
    return events


def extract_tool_trajectory(messages: Sequence[ModelMessage]) -> list[dict[str, Any]]:
    """Extract the ordered sequence of assistant tool calls, reasoning, and outcomes."""
    returns_by_id: dict[str, ToolReturnPart | RetryPromptPart] = {}
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_call_id:
                    returns_by_id.setdefault(part.tool_call_id, part)

    steps: list[dict[str, Any]] = []
    step_idx = 0
    for msg_idx, msg in enumerate(messages):
        if not isinstance(msg, ModelResponse):
            continue
        reasoning = "\n\n".join(p.content for p in msg.parts if isinstance(p, ThinkingPart))
        text = "\n\n".join(p.content for p in msg.parts if isinstance(p, TextPart))
        tool_calls = [p for p in msg.parts if isinstance(p, ToolCallPart)]

        if not tool_calls:
            step_idx += 1
            steps.append({
                "step_index": step_idx,
                "msg_index": msg_idx,
                "step_type": "text_response",
                "tool_name": None,
                "tool_call_id": None,
                "args": None,
                "outcome": "completed",
                "reasoning_content": reasoning,
                "assistant_text": text,
                "return_content": None,
            })
            continue

        for call_idx, call in enumerate(tool_calls):
            step_idx += 1
            ret = returns_by_id.get(call.tool_call_id or "")
            if isinstance(ret, RetryPromptPart):
                outcome = "retry"
                ret_content: Any = ret.model_response()
            elif isinstance(ret, ToolReturnPart):
                outcome = "output_tool" if call.tool_name == "final_result" else str(ret.outcome)
                ret_content = ret.content
            else:
                outcome = "unanswered"
                ret_content = None

            try:
                args_parsed = (
                    call.args
                    if isinstance(call.args, dict)
                    else json.loads(call.args_as_json_str())
                )
            except Exception:
                args_parsed = call.args

            steps.append({
                "step_index": step_idx,
                "msg_index": msg_idx,
                "step_type": "output_tool" if call.tool_name == "final_result" else "tool_call",
                "tool_name": call.tool_name,
                "tool_call_id": call.tool_call_id,
                "args": args_parsed,
                "outcome": outcome,
                "reasoning_content": reasoning if call_idx == 0 else "",
                "assistant_text": text if call_idx == 0 else "",
                "return_content": ret_content,
            })
    return steps


def compute_flowgraph_diff(
    initial_graph: dict[str, Any],
    final_graph: dict[str, Any],
) -> dict[str, Any]:
    """Compute structural diff between initial and final inspect_graph snapshots."""
    init_blocks = {b["instance_name"]: b for b in initial_graph.get("blocks", [])}
    final_blocks = {b["instance_name"]: b for b in final_graph.get("blocks", [])}

    blocks_added = sorted(set(final_blocks) - set(init_blocks))
    blocks_removed = sorted(set(init_blocks) - set(final_blocks))

    params_changed: dict[str, dict[str, dict[str, Any]]] = {}
    states_changed: dict[str, dict[str, str]] = {}
    for name in sorted(set(init_blocks) & set(final_blocks)):
        b_init = init_blocks[name]
        b_final = final_blocks[name]
        if b_init.get("state") != b_final.get("state"):
            states_changed[name] = {
                "before": str(b_init.get("state")),
                "after": str(b_final.get("state")),
            }
        p_init = b_init.get("params", {})
        p_final = b_final.get("params", {})
        changed_keys = {
            k: {"before": p_init.get(k), "after": p_final.get(k)}
            for k in sorted(set(p_init) | set(p_final))
            if p_init.get(k) != p_final.get(k)
        }
        if changed_keys:
            params_changed[name] = changed_keys

    init_conns = set(initial_graph.get("connections", []))
    final_conns = set(final_graph.get("connections", []))
    connections_added = sorted(final_conns - init_conns)
    connections_removed = sorted(init_conns - final_conns)

    return {
        "blocks_added": blocks_added,
        "blocks_removed": blocks_removed,
        "params_changed": params_changed,
        "states_changed": states_changed,
        "connections_added": connections_added,
        "connections_removed": connections_removed,
    }


def _strip_post_run_final_result_mock(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """Remove only the post-termination synthetic `final_result` ToolReturnPart for wire SFT export.

    When a run finishes via `GrcAgentResponse` (`final_result` output tool), Pydantic AI appends
    a mock `ModelRequest(parts=[ToolReturnPart(tool_name="final_result", content="Final result processed.")])`
    after the final LLM response so multi-turn continuations have a balanced tool return.
    On the wire for a single-turn SFT trajectory, the last message produced by the model is the
    preceding `ModelResponse`. Any mid-turn `final_result` that triggered a `RetryPromptPart`
    is NOT at the end of `messages` and is preserved intact.
    """
    msg_list = list(messages)
    if (
        len(msg_list) >= 2
        and isinstance(msg_list[-1], ModelRequest)
        and len(msg_list[-1].parts) == 1
        and isinstance(msg_list[-1].parts[0], ToolReturnPart)
        and msg_list[-1].parts[0].tool_name == "final_result"
    ):
        return msg_list[:-1]
    return msg_list


async def map_to_openai_wire_format(
    messages: Sequence[ModelMessage],
    recording_model: RecordingOpenAIChatModel,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Map Pydantic AI messages and tools to wire-accurate OpenAI format via OpenAIChatModel."""
    params = recording_model.last_request_parameters or ModelRequestParameters()
    settings = recording_model.last_model_settings or ModelSettings()

    tools_raw, _ = recording_model._get_tool_choice(settings, params)
    wire_msgs = _strip_post_run_final_result_mock(messages)
    mapped_raw = await recording_model._map_messages(wire_msgs, params, model_settings=settings)

    openai_messages: list[dict[str, Any]] = json.loads(json.dumps(mapped_raw, default=str))
    openai_tools: list[dict[str, Any]] = json.loads(json.dumps(tools_raw, default=str))
    system_text = "\n\n".join(
        str(m.get("content", "")) for m in openai_messages if m.get("role") == "system"
    )
    return openai_messages, openai_tools, system_text


def _resolve_fixture_path(fixture_path: str | Path) -> Path:
    """Resolve a fixture .grc path across CWD, bundled fixtures/, and repo root."""
    cand = Path(fixture_path)
    if cand.is_file():
        return cand.resolve()
    pkg_dir = Path(__file__).resolve().parent
    bundled = pkg_dir / "fixtures" / cand.name
    if bundled.is_file():
        return bundled.resolve()
    repo_rel = pkg_dir.parent.parent / cand
    if repo_rel.is_file():
        return repo_rel.resolve()
    raise FileNotFoundError(f"Could not locate .grc fixture: {fixture_path}")


async def run_harness_turn_async(
    *,
    scenario_id: str,
    prompt: str,
    endpoint: str = "http://192.168.0.234:9000/v1",
    model_name: str = "qwen3.8-27b",
    fixture_path: str | Path | None = "tests/data/dial_tone.grc",
    max_tokens: int = 8192,
    override_model: Any | None = None,
) -> dict[str, Any]:
    """Execute one user prompt through the full production GRC Agent executor harness.

    Runs in an isolated temporary directory with its own `.env` (`GRC_AGENT_ENV`) and
    SQLite `chat_sessions.db` so production state is never touched.
    """
    prev_env = os.environ.get("GRC_AGENT_ENV")
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"grc_atomic_{scenario_id}_"))
    try:
        env_file = tmp_dir / ".env"
        env_file.write_text(
            f"GRC_PROVIDER=openai_compatible\n"
            f"OPENAI_COMPATIBLE_BASE_URL={endpoint}\n"
            f"OPENAI_COMPATIBLE_MODEL={model_name}\n"
            f"GRC_EMBED_BACKEND=lexical\n",
            encoding="utf-8",
        )
        os.environ["GRC_AGENT_ENV"] = str(env_file)

        project_dir = tmp_dir / "project"
        project_dir.mkdir(parents=True, exist_ok=True)

        if fixture_path is not None:
            src_fixture = _resolve_fixture_path(fixture_path)
            dst_fixture = project_dir / src_fixture.name
            shutil.copy2(src_fixture, dst_fixture)
            fg = load_flow_graph(str(dst_fixture))
        else:
            fg = get_platform().make_flow_graph()

        set_active_graph_providers(
            grc_path_fn=lambda: str(getattr(fg, "grc_file_path", "") or ""),
            flow_graph_fn=lambda: fg,
            project_dir_fn=lambda: str(project_dir),
        )

        initial_snap = inspect_graph(fg).get("graph", {})

        cfg = {
            "provider": "openai_compatible",
            "model": model_name,
            "openai_compatible_base_url": endpoint,
        }
        bundle = build_agents_from_cfg(cfg)
        executor = bundle.executor
        recorder = RequestParametersRecorder()
        executor._root_capability.capabilities.append(recorder)

        http_client = httpx2.AsyncClient(
            timeout=httpx2.Timeout(connect=15.0, read=180.0, write=60.0, pool=30.0)
        )
        recording_model = RecordingOpenAIChatModel(
            model_name,
            provider=OpenAIProvider(
                base_url=endpoint,
                api_key="not-needed",
                http_client=http_client,
            ),
            profile=ModelProfile(
                openai_supports_strict_tool_definition=False,
                openai_chat_supports_multiple_system_messages=False,
                openai_chat_thinking_field="reasoning_content",
                openai_chat_send_back_thinking_parts="field",
            ),
        )

        active_model = override_model if override_model is not None else recording_model
        model_settings = ModelSettings(max_tokens=max_tokens)

        approvals_granted: list[dict[str, Any]] = []
        turn_usage = RunUsage()
        conversation_id = f"session-{scenario_id}"

        start_t = time.monotonic()
        with executor.override(model=active_model, model_settings=model_settings):
            res = await executor.run(
                prompt,
                deps=fg,
                usage=turn_usage,
                conversation_id=conversation_id,
            )
            while isinstance(res.output, DeferredToolRequests):
                approval_map = {}
                for call in res.output.approvals:
                    approval_map[call.tool_call_id] = ToolApproved()
                    approvals_granted.append({
                        "tool_name": call.tool_name,
                        "tool_call_id": call.tool_call_id,
                        "decision": "approved",
                    })
                res = await executor.run(
                    message_history=res.all_messages(),
                    deferred_tool_results=DeferredToolResults(approvals=approval_map),
                    deps=fg,
                    usage=turn_usage,
                    conversation_id=conversation_id,
                )
        elapsed_seconds = time.monotonic() - start_t

        all_messages = res.all_messages()

        # Ensure recording_model has the active request parameters even when override_model was used in tests
        if recording_model.last_request_parameters is None and recorder.last_request_parameters is not None:
            recording_model.prepare_request(
                recorder.last_model_settings or model_settings,
                recorder.last_request_parameters,
            )

        openai_messages, openai_tools, system_text = await map_to_openai_wire_format(
            all_messages, recording_model
        )
        canonical_json_str = serialize_messages(all_messages)
        canonical_messages = json.loads(canonical_json_str)

        final_snap = inspect_graph(fg).get("graph", {})
        validation = final_snap.get("validation", {"status": "unknown", "errors": []})
        flowgraph_diff = compute_flowgraph_diff(initial_snap, final_snap)
        guardrail_events = extract_guardrail_events(all_messages)
        tool_trajectory = extract_tool_trajectory(all_messages)
        usage_summary = sum_usage(all_messages)

        final_output_data = (
            res.output.model_dump()
            if hasattr(res.output, "model_dump")
            else str(res.output)
        )

        return {
            "scenario_id": scenario_id,
            "prompt": prompt,
            "endpoint": endpoint,
            "model": model_name,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "flowgraph_valid": validation.get("status") == "valid",
            "flowgraph_validation": validation,
            "flowgraph_diff": flowgraph_diff,
            "usage": usage_summary,
            "approvals_granted": approvals_granted,
            "guardrail_events": guardrail_events,
            "tool_trajectory": tool_trajectory,
            "final_output": final_output_data,
            "system": system_text,
            "tools": openai_tools,
            "messages": openai_messages,
            "canonical_messages": canonical_messages,
        }
    finally:
        set_active_graph_providers(lambda: None, lambda: None, lambda: None)
        if prev_env is None:
            os.environ.pop("GRC_AGENT_ENV", None)
        else:
            os.environ["GRC_AGENT_ENV"] = prev_env
        shutil.rmtree(tmp_dir, ignore_errors=True)


def run_harness_turn(**kwargs: Any) -> dict[str, Any]:
    """Synchronous wrapper around `run_harness_turn_async`."""
    return asyncio.run(run_harness_turn_async(**kwargs))
