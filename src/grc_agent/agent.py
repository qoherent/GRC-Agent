import asyncio
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import (
    Agent,
    ModelMessage,
    ModelRequest,
    ModelRequestNode,
    ModelRetry,
    ModelSettings,
    RunContext,
    Tool,
)
from pydantic_ai.capabilities import (
    AbstractCapability,
    AgentNode,
    NodeResult,
    ProcessHistory,
    WebFetch,
    WebSearch,
    WrapNodeRunHandler,
)
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.result import FinalResult
from pydantic_graph import End, GraphBuilder, StepContext

# Local imports
from grc_agent.adapter import (
    change_graph,
    inspect_graph,
    lite_web_search,
    load_flow_graph,
    query_catalog,
    query_docs,
)

MODEL = "qwen3.6:35b-a3b-q4_K_M"
OLLAMA_V1 = "http://localhost:11434/v1"

SCENARIOS = [
    {
        "name": "01_add_throttle",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Take a look at the flowgraph, then add a throttle block in the"
            " path between the 350 Hz tone and the adder that mixes the tones"
            " together. Call it `mid_throttle`, set its type to float, and"
            " have it use the samp_rate variable for its rate. Make sure the"
            " wiring is rerouted so it actually sits inline. Then inspect the"
            " result to confirm."
        ),
        "expect": {"blocks_present": ["mid_throttle"], "valid": True},
    },
    {
        "name": "02_update_sample_rate",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Inspect the current flowgraph. Then update the `samp_rate`"
            " variable to `48000`. Confirm the change by inspecting again."
        ),
        "expect": {
            "params": {"samp_rate": {"value": "48000"}},
            "valid": True,
        },
    },
    {
        "name": "03_disable_and_enable",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Inspect the flowgraph, then disable the noise source that's"
            " mixed into the audio output. Inspect again to confirm it's"
            " off. Then turn it back on and confirm."
        ),
        "expect": {"valid": True},
    },
    {
        "name": "04_add_and_remove_variable",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Inspect the flowgraph. Add a new variable called `gain_value`"
            " set to 2.0, then have the 350 Hz tone's amplitude use that"
            " variable instead of its current value. Inspect to confirm"
            " both changes landed."
        ),
        "expect": {
            "blocks_present": ["gain_value"],
            "params": {"analog_sig_source_x_0": {"amp": "gain_value"}},
            "valid": True,
        },
    },
    {
        "name": "05_full_rewire",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Inspect the flowgraph. I don't want the noise source anymore —"
            " remove it. In its place, add a constant source block, call it"
            " `dc_offset`, with its constant value set to 0.0, and wire its"
            " output into the same input on the adder that the noise source"
            " used to feed. Inspect the result to confirm the change."
        ),
        "expect": {
            "blocks_absent": ["analog_noise_source_x_0"],
            "blocks_present": ["dc_offset"],
            "valid": True,
        },
    },
    {
        "name": "06_query_knowledge_multiply",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Inspect the flowgraph. I want to multiply the two sine wave"
            " tones together instead of adding them. Look up the right GNU"
            " Radio block for a signal multiplier using query_knowledge"
            " (catalog domain) — don't guess the block id. Add it, call it"
            " `multiplier`, set its type to float, wire both tone sources"
            " into it, and remove the adder that's currently combining"
            " them. Inspect the result to confirm."
        ),
        "expect": {
            "blocks_present": ["multiplier"],
            "blocks_absent": ["blocks_add_xx"],
            "valid": True,
        },
    },
    {
        "name": "09_docs_stream_tags_concept",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "I'm learning GNU Radio. Use `query_knowledge` with the **docs**"
            " domain to explain what a 'stream tag' is and how tags move"
            " through a flowgraph. Summarize what the documentation says."
            " Don't change the graph."
        ),
        "expect": {"mode": "read"},
    },
    {
        "name": "10_bypass_source_block",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Inspect the flowgraph, then put the 350 Hz tone source into"
            " bypass mode. Inspect again to confirm it actually switched"
            " to bypass."
        ),
        "expect": {"states": {"analog_sig_source_x_0": "bypass"}},
    },
    {
        "name": "11_scoped_inspect_and_update",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "This flowgraph has several blocks in it. Using inspect_graph's"
            " targets option, look at just the sample rate variable and the"
            " 350 Hz tone source — don't pull the whole overview. Then"
            " change the sample rate to 96000. Check just those same two"
            " blocks again to confirm."
        ),
        "expect": {
            "params": {"samp_rate": {"value": "96000"}},
            "valid": True,
        },
    },
    {
        "name": "14_build_chain_from_scratch",
        "fixture": "tests/data/empty.grc",
        "prompt": (
            "Inspect the flowgraph — right now it's empty except for the"
            " samp_rate variable. Build a minimal signal chain: a signal"
            " source called `sig` (type float, freq 1000, amp 0.5, using"
            " samp_rate), a throttle called `throttle` (type float,"
            " samples_per_second using samp_rate), and a null sink called"
            " `sink` (type float). Wire the source into the throttle, and"
            " the throttle into the sink. Inspect to confirm the chain is"
            " valid."
        ),
        "expect": {
            "blocks_present": [["sig", "sig_source"], "throttle", "sink"],
            "valid": True,
        },
    },
    {
        "name": "21_type_conversion_and_conjugate",
        "fixture": "tests/data/resampler_demo.grc",
        "prompt": (
            "Inspect the flowgraph. I want to make some changes:\n"
            "1. Search the catalog for a block that converts a float stream"
            " into a complex stream, and also for a block that computes the"
            " complex conjugate of a complex signal.\n"
            "2. The FM modulator in this chain isn't needed anymore —"
            " replace it entirely with the float-to-complex converter you"
            " found. Call the converter `float_to_complex_converter`.\n"
            "3. Wire the throttle's output into the converter's real-part"
            " input.\n"
            "4. Search the catalog for a constant source block. Add one,"
            " call it `zero_imag`, type float, constant value 0.0, and wire"
            " it into the converter's imaginary-part input so the converter"
            " has a valid complex input.\n"
            "5. Connect the converter's output to both the resampler and"
            " the original spectrum display that the FM modulator used to"
            " feed.\n"
            "6. Add the complex conjugate block, call it `signal_conjugate`,"
            " and insert it right after the resampler, before the resampled"
            " spectrum display — so the resampler's output goes through the"
            " conjugate block before reaching that display.\n"
            "7. Remove the old FM modulator block entirely, make sure the"
            " flowgraph is valid, and inspect it to confirm."
        ),
        "expect": {
            "blocks_present": ["float_to_complex_converter", "zero_imag", "signal_conjugate"],
            "blocks_absent": ["analog_frequency_modulator_fc_0"],
            "valid": True,
        },
    },
    {
        "name": "22_fm_rx_filter_squelch",
        "fixture": "tests/data/fm_rx.grc",
        "prompt": (
            "Upgrade this FM Receiver flowgraph to add a band-limiting filter and a noise squelch:\n"
            "1. Search the catalog for a standard low pass filter block.\n"
            "2. Add it to the flowgraph, call it `channel_filter`. Set its type parameter to `fir_filter_ccf` "
            "(complex input/output, float taps). Set its sample rate to `in_rate`, cutoff frequency to `100e3`, "
            "and transition width to `20e3`.\n"
            "3. Search the catalog for a squelch block that operates on complex signals. Add it, call it "
            "`signal_squelch`, type `analog_simple_squelch_cc`. Set its threshold to `-50` and alpha to `0.01`.\n"
            "4. Insert `channel_filter` right after the file source (`blocks_file_source_0`). The file source's "
            "output must go into the filter's input. The time display (`qtgui_time_sink_x_0_0`) and frequency display "
            "(`qtgui_freq_sink_x_0_0`) must remain connected directly to the original file source's output.\n"
            "5. Insert `signal_squelch` right after the `channel_filter`. The channel filter's output goes into "
            "the squelch block's input. The squelch block's output then feeds the input of the quadrature demodulator "
            "(`analog_quadrature_demod_cf_0`).\n"
            "6. Make sure you remove the direct connection from the file source to the quadrature demodulator, "
            "ensuring the new filter and squelch blocks are inline. Check that the flowgraph is valid and confirm the result."
        ),
        "expect": {
            "blocks_present": ["channel_filter", "signal_squelch"],
            "params": {
                "channel_filter": {
                    "type": "fir_filter_ccf",
                    "samp_rate": "in_rate",
                    "cutoff_freq": "100e3",
                    "width": "20e3",
                },
                "signal_squelch": {
                    "threshold": "-50",
                    "alpha": "0.01",
                },
            },
            "valid": True,
        },
    },
]


# Pydantic models for structured outputs and tool schemas
class GrcAgentResponse(BaseModel):
    actions_taken: list[str] = Field(
        ...,
        description="A list of changes applied to the flowgraph (e.g. ['Added mid_throttle block', 'Connected sig to mid_throttle']).",
    )
    explanation: str = Field(
        ..., description="A summary explaining the final state of the flowgraph."
    )


class BlockAdd(BaseModel):
    block_id: str = Field(
        ..., description="Installed GNU Radio catalog block ID (e.g. 'analog_sig_source_x')."
    )
    instance_name: str = Field(
        ..., description="New unique graph instance name (e.g. 'my_source')."
    )
    params: dict[str, str] | None = Field(
        None, description="Initial parameter values keyed by parameter ID."
    )
    state: Literal["enabled", "disabled", "bypass"] | None = Field(
        None, description="Initial block state; defaults to 'enabled'."
    )


class ParamUpdate(BaseModel):
    instance_name: str = Field(..., description="Target block instance name (e.g. 'my_source').")
    params: dict[str, str] = Field(..., description="Param updates keyed by parameter ID.")


class StateUpdate(BaseModel):
    instance_name: str = Field(..., description="Target block instance name (e.g. 'my_source').")
    state: Literal["enabled", "disabled", "bypass"] = Field(..., description="New block state.")


@dataclass
class StopGracefully(AbstractCapability[Any]):
    """Ends the run with a clear message instead of letting a stuck local
    model's request count blow past the ceiling as a raw UsageLimitExceeded
    traceback — pydantic_ai's own documented capability pattern
    (wrap_node_run + End(FinalResult(...))), not a hand-rolled loop
    detector."""

    max_requests: int = 40
    count: int = 0

    async def for_run(self, ctx: RunContext[Any]) -> "StopGracefully":
        return StopGracefully(max_requests=self.max_requests)

    async def wrap_node_run(
        self, ctx: RunContext[Any], *, node: AgentNode, handler: WrapNodeRunHandler
    ) -> NodeResult:
        if isinstance(node, ModelRequestNode):
            self.count += 1
            if self.count > self.max_requests:
                return End(
                    FinalResult(
                        output=(
                            "I've made a lot of tool calls without finishing, so I'm stopping here "
                            "rather than looping further. Please check what's changed so far and "
                            "try a more specific follow-up."
                        )
                    )
                )
        return await handler(node)


# Provider-adaptive web capabilities. On providers with native web support
# (OpenRouter, via its plugins) the framework runs search/fetch server-side; on
# providers without it (Ollama has none) it falls back to `local` — here a
# lite.duckduckgo.com scrape (`lite_web_search`) and the bundled markdownify
# fetch (`WebFetch(local=True)`). Eager (defer_loading=False) so the tools are
# always callable — no load_capability round-trip. Defined once here and
# imported by web.py / tests so every Agent shares the same instances.
web_search_cap = WebSearch(local=lite_web_search)
web_fetch_cap = WebFetch(local=True)


def fresh_agent(fixture):
    tmp_dir = tempfile.mkdtemp()
    tmp = Path(tmp_dir) / Path(fixture).name
    shutil.copy2(fixture, tmp)
    fg = load_flow_graph(str(tmp))
    return fg, tmp, tmp_dir


# Module-level tool functions
async def inspect_graph_func(ctx: RunContext[Any], targets: list[str] | None = None) -> str:
    """Read-only inspection of the active graph. Returns topology, block instances, connections, parameter values, and validation status."""
    return json.dumps(inspect_graph(ctx.deps, targets=targets, view="overview"))


async def query_knowledge_func(
    ctx: RunContext[Any], query: str, domain: Literal["catalog", "docs"]
) -> str:
    """Answer GNU Radio knowledge questions from two domains: catalog (block IDs, port names, parameter keys) or docs (concepts)."""
    if domain == "catalog":
        res = await asyncio.to_thread(query_catalog, query)
        return json.dumps(res)
    else:
        res = await asyncio.to_thread(query_docs, query)
        return json.dumps(res)


def validate_change_graph_args(
    ctx: RunContext[Any],
    add_blocks: list[BlockAdd] | None = None,
    remove_blocks: list[str] | None = None,
    update_params: list[ParamUpdate] | None = None,
    update_states: list[StateUpdate] | None = None,
    **kwargs,
) -> None:
    try:
        current_blocks = {b.name for b in ctx.deps.blocks}
        added_names = {b.instance_name for b in add_blocks} if add_blocks else set()

        # Check block presence for updates
        for item in update_params or []:
            if item.instance_name not in current_blocks and item.instance_name not in added_names:
                raise ModelRetry(
                    f"Block '{item.instance_name}' does not exist in the flowgraph. "
                    "You must add the block first before trying to update its parameters."
                )

        for item in update_states or []:
            if item.instance_name not in current_blocks and item.instance_name not in added_names:
                raise ModelRetry(
                    f"Block '{item.instance_name}' does not exist in the flowgraph. "
                    "You must add the block first before trying to update its state."
                )

        # Check block presence for removals
        for name in remove_blocks or []:
            if name not in current_blocks:
                raise ModelRetry(
                    f"Cannot remove block '{name}' because it does not exist in the flowgraph."
                )
    except ModelRetry:
        raise
    except Exception as exc:  # pragma: no cover
        # Log unexpected validator errors so they surface as real failures
        # rather than silently letting the model proceed with a broken harness.
        import logging

        logging.getLogger(__name__).warning(
            "validate_change_graph_args raised unexpectedly: %s", exc, exc_info=True
        )


async def change_graph_func(
    ctx: RunContext[Any],
    add_blocks: list[BlockAdd] | None = None,
    remove_blocks: list[str] | None = None,
    update_params: list[ParamUpdate] | None = None,
    update_states: list[StateUpdate] | None = None,
    add_connections: list[str] | None = None,
    remove_connections: list[str] | None = None,
    force: bool = False,
) -> str:
    """Apply a batch of structural graph edits.

    Can add/remove blocks, update parameters/states, and add/remove connections in a single transaction.

    Connection strings (in add_connections and remove_connections) must strictly use the format 'src_block:src_port->dst_block:dst_port' (e.g. 'source_0:0->sink_0:0').
    """
    add_blocks_dict = [b.model_dump(exclude_none=True) for b in add_blocks] if add_blocks else None
    update_params_dict = (
        [p.model_dump(exclude_none=True) for p in update_params] if update_params else None
    )
    update_states_dict = [s.model_dump() for s in update_states] if update_states else None

    res = change_graph(
        ctx.deps,
        add_blocks=add_blocks_dict,
        remove_blocks=remove_blocks,
        update_params=update_params_dict,
        update_states=update_states_dict,
        add_connections=add_connections,
        remove_connections=remove_connections,
        force=force,
    )
    if not res.get("ok"):
        raise ModelRetry(
            f"Graph modification failed. Errors: {res.get('errors') or res.get('message') or '(no detail)'}. "
            "Please adjust your parameters/connections or set force=True if appropriate and retry."
        )
    if hasattr(ctx.deps, "bump_version"):
        ctx.deps.bump_version()
    # Tell the live GTK canvas (if any) to reload from disk — the in-memory
    # graph and the on-disk file are now ahead of what's visually rendered.
    # The outcome is surfaced so a desync isn't silent; on a raw flowgraph
    # deps (scenario harness) notify_edit is absent and this is skipped.
    if hasattr(ctx.deps, "notify_edit"):
        res["canvas_synced"] = (await ctx.deps.notify_edit()).get("ok", False)
    return json.dumps(res)


def grc_tools() -> list[Tool[Any]]:
    inspect_tool = Tool(
        inspect_graph_func,
        name="inspect_graph",
        description="Read-only inspection of the active graph. Returns topology, block instances, connections, parameter values, and validation status.",
    )

    query_tool = Tool(
        query_knowledge_func,
        name="query_knowledge",
        description="Answer GNU Radio knowledge questions from two domains: catalog (block IDs, port names, parameter keys) or docs (concepts).",
    )

    change_tool = Tool(
        change_graph_func,
        name="change_graph",
        description="Apply a batch of structural graph edits. Can add/remove blocks, update parameters/states, and add/remove connections in a single transaction.",
        args_validator=validate_change_graph_args,
    )
    change_tool.max_retries = 3

    return [inspect_tool, query_tool, change_tool]


def build_system_prompt(session_id: str | None = None) -> str:
    prefix = f"Session ID: {session_id}\n" if session_id else ""
    return prefix + (
        "Role: GNU Radio graph editing assistant.\n"
        "inspect_graph: read topology, blocks, connections, field values, and validation status. "
        "Pass a targets list of block instance names to scope it to those blocks instead of the whole graph.\n"
        "query_knowledge: search catalog blocks or GNU Radio documentation.\n"
        "change_graph: add/remove blocks, edit field values, add/remove connections.\n"
        "Parameter values are string expressions; a variable reference is simply the variable's name (e.g. use 'base_freq * 1.5', NOT 'vars.base_freq * 1.5').\n"
        "Set a type-controlling parameter (e.g. 'type', 'itype', 'otype') to the literal value 'auto' "
        "to resolve it from a connected neighbor's dtype instead of guessing a value.\n"
        "Stream-port connections use numeric port keys (e.g. '0', '1', '2'), not names like 'out', 'in(0)', or 'in0'. "
        "GRC error messages like 'in(0)' refer to port index '0'. Message ports are the exception: "
        "they use their exact declared string identifier (e.g. 'pdus', 'msg') instead of a numeric index.\n"
        "Connection strings must use the exact format 'src_block:src_port->dst_block:dst_port' (e.g. 'source_0:0->sink_0:0').\n"
        "Do not attempt to rename blocks by changing the 'id' parameter in update_params; "
        "changing a block's ID is not supported and will be ignored. To rename a block, you must remove it and add a new one.\n"
        'Variables are blocks; use block_id "variable" (not "parameter") to add one.\n'
        "Every GNU Radio fact must be grounded in query_knowledge, not memory.\n"
        "Ensure the final state of the flowgraph is valid. The environment automatically "
        "validates the graph state when you complete a turn and will raise a validation error "
        "if invalid.\n"
        "A change_graph call that returns ok=false applied nothing — the batch was rolled back. "
        "Read the errors, adjust the call, and retry; do not resubmit identical arguments.\n"
        "Describing a change_graph call in your reply text does not execute it; only an actual tool call applies changes to the graph.\n"
        "The force=True flag in change_graph commits edits but does not resolve errors; "
        "you must still fix any unconnected ports or blocks to make the graph valid.\n"
        "To change a block's enablement, use the update_states batch field: "
        "{instance_name, state}, where state is enabled, disabled, or bypass.\n"
        "'Port is not connected' means a required port has zero active connections — this includes a "
        "newly added block that was never wired up, not only a block being disabled. "
        "Disabling a block that is part of a connection also fails this same validation; "
        "use state=bypass to take a connected block out of service without breaking the graph, "
        "or force=true to commit the disabled state anyway.\n"
        "When removing blocks, also update_states (disabled/bypass) or remove any source blocks that become unconnected.\n"
        "Never use hallucinated block IDs; if query_knowledge does not return a block ID, it does not exist.\n"
        "When the user asks a question, answer concisely: lead with the direct answer, then add only the context needed to act on it.\n"
        "Do not use LaTeX or TeX math notation in chat replies; write math inline in plain text (e.g. `350 microHz`, `f^2`, `x_i`).\n"
    )


def prune_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    if len(messages) <= 12:
        return messages
    target = len(messages) - 10
    for i in range(target, 0, -1):
        msg = messages[i]
        if isinstance(msg, ModelRequest):
            if any(isinstance(p, UserPromptPart) for p in msg.parts):
                return [messages[0]] + messages[i:]
    return messages


async def validate_flowgraph_state(ctx: RunContext[Any], output: str) -> str:
    from pydantic_ai.messages import ToolCallPart

    has_mutated = False
    for msg in ctx.messages:
        if hasattr(msg, "parts"):
            for part in msg.parts:
                if isinstance(part, ToolCallPart) and part.tool_name == "change_graph":
                    has_mutated = True
                    break
    if has_mutated:
        fg = ctx.deps
        if not fg.is_valid():
            validation_errors = []
            for elem, msg in fg.iter_error_messages():
                parent = getattr(elem, "parent_block", None)
                if parent is not None and parent is not elem:
                    validation_errors.append(f"{parent.name}: {elem}: {msg}")
                else:
                    validation_errors.append(f"{elem}: {msg}")
            raise ModelRetry(
                f"The flowgraph has validation errors after mutation: {validation_errors}. "
                "You must run change_graph to correct these errors (or set force=True if they are unresolvable) before completing the response."
            )
    return output


def check_expect(fixture_path, expect, run_result=None):
    fg = load_flow_graph(str(fixture_path))
    snap = inspect_graph(fg)["graph"]
    valid = snap["validation"]["status"] == "valid"
    names = {b["instance_name"] for b in snap["blocks"]}
    params = {b["instance_name"]: b["params"] for b in snap["blocks"]}
    states = {b["instance_name"]: b["state"] for b in snap["blocks"]}

    fail_reasons = []
    mode = expect.get("mode", "edit")

    if mode == "read":
        has_read_tool = False
        if run_result:
            from pydantic_ai.messages import ToolCallPart

            for msg in run_result.all_messages():
                if hasattr(msg, "parts"):
                    if any(
                        isinstance(p, ToolCallPart)
                        and p.tool_name in ("query_knowledge", "inspect_graph")
                        for p in msg.parts
                    ):
                        has_read_tool = True
        if not has_read_tool:
            fail_reasons.append("no read tool used")
        if not run_result or not run_result.output:
            fail_reasons.append("empty answer")
    else:
        for blk in expect.get("blocks_present") or []:
            if isinstance(blk, (list, tuple)):
                if not any(alt in names for alt in blk):
                    fail_reasons.append(f"missing block (one of {blk})")
            else:
                if blk not in names:
                    fail_reasons.append(f"missing block {blk}")
        for blk in expect.get("blocks_absent") or []:
            if blk in names:
                fail_reasons.append(f"block {blk} still present")
        if "valid" in expect and valid != bool(expect["valid"]):
            fail_reasons.append(f"graph valid={valid} expected {expect['valid']}")
        for inst, st in (expect.get("states") or {}).items():
            if str(states.get(inst, "")) != str(st):
                fail_reasons.append(f"state {inst}={states.get(inst)!r} expected {st!r}")
        for inst, pv in (expect.get("params") or {}).items():
            actual = params.get(inst, {})
            for k, v in pv.items():
                actual_val = str(actual.get(k, "")).replace(" ", "")
                expected_val = str(v).replace(" ", "")
                if actual_val == expected_val:
                    continue
                try:
                    numeric_match = float(actual_val) == float(expected_val)
                except ValueError:
                    numeric_match = False
                if not numeric_match:
                    fail_reasons.append(f"param {inst}.{k}={actual.get(k)!r} expected {v!r}")

    return {"pass": not fail_reasons, "reasons": fail_reasons, "valid": valid}


def render_scenario_markdown(sc, grc_before, run_result, verdict) -> str:
    events = []
    from pydantic_ai import ModelRequest, ModelResponse
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart

    messages = run_result.all_messages() if run_result else []
    tool_calls = {}

    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    tool_calls[part.tool_call_id] = {
                        "name": part.tool_name,
                        "args": part.args,
                        "result": None,
                    }

    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    if part.tool_call_id in tool_calls:
                        tool_calls[part.tool_call_id]["result"] = part.content

    call_idx = 0
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    t_info = tool_calls.get(part.tool_call_id)
                    if t_info:
                        call_idx += 1
                        events.append(
                            {
                                "event": "model_message",
                                "role": "tool_model",
                                "tool_called": {"name": t_info["name"], "args": t_info["args"]},
                                "payload": {
                                    "content": [
                                        {
                                            "tool_call_result": str(t_info["result"])
                                            if t_info["result"] is not None
                                            else ""
                                        }
                                    ]
                                },
                            }
                        )

    final_res = {
        "ok": verdict["pass"],
        "assistant_text": run_result.output if run_result else "",
        "expect_reason": "; ".join(verdict["reasons"]) if verdict["reasons"] else "ok",
    }
    events.append({"event": "final", "result": final_res})

    title = sc["name"].replace("_", " ").title()
    rec = {
        "title": title,
        "name": sc["name"],
        "fixture_name": Path(sc["fixture"]).name,
        "system_prompt": build_system_prompt("pai-experiment"),
        "prompt": sc["prompt"],
        "grc_before": grc_before,
        "events": events,
    }

    parts = [
        f"# {rec['title']}",
        "",
        f"**Scenario:** `{rec['name']}` | **Fixture:** `{rec['fixture_name']}` | **Model:** `{MODEL}`",
        "",
        "## System Prompt",
        "",
        "```text",
        rec["system_prompt"],
        "```",
        "",
        "## User Prompt",
        "",
        "```text",
        rec["prompt"],
        "```",
        "",
        "## Flowgraph: BEFORE",
        "",
        "```yaml",
        rec["grc_before"],
        "```",
        "",
        "## Tool calls (raw inputs + outputs the model saw)",
        "",
    ]

    for idx, ev in enumerate(events):
        if ev.get("event") == "model_message" and ev.get("role") == "tool_model":
            tc = ev.get("tool_called") or {}
            tool_name = tc.get("name")
            parts.append(f"### call {idx + 1} — `{tool_name}`")
            parts.append("")
            parts.append("**args (model sent):**")
            parts.append("")
            parts.append("```json")
            parts.append(json.dumps(tc.get("args", {}), indent=2, default=str))
            parts.append("```")
            parts.append("")

            payload = ev.get("payload", {}) or {}
            content = payload.get("content") or []
            entry = content[0] if content else {}
            result_text = entry.get("tool_call_result", "")

            parts.append("**result (model saw this exact string):**")
            parts.append("")
            parts.append("```json")
            parts.append(result_text)
            parts.append("```")
            parts.append("")

    parts.append("## Final result (raw)")
    parts.append("")
    parts.append("```json")
    parts.append(json.dumps(final_res, indent=2, default=str))
    parts.append("```")
    parts.append("")

    return "\n".join(parts)


# Scenario State Machine workflow using pydantic-graph
@dataclass
class ScenarioState:
    sc: dict
    grc_before: str
    fg: Any = None
    fixture_path: Path | None = None
    tmp_dir: str | None = None
    res: Any = None
    verdict: dict | None = None


g = GraphBuilder(state_type=ScenarioState)


@g.step
async def init_scenario_node(ctx: StepContext[ScenarioState, None, None]) -> "run_agent_node":
    sc = ctx.state.sc
    tmp_dir = tempfile.mkdtemp()
    tmp = Path(tmp_dir) / Path(sc["fixture"]).name
    shutil.copy2(sc["fixture"], tmp)
    ctx.state.tmp_dir = tmp_dir
    ctx.state.fixture_path = tmp
    ctx.state.fg = load_flow_graph(str(tmp))
    return run_agent_node


@g.step
async def run_agent_node(ctx: StepContext[ScenarioState, None, None]) -> "verify_expectations_node":
    sc = ctx.state.sc
    agent = Agent(
        OllamaModel(MODEL, provider=OllamaProvider(base_url=OLLAMA_V1)),
        deps_type=Any,
        output_type=[GrcAgentResponse, str],
        name="grc_scenario_agent",
        instructions=build_system_prompt("pai-experiment"),
        tools=grc_tools(),
        capabilities=[
            ProcessHistory(prune_history),
            StopGracefully(),
            web_search_cap,
            web_fetch_cap,
        ],
        model_settings=ModelSettings(extra_body={"think": True}),
    )
    agent.output_validator(validate_flowgraph_state)

    print("Invoking agent run...")
    ctx.state.res = await agent.run(sc["prompt"], deps=ctx.state.fg)
    return verify_expectations_node


@g.step
async def verify_expectations_node(
    ctx: StepContext[ScenarioState, None, None],
) -> "report_and_clean_node":
    ctx.state.verdict = check_expect(
        ctx.state.fixture_path, ctx.state.sc["expect"], run_result=ctx.state.res
    )
    return report_and_clean_node


@g.step
async def report_and_clean_node(ctx: StepContext[ScenarioState, None, None]) -> End[dict]:
    sc = ctx.state.sc
    res = ctx.state.res
    verdict = ctx.state.verdict

    output_dir = Path("tests/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    md_log = render_scenario_markdown(sc, ctx.state.grc_before, res, verdict)
    log_path = output_dir / f"{sc['name']}.md"
    log_path.write_text(md_log, encoding="utf-8")
    print(f"Saved scenario log to {log_path}")

    # Count turns and tool calls
    tool_calls_count = 0
    tool_counts = {}
    from pydantic_ai.messages import ToolCallPart

    if res:
        for msg in res.all_messages():
            if hasattr(msg, "parts"):
                for p in msg.parts:
                    if isinstance(p, ToolCallPart):
                        tool_calls_count += 1
                        tool_counts[p.tool_name] = tool_counts.get(p.tool_name, 0) + 1

    # Teardown temporary resources
    if ctx.state.tmp_dir:
        shutil.rmtree(ctx.state.tmp_dir)

    return End(
        {
            "name": sc["name"],
            "pass": verdict["pass"],
            "reasons": verdict["reasons"],
            "turns": len(res.all_messages()) if res else 0,
            "tool_calls": tool_calls_count,
            "tool_counts": tool_counts,
        }
    )


def main():
    sc_filter = os.environ.get("GRC_AGENT_PAI_SCENARIOS", "01,11")
    indices = [s.strip() for s in sc_filter.split(",")]
    run_scenarios = [s for s in SCENARIOS if any(ind in s["name"] for ind in indices)]

    print(
        f"Starting experiment sweep on {len(run_scenarios)} scenarios using {MODEL} via {OLLAMA_V1}..."
    )
    metrics = []

    import asyncio

    runner_graph = g.build()

    for sc in run_scenarios:
        print("\n==================================================")
        print(f"Running scenario: {sc['name']}")
        print("==================================================")

        grc_before = Path(sc["fixture"]).read_text(encoding="utf-8")
        state = ScenarioState(sc=sc, grc_before=grc_before)

        try:
            scenario_metric = asyncio.run(runner_graph.run(state=state))
            metrics.append(scenario_metric)
            print(
                f"Verdict for {sc['name']}: pass={scenario_metric['pass']}, reasons={scenario_metric['reasons']}"
            )
        except Exception as e:
            print(f"Scenario {sc['name']} failed with error: {e}")
            metrics.append(
                {
                    "name": sc["name"],
                    "pass": False,
                    "reasons": [str(e)],
                    "turns": 0,
                    "tool_calls": 0,
                    "tool_counts": {},
                }
            )

    # Write METRICS.md
    metrics_path = Path("tests/output/METRICS.md")
    print(f"\nWriting execution metrics to {metrics_path}...")

    with open(metrics_path, "w", encoding="utf-8") as f:
        f.write("# PydanticAI Experiment Metrics\n\n")
        f.write("| Scenario | Verdict | Turns | Tool Calls | Breakdowns | Reasons |\n")
        f.write("|----------|---------|-------|------------|------------|---------|\n")
        for m in metrics:
            status_emoji = "✅ PASS" if m["pass"] else "❌ FAIL"
            breakdown_str = ", ".join(f"{k}:{v}" for k, v in m["tool_counts"].items())
            reasons_str = "; ".join(m["reasons"]) if m["reasons"] else "None"
            f.write(
                f"| {m['name']} | {status_emoji} | {m['turns']} | {m['tool_calls']} | `{breakdown_str}` | {reasons_str} |\n"
            )

    print("Done.")


if __name__ == "__main__":
    main()
