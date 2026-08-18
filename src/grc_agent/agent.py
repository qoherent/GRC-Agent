import asyncio
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import (
    ModelRequest,
    ModelRequestNode,
    ModelRetry,
    RunContext,
    Tool,
)
from pydantic_ai.capabilities import (
    AbstractCapability,
    AgentNode,
    NodeResult,
    WebFetch,
    WebSearch,
    WrapNodeRunHandler,
)
from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.result import FinalResult
from pydantic_graph import End

# Local imports
from grc_agent.adapter import (
    change_graph,
    inspect_graph,
    load_flow_graph,
    preview_flowgraph_py,
    query_catalog,
    query_docs,
    save_block_to_library,
)
from grc_agent.prompts import build_system_prompt

_log = logging.getLogger(__name__)

MODEL = "qwen3.6:35b-a3b-q4_K_M"
OLLAMA_V1 = "http://localhost:11434/v1"


def build_scenario_model(provider: str, model_name: str | None = None) -> Any:
    """Build a model instance for the scenario/integration harness.

    The app has its own _build_model() that respects user settings; this
    helper is for the reproducible scenario harness and tests, which may run
    against either Ollama (local/cloud) or OpenAI-compatible (OpenRouter/llama.cpp/vLLM/etc.)
    depending on the environment.
    """
    if provider in ("openai_compatible", "openrouter"):
        key = (
            os.environ.get("OPENAI_COMPATIBLE_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
            or "not-required"
        )
        raw_url = (
            os.environ.get("OPENAI_COMPATIBLE_BASE_URL")
            or (
                "https://openrouter.ai/api/v1"
                if provider == "openrouter"
                else "http://localhost:8080/v1"
            )
        ).rstrip("/")
        base_url = raw_url if raw_url.endswith("/v1") else f"{raw_url}/v1"
        return OpenAIChatModel(
            model_name
            or os.environ.get("OPENAI_COMPATIBLE_MODEL")
            or os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash"),
            provider=OpenAIProvider(base_url=base_url, api_key=key),
        )
    if provider == "ollama_cloud":
        key = os.environ.get("OLLAMA_CLOUD_API_KEY", "")
        return OllamaModel(
            model_name or os.environ.get("OLLAMA_CLOUD_MODEL", "deepseek-v4-flash:cloud"),
            provider=OllamaProvider(
                base_url="https://ollama.com/v1",
                api_key=key,
            ),
        )
    raw_url = (os.environ.get("OLLAMA_BASE_URL") or OLLAMA_V1).rstrip("/")
    base_url = raw_url if raw_url.endswith("/v1") else f"{raw_url}/v1"
    key = os.environ.get("OLLAMA_API_KEY") or os.environ.get("OLLAMA_CLOUD_API_KEY")
    return OllamaModel(model_name or MODEL, provider=OllamaProvider(base_url=base_url, api_key=key))


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
    {
        # Not part of tests/test_integration.py's SELECTED_SCENARIOS multi-backend
        # sweep — it is driven by a single dedicated, ollama_cloud-only test
        # (test_scenario_lexical_fallback_ollama_cloud_only) that first breaks
        # the local embedding backend for real (bad OLLAMA_EMBEDDING_MODEL name
        # + a fresh GRC_AGENT_VECTORS_DIR — see rag.py's _embed_endpoint/query_catalog),
        # so query_knowledge's catalog lookup below is forced into its real
        # SQLite FTS5/BM25 lexical fallback (search_mode == "lexical") instead
        # of vector search. "complex conjugate" is deliberately literal/exact
        # wording — it is the block's own label text — so BM25 keyword matching
        # reliably surfaces `blocks_conjugate_cc` even with no embeddings at
        # all, proving the agent can still complete a real graph edit end to
        # end using only lexically retrieved catalog info.
        "name": "23_lexical_conjugate_insert",
        "fixture": "tests/data/resampler_demo.grc",
        "prompt": (
            "Inspect the flowgraph. Search the catalog for the block that"
            " computes the complex conjugate of a complex signal using"
            " query_knowledge (catalog domain) — don't guess the block id."
            " Add it, call it `signal_conjugate`, and insert it right after"
            " the resampler (`pfb_arb_resampler_xxx_0`) and before the"
            " resampled spectrum display (`qtgui_freq_sink_x_0_0`), so the"
            " resampler's output goes through the conjugate block before"
            " reaching that display. Remove the direct connection from the"
            " resampler to that display. Make sure the flowgraph is valid"
            " and inspect it to confirm."
        ),
        "expect": {
            "blocks_present": ["signal_conjugate"],
            "valid": True,
        },
    },
    {
        # In SELECTED_SCENARIOS (generic mode="read" check: some read tool was
        # used, answer non-empty) AND covered by a dedicated test,
        # test_scenario_generate_python_writes_nothing_to_disk, which asserts
        # generate_python specifically was called, that it returned real
        # generated source, and — the tool's actual load-bearing promise —
        # that the fixture's temp directory holds exactly the same files
        # after the live agent turn as before it.
        "name": "24_generate_python_preview",
        "fixture": "tests/data/dial_tone.grc",
        "prompt": (
            "Show me the Python code GNU Radio would actually generate for"
            " this flowgraph — use the generate_python tool for the real"
            " generated source, don't write or guess the code yourself."
            " Briefly summarize what it does. Don't change the graph."
        ),
        "expect": {"mode": "read"},
    },
    {
        "name": "25_save_epy_block_to_library",
        "fixture": "tests/data/empty.grc",
        "prompt": (
            "Inspect the flowgraph — right now it's empty except for the"
            " samp_rate variable. Write a small Embedded Python Block from"
            " scratch, call it `scale_by_three`: a gr.sync_block subclass"
            " named `blk` that takes one float input stream, multiplies"
            " every sample by a constant `scale` parameter (default 3.0),"
            " and produces one float output stream. Add a signal source"
            " called `sig` (type float, freq 1000, amp 1.0, using"
            " samp_rate) and a null sink called `null_sink` (type float)."
            " Wire the source into your new epy block, and the epy block"
            " into the sink. Inspect to confirm the chain is valid. Then"
            " use the save_block tool to export `scale_by_three` into the"
            " reusable block library under the block_id"
            " `agent_test_scale_multiplier` — if a block with that id"
            " already exists from a previous run, pass overwrite=True to"
            " replace it."
        ),
        "expect": {
            "blocks_present": ["scale_by_three", "sig", "null_sink"],
            "valid": True,
            "tools_called": ["save_block"],
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

    async def for_run(self, ctx: RunContext[Any]) -> "StopGracefully":  # noqa: ARG002
        return StopGracefully(max_requests=self.max_requests)

    async def wrap_node_run(
        self,
        ctx: RunContext[Any],  # noqa: ARG002
        *,
        node: AgentNode,
        handler: WrapNodeRunHandler,
    ) -> NodeResult:
        if isinstance(node, ModelRequestNode):
            self.count += 1
            if self.count > self.max_requests:
                _log.warning(
                    "StopGracefully: hit max_requests=%d ceiling — ending run to avoid a stuck loop",
                    self.max_requests,
                )
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
# providers without it (Ollama has none) it falls back to `local` — here
# upstream pydantic-ai's own ddgs-backed `duckduckgo_search` tool and the
# bundled markdownify `web_fetch`. Both wrap the same `ddgs` engine our old
# hand-rolled adapter/search.py used (a hard dependency), while gaining a
# proper tool name (`duckduckgo_search`, not the wrapped function's) and
# honest error semantics (network failures raise into pydantic-ai's tool
# retry instead of returning a masked "Web search failed: ..." string).
# Eager (defer_loading=False) so the tools are always callable — no
# load_capability round-trip. Defined once here and imported by
# agent_factory.py / tests so every Agent shares the same instances.
web_search_cap = WebSearch(local=duckduckgo_search_tool(max_results=5))
web_fetch_cap = WebFetch(local=True)


def fresh_agent(fixture):
    tmp_dir = tempfile.mkdtemp()
    tmp = Path(tmp_dir) / Path(fixture).name
    shutil.copy2(fixture, tmp)
    fg = load_flow_graph(str(tmp))
    return fg, tmp, tmp_dir


async def _with_state_lock(ctx: RunContext[Any], fn):
    """Run the zero-arg callable `fn` under ctx.deps's state lock if it
    exposes one — a no-op passthrough otherwise (the native desktop app's
    NativeFlowgraphProxy returns None from get_state_lock since gbulb runs
    everything on one thread; the scenario harness passes a raw flowgraph
    as deps, which has no lock at all)."""
    if hasattr(ctx.deps, "get_state_lock"):
        lock = ctx.deps.get_state_lock()
        if lock is not None:
            async with lock:
                return fn()
    return fn()


# Module-level tool functions
async def inspect_graph_func(ctx: RunContext[Any], targets: list[str] | str | None = None) -> str:
    """Read-only inspection of the active graph. Returns topology, block instances, connections, parameter values, and validation status.

    Args:
        targets: Block/variable instance name(s) to scope inspection to (e.g. ["samp_rate", "blocks_head_0"] or "samp_rate"). Omit or pass null to inspect the full graph.
    """
    result = await _with_state_lock(
        ctx, lambda: inspect_graph(ctx.deps, targets=targets, view="overview")
    )
    return json.dumps(result)


_QUERY_KNOWLEDGE_MIN_K = 1
_QUERY_KNOWLEDGE_MAX_K = 20


async def query_knowledge_func(
    ctx: RunContext[Any],  # noqa: ARG001
    query: str,
    domain: Literal["catalog", "docs"],
    k: int = 5,
) -> str:
    """Answer GNU Radio knowledge questions from two domains: catalog (block IDs, port names, parameter keys) or docs (concepts).

    Args:
        query: The search text.
        domain: "catalog" for block lookups, "docs" for conceptual/how-to questions.
        k: How many results to return. Defaults to 5 — raise it (e.g. 10-20)
            when you need broader recall (a vague query, or comparing several
            candidate blocks); lower it (e.g. 2-3) when you already know
            roughly what you're looking for and just need the top match.
            Clamped to 1-20.
    """
    k = max(_QUERY_KNOWLEDGE_MIN_K, min(_QUERY_KNOWLEDGE_MAX_K, k))
    if domain == "catalog":
        res = await asyncio.to_thread(query_catalog, query, k)
        return json.dumps(res)
    else:
        res = await asyncio.to_thread(query_docs, query, k)
        return json.dumps(res)


async def generate_python_func(ctx: RunContext[Any], k: int = 5) -> str:
    """Render the Python source GNU Radio would generate from the current graph. Read-only — never writes to disk or runs the flowgraph.

    Returns one entry per generated file: the main flowgraph script, plus one
    per Embedded Python Block/Module instance if any are present. The main
    script is always included; if there are more block-source files than
    fit, the excess is dropped and counted in "omitted_files" — never
    silently. Raises if the graph is currently invalid, or is a
    hierarchical-block or C++-output flowgraph (neither can be rendered this
    way) — fix the graph with change_graph and retry.

    Args:
        k: Max number of block-source files to include alongside the main
            script (the main script itself doesn't count against this).
            Defaults to 5 — raise it (up to 20) only if you actually need to
            see every Embedded Python Block/Module's source in one call.
    """
    try:
        result = await _with_state_lock(ctx, lambda: preview_flowgraph_py(ctx.deps, k=k))
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    return json.dumps(result)


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
    """Apply a batch of structural graph edits in a single transaction.

    Runs in a fixed phase order regardless of argument order: remove_connections,
    remove_blocks, add_blocks, update_params, update_states, add_connections. A
    type-controlling param (e.g. 'type') set to the literal string 'auto' is
    resolved from an explicit, non-'auto' value on a connected neighbor —
    including one added and connected in this same call — but only if at
    least one side of the connection has such a value; set an explicit type
    on at least one side rather than 'auto' on both, or the call fails with
    an actionable error instead of guessing.

    Args:
        add_blocks: New blocks to create.
        remove_blocks: Instance names of blocks to delete.
        update_params: Parameter updates for existing (or just-added) blocks.
        update_states: enabled/disabled/bypass updates for existing (or just-added) blocks.
        add_connections: New connections, each formatted
            'src_block:src_port->dst_block:dst_port' (e.g. 'source_0:0->sink_0:0').
        remove_connections: Connections to remove, same format as add_connections.
        force: Bypass GNU Radio's own validation failures (e.g. an
            intentionally unconnected port mid-edit). Does not bypass this
            tool's own argument errors (unknown param, missing block).
    """
    add_blocks_dict = [b.model_dump(exclude_none=True) for b in add_blocks] if add_blocks else None
    update_params_dict = (
        [p.model_dump(exclude_none=True) for p in update_params] if update_params else None
    )
    update_states_dict = [s.model_dump() for s in update_states] if update_states else None

    res = await _with_state_lock(
        ctx,
        lambda: change_graph(
            ctx.deps,
            add_blocks=add_blocks_dict,
            remove_blocks=remove_blocks,
            update_params=update_params_dict,
            update_states=update_states_dict,
            add_connections=add_connections,
            remove_connections=remove_connections,
            force=force,
        ),
    )
    if not res.get("ok"):
        # force=True only ever bypasses the native-validation gate
        # (error_type == "validation_failed") — every other failure in the
        # `errors` list (auto_resolve_failed, connection_silently_dropped,
        # add_connection_failed, etc.) is rolled back unconditionally
        # regardless of force, so suggesting it there is actively misleading
        # and can waste one of the model's limited retries chasing something
        # that can never succeed.
        hint = (
            "Set force=True to bypass GNU Radio's own validation opinion and retry."
            if res.get("error_type") == "validation_failed"
            else "Adjust your parameters/connections based on the errors above and retry — force=True will not help here."
        )
        raise ModelRetry(
            f"Graph modification failed. Errors: {res.get('errors') or res.get('message') or '(no detail)'}. {hint}"
        )
    # Tell the live GTK canvas (if any) to redraw — the agent mutated the very
    # same in-memory FlowGraph the canvas renders (single-process, shared
    # object), so there is nothing to reload from disk; notify_edit just queues
    # a draw, scrolls to new blocks, and refreshes the sync baseline. The
    # outcome is surfaced so a desync isn't silent; on a raw flowgraph deps
    # (scenario harness) notify_edit is absent and this is skipped.
    if hasattr(ctx.deps, "notify_edit"):
        res["canvas_synced"] = (await ctx.deps.notify_edit()).get("ok", False)
    return json.dumps(res)


async def get_run_log_func(ctx: RunContext[Any]) -> str:
    """Read the console output (stdout + stderr) of the most recent flowgraph run.

    Returns the full captured log from the last Execute action, whether it succeeded
    or failed. Use this after running a flowgraph to diagnose runtime errors (e.g.
    hardware not found, parameter mismatches, GPU/CPU issues) that are not visible
    in the static graph structure.

    The log is retained until the next run — you can call this tool at any time
    after a run to re-read the output.
    """
    get_fn = getattr(ctx.deps, "get_run_log", None)
    if get_fn is None or not callable(get_fn):
        return json.dumps(
            {
                "log_text": "",
                "message": "No execution log available — no run monitor wired.",
            }
        )
    data = get_fn()
    if data is None:
        return json.dumps(
            {
                "log_text": "",
                "message": "No flowgraph has been run yet. Use GRC's Execute button to run the flowgraph first.",
            }
        )
    return json.dumps(data)


async def save_block_func(
    ctx: RunContext[Any],
    instance_name: str,
    block_id: str | None = None,
    label: str | None = None,
    category: str | None = None,
    overwrite: bool = False,
) -> str:
    """Save an existing Embedded Python Block (epy_block) instance into GNU Radio's native hier-block library so it becomes a reusable catalog block for future flowgraphs.

    Does NOT modify the current flowgraph's own epy_block instance — it keeps using
    its own local inline source, unaffected. The saved block is a new, separately
    named catalog entry available for future change_graph calls (in this flowgraph
    or any other) once this call succeeds. This is not an out-of-tree (OOT) module —
    it's GNU Radio's lighter hier-block library mechanism (~/.grc_gnuradio); say so
    if asked, rather than calling it OOT.

    Args:
        instance_name: The epy_block instance in the current flowgraph to export.
        block_id: Desired catalog block id. Defaults to instance_name. Must be a
            valid Python identifier — it becomes both the catalog id and the saved
            module's filename.
        label: Human-readable block-tree label. Defaults to a title-cased block_id.
        category: GRC category path. Defaults to "[Custom]".
        overwrite: Set True to replace a block_id this tool previously saved. Never
            allowed to overwrite a stock or foreign block regardless of this flag.
    """
    if hasattr(ctx.deps, "save_block"):
        res = await ctx.deps.save_block(
            instance_name,
            block_id=block_id,
            label=label,
            category=category,
            overwrite=overwrite,
        )
    else:
        res = await _with_state_lock(
            ctx,
            lambda: save_block_to_library(
                ctx.deps,
                instance_name,
                block_id=block_id,
                label=label,
                category=category,
                overwrite=overwrite,
            ),
        )
    if not res.get("ok"):
        raise ModelRetry(f"Failed to save block. Errors: {res.get('errors') or '(no detail)'}")
    return json.dumps(res)


def grc_tools() -> list[Tool[Any]]:
    inspect_tool = Tool(
        inspect_graph_func,
        name="inspect_graph",
        docstring_format="google",
        require_parameter_descriptions=True,
    )

    query_tool = Tool(
        query_knowledge_func,
        name="query_knowledge",
        docstring_format="google",
        require_parameter_descriptions=True,
    )

    generate_python_tool = Tool(
        generate_python_func,
        name="generate_python",
        docstring_format="google",
        require_parameter_descriptions=True,
    )

    change_tool = Tool(
        change_graph_func,
        name="change_graph",
        # docstring_format + require_parameter_descriptions is PydanticAI's
        # own sanctioned idiom for deriving both the tool description and
        # each top-level arg's schema description straight from the
        # docstring — one source of truth instead of a hand-written
        # description that can silently drift from the real signature.
        docstring_format="google",
        require_parameter_descriptions=True,
    )
    change_tool.max_retries = 3

    run_log_tool = Tool(
        get_run_log_func,
        name="get_run_log",
        docstring_format="google",
        require_parameter_descriptions=True,
    )

    save_block_tool = Tool(
        save_block_func,
        name="save_block",
        docstring_format="google",
        require_parameter_descriptions=True,
    )
    save_block_tool.max_retries = 3

    return [
        inspect_tool,
        query_tool,
        generate_python_tool,
        change_tool,
        run_log_tool,
        save_block_tool,
    ]


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
        # Hold the same state lock as the tool functions for harness
        # consistency. In this single-process gbulb desktop app,
        # NativeFlowgraphProxy.get_state_lock() returns None (no races),
        # so _with_state_lock is a no-op passthrough — but mirroring the
        # tool pattern keeps the harness safe if this validator ever
        # gains an await or runs under a different harness.
        def _do_validate():
            fg = ctx.deps
            # is_valid()/iter_error_messages() only read _error_messages, which
            # only validate() populates (rewrite() clears it without refilling)
            # — call it explicitly rather than assuming some earlier tool call
            # in this turn happened to leave it fresh.
            fg.validate()
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

        return await _with_state_lock(ctx, _do_validate)
    return output


# Tool names that satisfy a mode == "read" scenario's expectation. Also the
# default input to the generic tool-usage check below — one uniform helper
# backs both, rather than a hand-picked heuristic special-cased per scenario.
_READ_TOOLS = ("query_knowledge", "inspect_graph", "generate_python")


def _any_tool_called(run_result, tool_names) -> bool:
    """True if any ToolCallPart in the run's real message history used one
    of the given tool names. Backs both the mode == "read" check and any
    scenario's explicit `tools_called` expectation — a single reusable
    tool-usage check instead of a one-off hardcoded for a specific scenario."""
    if not run_result:
        return False
    from pydantic_ai.messages import ToolCallPart

    for msg in run_result.all_messages():
        if hasattr(msg, "parts") and any(
            isinstance(p, ToolCallPart) and p.tool_name in tool_names for p in msg.parts
        ):
            return True
    return False


def check_expect(fixture_path, expect, run_result=None):  # noqa: C901
    fg = load_flow_graph(str(fixture_path))
    snap = inspect_graph(fg)["graph"]
    valid = snap["validation"]["status"] == "valid"
    names = {b["instance_name"] for b in snap["blocks"]}
    params = {b["instance_name"]: b["params"] for b in snap["blocks"]}
    states = {b["instance_name"]: b["state"] for b in snap["blocks"]}

    fail_reasons = []
    mode = expect.get("mode", "edit")

    if mode == "read":
        if not _any_tool_called(run_result, _READ_TOOLS):
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

    # Applies regardless of mode: any scenario can require one or more
    # specific named tools to have actually been called during the run (e.g.
    # save_block, which doesn't mutate the flowgraph itself so nothing above
    # would otherwise notice whether it ran) — one uniform check reusable by
    # any scenario, not a hand-picked one-off for a single case.
    for tool_name in expect.get("tools_called") or []:
        if not _any_tool_called(run_result, (tool_name,)):
            fail_reasons.append(f"tool {tool_name!r} was never called")

    return {"pass": not fail_reasons, "reasons": fail_reasons, "valid": valid}


def render_scenario_markdown(sc, grc_before, run_result, verdict) -> str:  # noqa: C901
    events = []
    from pydantic_ai import ModelResponse
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
                if isinstance(part, ToolReturnPart) and part.tool_call_id in tool_calls:
                    tool_calls[part.tool_call_id]["result"] = part.content

    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    t_info = tool_calls.get(part.tool_call_id)
                    if t_info:
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


# NOTE: this module has no `__main__`/CLI entry point of its own — the
# scenario-running building blocks below (SCENARIOS, fresh_agent,
# check_expect, render_scenario_markdown, build_system_prompt, grc_tools)
# are consumed directly by tests/test_integration.py's own pytest-parametrized
# loop (agent.run_sync(...) per scenario), which is the actual, live scenario
# harness. A prior pydantic-graph-based runner (GraphBuilder/@g.step nodes +
# a main()) lived here but was dead: its graph's step return-type hints were
# bare forward-referenced function names, which pydantic-graph does not
# resolve into edges, so g.build() raised GraphValidationError("no edges
# from the start node") on the only two ways to reach it (running this file
# directly, or `python -m grc_agent.agent` — neither is a registered
# entry point; pyproject.toml's console script is grc-agent).
