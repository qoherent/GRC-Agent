import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import (
    ApprovalRequired,
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
from pydantic_ai.result import FinalResult
from pydantic_ai_harness import PromptInjectionDefender
from pydantic_graph import End

# Local imports
from grc_agent.adapter import (
    change_graph,
    inspect_graph,
    preview_flowgraph_py,
    query_catalog,
    query_docs,
    save_block_to_library,
)

_log = logging.getLogger(__name__)

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
# bundled markdownify `web_fetch`. Both wrap the same `ddgs` engine, while
# keeping a
# proper tool name (`duckduckgo_search`, not the wrapped function's) and
# honest error semantics (network failures raise into pydantic-ai's tool
# retry instead of returning a masked "Web search failed: ..." string).
# Eager (defer_loading=False) so the tools are always callable — no
# load_capability round-trip. Defined once here and imported by
# agent_factory.py / tests so every Agent shares the same instances.
web_search_cap = WebSearch(local=duckduckgo_search_tool(max_results=5))
web_fetch_cap = WebFetch(local=True)


def _log_injection_detection(ctx, call, verdict) -> None:  # noqa: ARG001
    """Observability for flagged tool results — never raises (an exception
    here would fail the run per the capability contract)."""
    _log.warning(
        "prompt-injection: tool=%s risk=%s detections=%s",
        call.tool_name,
        verdict.risk_level,
        list(verdict.detections),
    )


# Indirect prompt-injection defense over tool results. The agent now ingests
# untrusted text from two directions — user project files (read_file/
# search_files over the flowgraph's folder) and web content (web_fetch local
# fallback) — and it can WRITE files, so a planted instruction is not just a
# context hazard. One uniform rule: every client-executed tool result is
# classified with stackone-defender tier-1 pattern detection (no ML extra, no
# network), and every detection is logged via _log_injection_detection.
# Detect-and-log, never withhold (block_high_risk=False, 2026-08-28 user
# decision): withholding high-risk results false-positived on official GNU
# Radio doxygen pages — their own jQuery boilerplate (`$(document)` ×2) trips
# the tier-1 `shell_command` regex `\$\([^)]+\)` escalated to high by the
# 2-matches+entropy rule — and blinded the agent mid-build (sessions 150/151
# forensic). The tradeoff, accepted: a real injection now reaches the model
# but is loudly logged, and the ToolReturn passes through unchanged. Known
# scope limits, accepted: provider-native web tools run server-side and never
# transit the client; ModelRetry failure text is our own strings. Tier-1 runs
# on the event loop (~0.5ms/KB measured: 6ms for a 40KB inspect_graph, 48ms
# for a 100KB read) — same cost class as the app's existing sync file I/O on
# that loop.
prompt_injection_cap = PromptInjectionDefender(
    block_high_risk=False,
    on_detection=_log_injection_detection,
)
# Module-level tool functions
async def inspect_graph_func(ctx: RunContext[Any], targets: list[str] | str | None = None) -> str:
    """Read-only inspection of the active graph. Returns topology, block instances, connections, parameter values, and validation status.

    Args:
        targets: Block/variable instance name(s) to scope inspection to (e.g. ["samp_rate", "blocks_head_0"] or "samp_rate"). Omit or pass null to inspect the full graph.
    """
    result = inspect_graph(ctx.deps, targets=targets)
    if not result.get("ok", True):
        raise ModelRetry(f"Inspection failed. Errors: {result.get('errors') or '(no detail)'}")
    return json.dumps(result)


_QUERY_KNOWLEDGE_MIN_K = 1
_QUERY_KNOWLEDGE_MAX_K = 20


async def query_knowledge_func(
    ctx: RunContext[Any],  # noqa: ARG001
    query: str,
    domain: Literal["catalog", "docs"],
    k: int = 5,
) -> str:
    """Answer GNU Radio knowledge questions from two domains: catalog (block IDs, port names, parameter keys, and each block's implementation docstring with parameter units/semantics) or docs (concepts).

    Responses carry search_mode ('vector' | 'lexical' | 'hybrid') and output_truncated (true when more matching entries existed beyond the k returned — raise k or refine the query).

    Args:
        query: The search text.
        domain: "catalog" for block lookups, "docs" for conceptual/how-to questions.
        k: How many results to return (1-20, default 5). Raise toward 20 for
            broader recall or when comparing several candidates; lower to 2-3
            when you only need the top match.
    """
    k = max(_QUERY_KNOWLEDGE_MIN_K, min(_QUERY_KNOWLEDGE_MAX_K, k))
    engine = query_catalog if domain == "catalog" else query_docs
    res = await asyncio.to_thread(engine, query, k)
    if not res.get("ok", True):
        raise ModelRetry(
            f"Knowledge lookup failed ({domain}): {res.get('message') or '(no detail)'}"
        )
    return json.dumps(res)


async def generate_python_func(ctx: RunContext[Any], k: int = 5) -> str:
    """Render the Python source GNU Radio would generate from the current graph. Read-only — never writes to disk or runs the flowgraph.

    Returns one entry per generated file: the main flowgraph script plus one
    per Embedded Python Block/Module if any are present (excess block-source
    files are dropped and counted in "omitted_files", never silently). Fails
    (with a clear error) on invalid graphs, hierarchical blocks, or C++-output
    flowgraphs — fix the graph with change_graph and retry.

    Args:
        k: Max number of block-source files to include alongside the main
            script. Defaults to 5; raise it (up to 20) only when you need to
            see every block's source in one call.
    """
    try:
        result = preview_flowgraph_py(ctx.deps, k=k)
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    return json.dumps(result)


async def change_graph_func(
    ctx: RunContext[Any],
    reason: str,
    add_blocks: list[BlockAdd] | None = None,
    remove_blocks: list[str] | None = None,
    update_params: list[ParamUpdate] | None = None,
    update_states: list[StateUpdate] | None = None,
    add_connections: list[str] | None = None,
    remove_connections: list[str] | None = None,
    force: bool = False,
) -> str:
    """Apply a batch of structural graph edits as one atomic transaction.

    The edit is applied only after the user approves it — this tool requires
    human-in-the-loop approval, and the flowgraph is never mutated before then.

    A type-controlling param (e.g. 'type') set to the literal string 'auto' is
    resolved from an explicit, non-'auto' value on a connected neighbor —
    including one added and connected in this same call; if neither side has an
    explicit type, the call fails with an actionable error instead of guessing.

    Args:
        reason: One-sentence justification of this edit's intent, shown to the
            user alongside the proposed changes for approval.
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
    # Engine phase order (fixed regardless of argument order, backend detail
    # the model does not need): remove_connections, remove_blocks, add_blocks,
    # update_params, resolve 'auto' types, update_states, add_connections.
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
        # force=True only ever bypasses the native-validation gate
        # (error_type == "validation_failed") — every other failure in the
        # `errors` list (auto_resolve_failed, connection_silently_dropped,
        # add_connection_failed, etc.) is rolled back unconditionally
        # regardless of force, so suggesting it there is actively misleading
        # and can waste one of the model's limited retries chasing something
        # that can never succeed.
        if res.get("rollback_failed") and hasattr(ctx.deps, "notify_edit"):
            await ctx.deps.notify_edit(relayout=False)
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
    # a draw, fits the whole graph into view when this batch relaid out, and
    # refreshes the sync baseline. Its result is deliberately NOT reported to
    # the model: NativeFlowgraphProxy.notify_edit returns {"ok": True}
    # unconditionally and after_agent_edit logs GTK failures rather than
    # signalling them, so a `canvas_synced` field could only ever say True —
    # false assurance is worse than none. On a raw flowgraph deps (scenario
    # harness) notify_edit is absent and this is skipped.
    if hasattr(ctx.deps, "notify_edit"):
        await ctx.deps.notify_edit(relayout=bool(res.get("relayout")))
    # The `reason` argument is consumed by the approval UI, not by the engine;
    # it is echoed into the result so the persisted transcript carries the
    # edit's intent next to its outcome.
    return json.dumps({**res, "reason": reason})


async def get_run_log_func(ctx: RunContext[Any]) -> str:
    """Read the console output (stdout + stderr) of the most recent flowgraph run.

    Returns the captured log from the last Execute action, whether it succeeded
    or failed. Use this after running a flowgraph to diagnose runtime errors (e.g.
    hardware not found, parameter mismatches, GPU/CPU issues) that are not visible
    in the static graph structure.

    The log is retained until the next run — you can call this tool at any time
    after a run to re-read the output. If it was longer than the monitor's
    buffer, the oldest output is dropped and "log_truncated" is set.
    """
    get_fn = getattr(ctx.deps, "get_run_log", None)
    if get_fn is None or not callable(get_fn):
        # A missing monitor is a wiring fault in this app, not an empty result.
        # Reporting it as ordinary data made it indistinguishable from "no run
        # yet" — the model would read a broken environment as a normal one.
        raise ModelRetry(
            "The run monitor is not available, so no execution log can be read. This is an "
            "environment fault, not an empty log — do not retry this tool; tell the user that "
            "run-log capture is unavailable and continue without it."
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
    it's GNU Radio's lighter hier-block library mechanism; say so if asked, rather
    than calling it OOT.

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
        res = save_block_to_library(
            ctx.deps,
            instance_name,
            block_id=block_id,
            label=label,
            category=category,
            overwrite=overwrite,
        )
    if not res.get("ok"):
        raise ModelRetry(f"Failed to save block. Errors: {res.get('errors') or '(no detail)'}")
    return json.dumps(res)


async def run_flowgraph_func(
    ctx: RunContext[Any],
    action: Literal["start", "stop"] = "start",
    wait: bool = True,
    timeout_seconds: float = 60.0,
    stop_after_seconds: float | None = None,
) -> str:
    """Control execution of the active GNU Radio flowgraph (start or stop).

    Executes via GRC's native runner and streams console output live. Action 'start'
    is approval-gated (RF safety); 'stop' terminates immediately without approval.
    Read stdout/stderr with get_run_log after completion.

    Args:
        action: 'start' to run the active flowgraph, or 'stop' to terminate it (SIGTERM).
        wait: For action='start', True blocks until completion (for non-GUI graphs).
            False returns immediately while the graph runs in background (for QT GUI sinks).
        timeout_seconds: Max seconds to wait when wait=True before returning still_running.
            Ignored when stop_after_seconds is set.
        stop_after_seconds: Optional runtime budget for a bounded run: automatically
            stops the flowgraph after N seconds (requires wait=True). Leave unset to run
            until completion or manual stop.
    """
    if action not in ("start", "stop"):
        raise ModelRetry(f"Invalid action {action!r}: must be 'start' or 'stop'.")
    if action == "start" and not getattr(ctx, "tool_call_approved", False):
        raise ApprovalRequired()

    run_fn = getattr(ctx.deps, "run_flowgraph", None)
    if run_fn is None or not callable(run_fn):
        raise ModelRetry(
            "Flowgraph execution is not available in this environment — this is a wiring "
            "fault, not a fixable error. Do not retry; tell the user to use GRC's own "
            "Execute/Stop button and continue without this tool."
        )
    try:
        res = await run_fn(
            action=action,
            wait=wait,
            timeout_seconds=timeout_seconds,
            stop_after_seconds=stop_after_seconds,
        )
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    return json.dumps(res)


async def save_graph_func(ctx: RunContext[Any]) -> str:
    """Save the active flowgraph to disk through GRC's native serializer — the agent-side equivalent of Ctrl+S, with no dialog and no user interaction.

    Takes no arguments: it always saves the currently active GRC tab. A page
    that has never been saved is written into the project directory under a
    name derived from the graph's options id (collision-free); a page that
    already has a file is saved in place. The write is atomic. GRC generates
    into the saved graph's directory and executes from there, so save here
    before run_flowgraph when the flowgraph is untitled.

    Returns a JSON object {"path", "page"}: "path" is the saved .grc file's
    path and "page" is the tab name it lives under, so you can detect tab
    switches between calls. Failures raise a retryable error naming the
    target path or tab so you can correct course: no project directory is
    selected (select one first), the derived path is already open in another
    tab (switch to that tab and save there, or close it), the target file is
    read-only (make it writable), the target is locked by another writer
    (retry the save shortly), or the write itself failed (the target is left
    untouched).
    """
    save_fn = getattr(ctx.deps, "save_graph", None)
    if save_fn is None or not callable(save_fn):
        raise ModelRetry(
            "Saving the flowgraph is not available in this environment — this is a wiring "
            "fault, not a fixable error. Do not retry; tell the user to save the flowgraph "
            "in GRC (File > Save) and continue without this tool."
        )
    try:
        res = await save_fn()
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
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
        # PydanticAI's own sanctioned human-in-the-loop approval mechanism:
        # the model's call is never executed — the run ends with a
        # DeferredToolRequests output the UI resolves with
        # ToolApproved()/ToolDenied() before the tool body runs.
        requires_approval=True,
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

    # Execution control: starting a flowgraph raises ApprovalRequired() conditionally
    # inside run_flowgraph_func; stopping is safe and executes immediately.
    run_fg_tool = Tool(
        run_flowgraph_func,
        name="run_flowgraph",
        docstring_format="google",
        require_parameter_descriptions=True,
    )
    run_fg_tool.max_retries = 3

    # Agent-side save (Ctrl+S parity): the unsaved-run gate directs the model
    # here before a first run, so it sits next to run_flowgraph. No approval:
    # a save is local and atomic, and its guards fail before anything is
    # touched (never destructive).
    save_graph_tool = Tool(
        save_graph_func,
        name="save_graph",
        docstring_format="google",
        require_parameter_descriptions=True,
    )
    save_graph_tool.max_retries = 3

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
        run_fg_tool,
        save_graph_tool,
        save_block_tool,
    ]


def _extract_turn_pre_existing_errors(messages: list[Any]) -> set[str]:
    """Extract pre-existing error strings reported by the first change_graph execution in the current turn."""
    for msg in messages:
        if not hasattr(msg, "parts"):
            continue
        for part in msg.parts:
            part_kind = getattr(part, "part_kind", None) or part.__class__.__name__
            tool_name = getattr(part, "tool_name", None)
            if tool_name == "change_graph" and (
                part_kind in ("tool-return", "ToolReturnPart")
                or getattr(part, "outcome", None) == "success"
            ):
                content = getattr(part, "content", None)
                parsed = None
                if isinstance(content, str):
                    with contextlib.suppress(Exception):
                        parsed = json.loads(content)
                elif isinstance(content, dict):
                    parsed = content

                if isinstance(parsed, dict) and parsed.get("ok"):
                    # The first executed change_graph in the turn sets the turn's true pre-existing baseline
                    return set(parsed.get("pre_existing_errors") or [])
    return set()


async def validate_flowgraph_state(ctx: RunContext[Any], output: Any) -> Any:
    # A change_graph call only mutates the graph when it EXECUTED successfully:
    # denied calls (approval card) never run their body, and failed/rolled-back
    # calls leave the graph as it was before the call — validating the live
    # graph against either would blame the agent for pre-existing user state.
    # We restrict the scan to the current user turn (from the last UserPromptPart
    # onwards) so past turns' mutations do not falsely trigger validation here.
    messages = list(ctx.messages)
    start_idx = 0
    for idx, msg in enumerate(reversed(messages)):
        if hasattr(msg, "parts") and any(
            getattr(part, "part_kind", None) == "user-prompt"
            or part.__class__.__name__ == "UserPromptPart"
            for part in msg.parts
        ):
            start_idx = len(messages) - 1 - idx
            break

    current_turn_messages = messages[start_idx:]

    has_mutated = any(
        getattr(part, "tool_name", None) == "change_graph"
        and (
            getattr(part, "outcome", None) == "success"
            or "rollback_failed" in str(getattr(part, "content", ""))
            or "rollback failed" in str(getattr(part, "content", ""))
        )
        for msg in current_turn_messages
        if hasattr(msg, "parts")
        for part in msg.parts
    )
    if has_mutated:
        turn_pre_existing_errors = _extract_turn_pre_existing_errors(current_turn_messages)
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
            new_errors = [e for e in validation_errors if e not in turn_pre_existing_errors]
            if new_errors:
                raise ModelRetry(
                    f"The flowgraph has validation errors after mutation: {new_errors}. "
                    "You must run change_graph to correct these errors before completing the response."
                )
    return output
