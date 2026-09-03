import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, BeforeValidator, Field
from pydantic_ai import (
    ApprovalRequired,
    ModelRequestNode,
    ModelRetry,
    RunContext,
    Tool,
    ToolFailed,
)
from pydantic_ai.capabilities import (
    AbstractCapability,
    AgentNode,
    NodeResult,
    RawToolArgs,
    WebFetch,
    WebSearch,
    WrapNodeRunHandler,
)
from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.result import FinalResult
from pydantic_ai.tools import ToolDefinition
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
from grc_agent.deps import (
    FlowgraphDeps,
    SupportsGetRunLog,
    SupportsNotifyEdit,
    SupportsRunFlowgraph,
    SupportsSaveBlock,
    SupportsSaveGraph,
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
        ...,
        validation_alias=AliasChoices("block_id", "id"),
        description="Installed GNU Radio catalog block ID (e.g. 'analog_sig_source_x').",
    )
    instance_name: str = Field(
        ..., description="New unique graph instance name (e.g. 'my_source')."
    )
    params: dict[str, Any] | None = Field(
        None, description="Initial parameter values keyed by parameter ID."
    )
    state: Literal["enabled", "disabled", "bypass"] | None = Field(
        None, description="Initial block state; defaults to 'enabled'."
    )


class ParamUpdate(BaseModel):
    instance_name: str = Field(..., description="Target block instance name (e.g. 'my_source').")
    params: dict[str, Any] = Field(..., description="Param updates keyed by parameter ID.")


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
    # ToolFailed reports a terminal failure and deliberately consumes no retry
    # budget, so a tool that keeps failing is bounded only by max_requests —
    # 40 model round-trips of the same dead end. Cap the repeats instead:
    # pydantic-ai's own guidance is to bound repeated failures at the run
    # level, which is what AGENTS.md section 3's do-not-retry prose used to
    # approximate in words.
    max_repeated_failures: int = 3
    count: int = 0

    async def for_run(self, ctx: RunContext[FlowgraphDeps]) -> "StopGracefully":  # noqa: ARG002
        return StopGracefully(
            max_requests=self.max_requests,
            max_repeated_failures=self.max_repeated_failures,
        )

    def _repeatedly_failing_tool(self, ctx: RunContext[FlowgraphDeps]) -> str | None:
        """The tool that has just failed terminally too many times, if any.

        Counts the trailing run of failed returns rather than a total, so a
        tool that fails once, succeeds, then fails again is not penalised.
        """
        streak: dict[str, int] = {}
        for msg in reversed(list(ctx.messages)):
            parts = getattr(msg, "parts", ())
            settled = [p for p in parts if getattr(p, "outcome", None) is not None]
            if not settled:
                continue
            for part in settled:
                if part.outcome != "failed":
                    return None
                name = getattr(part, "tool_name", "") or ""
                streak[name] = streak.get(name, 0) + 1
                if streak[name] >= self.max_repeated_failures:
                    return name
        return None

    async def wrap_node_run(
        self,
        ctx: RunContext[FlowgraphDeps],
        *,
        node: AgentNode,
        handler: WrapNodeRunHandler,
    ) -> NodeResult:
        if isinstance(node, ModelRequestNode):
            failing = self._repeatedly_failing_tool(ctx)
            if failing:
                _log.warning(
                    "StopGracefully: %r failed terminally %d times in a row — ending run",
                    failing,
                    self.max_repeated_failures,
                )
                return End(
                    FinalResult(
                        output=(
                            f"`{failing}` is not available in this environment and kept "
                            "failing, so I'm stopping rather than retrying it further. "
                            "The rest of the session is unaffected."
                        )
                    )
                )
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
@dataclass
class JsonRepairCapability(AbstractCapability[Any]):
    """Automatically decodes JSON-stringified tool arguments before validation.

    Many small, fast, or remote LLMs format complex nested tool parameters (such as lists
    or objects) as serialized JSON strings instead of native nested JSON arrays/objects.

    This capability hooks into Pydantic AI's official `before_tool_validate` lifecycle
    to unpack JSON-encoded values for any parameter whose schema expects an array or object.
    """

    async def before_tool_validate(
        self,
        ctx: RunContext[Any],  # noqa: ARG002
        *,
        call: ToolCallPart,  # noqa: ARG002
        tool_def: ToolDefinition,
        args: RawToolArgs,
    ) -> RawToolArgs:
        if isinstance(args, str):
            with contextlib.suppress(Exception):
                args = json.loads(args)
            if not isinstance(args, dict):
                return args

        if isinstance(args, dict):
            props = tool_def.parameters_json_schema.get("properties", {})
            for k, v in list(args.items()):
                if isinstance(v, str):
                    s = v.strip()
                    param_spec = props.get(k, {})
                    target_type = param_spec.get("type")
                    is_composite = (
                        target_type in ("array", "object")
                        or "items" in param_spec
                        or "$ref" in param_spec
                    )
                    if is_composite and (
                        (s.startswith("[") and s.endswith("]"))
                        or (s.startswith("{") and s.endswith("}"))
                    ):
                        with contextlib.suppress(Exception):
                            args[k] = json.loads(s)
        return args


web_search_cap = WebSearch(local=duckduckgo_search_tool(max_results=5))
web_fetch_cap = WebFetch(local=True)
json_repair_cap = JsonRepairCapability()


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
async def inspect_graph_func(ctx: RunContext[FlowgraphDeps], targets: list[str] | None = None) -> str:
    """Read-only inspection of the active graph: topology, block instances, connections, parameter values and validation status.

    Advanced parameters at their default value and unconnected optional ports
    are left out. An "omitted_*_count" appears only when something actually
    was omitted, so a missing counter means nothing was hidden.

    Args:
        targets: Block/variable instance names to scope inspection to (e.g. ["samp_rate", "blocks_head_0"]). Omit to inspect the full graph.
    """
    result = inspect_graph(ctx.deps, targets=targets)
    if not result.get("ok", True):
        raise ModelRetry(f"Inspection failed. Errors: {result.get('errors') or '(no detail)'}")
    return json.dumps(result)


# Bounds live in the schema, not in a clamp. The model sees minimum/maximum
# and an out-of-range value is rejected by argument validation before the tool
# body runs, instead of being silently rewritten behind its back.
_K_DESCRIPTION = (
    "How many results to return (1-20, default 5). Raise it for broader recall; "
    "lower it when you already know the target."
)
ResultCount = Annotated[int, Field(ge=1, le=20, description=_K_DESCRIPTION)]

# Exactly one '->' and exactly one ':' on each side — the same rule parse_conn
# enforces, moved into the schema so a malformed string is an argument-
# validation error the model can correct cheaply, rather than a burned
# domain retry after the mutation engine has already started.
_CONNECTION_PATTERN = r"^[^:>]+:[^:>]+->[^:>]+:[^:>]+$"
ConnectionSpec = Annotated[
    str,
    Field(
        pattern=_CONNECTION_PATTERN,
        description="'src_block:src_port->dst_block:dst_port', e.g. 'source_0:0->sink_0:0'.",
    ),
]


async def query_knowledge_func(
    ctx: RunContext[FlowgraphDeps],  # noqa: ARG001
    query: str,
    domain: Literal["catalog", "docs"],
    k: ResultCount = 5,
) -> str:
    """Answer GNU Radio questions from the local corpus: block schemas and parameter semantics, or DSP concepts.

    Args:
        query: The search text.
        domain: "catalog" for block IDs, ports, parameter keys and units; "docs" for concepts and how-to.
        k: How many results to return.
    """
    engine = query_catalog if domain == "catalog" else query_docs
    res = await asyncio.to_thread(engine, query, k)
    if not res.get("ok", True):
        raise ModelRetry(
            f"Knowledge lookup failed ({domain}): {res.get('message') or '(no detail)'}"
        )
    return json.dumps(res)


async def generate_python_func(ctx: RunContext[FlowgraphDeps], k: ResultCount = 5) -> str:
    """Render the Python source GNU Radio would generate from the current graph. Read-only — never writes to disk or runs the flowgraph.

    Returns the main flowgraph script plus one entry per Embedded Python
    Block/Module, capped at k; any dropped are counted in "omitted_files".

    Args:
        k: Max number of block-source files to include alongside the main script.
    """
    try:
        result = preview_flowgraph_py(ctx.deps, k=k)
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    return json.dumps(result)


def coerce_json_sequence(v: Any) -> Any:
    """Decode a JSON string if the argument was passed as a serialized JSON array."""
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                return json.loads(s)
            except Exception as exc:
                raise ModelRetry(f"Invalid JSON array string: {exc}") from exc
    return v


JsonCoercedSequence = BeforeValidator(coerce_json_sequence)


async def change_graph_func(
    ctx: RunContext[FlowgraphDeps],
    reason: str,
    add_blocks: Annotated[list[BlockAdd], JsonCoercedSequence, Field(default_factory=list)],
    remove_blocks: Annotated[list[str], JsonCoercedSequence, Field(default_factory=list)],
    update_params: Annotated[list[ParamUpdate], JsonCoercedSequence, Field(default_factory=list)],
    update_states: Annotated[list[StateUpdate], JsonCoercedSequence, Field(default_factory=list)],
    add_connections: Annotated[list[ConnectionSpec], JsonCoercedSequence, Field(default_factory=list)],
    remove_connections: Annotated[list[ConnectionSpec], JsonCoercedSequence, Field(default_factory=list)],
    force: bool = False,
) -> str:
    """Apply a batch of structural graph edits as one atomic transaction, after the user approves it.

    Nothing is mutated before approval, and a failed batch rolls back whole.
    A type-controlling param set to 'auto' resolves from an explicit value on a
    connected neighbour, including one added in this same call; if neither side
    has one the call fails rather than guessing.

    Args:
        reason: One-sentence justification of this edit's intent, shown to the
            user alongside the proposed changes for approval.
        add_blocks: New blocks to create.
        remove_blocks: Instance names of blocks to delete.
        update_params: Parameter updates for existing (or just-added) blocks.
        update_states: enabled/disabled/bypass updates for existing (or just-added) blocks.
        add_connections: New connections to make.
        remove_connections: Connections to remove.
        force: Bypass GNU Radio's own validation failures (e.g. an
            intentionally unconnected port mid-edit). Does not bypass this
            tool's own argument errors (unknown param, missing block).
    """
    # Engine phase order (fixed regardless of argument order, backend detail
    # the model does not need): remove_connections, remove_blocks, add_blocks,
    # update_params, resolve 'auto' types, update_states, add_connections.
    # The engine treats an empty batch and a missing one identically, so an
    # empty list collapses to None rather than widening every argument's
    # schema with a null branch.
    add_blocks_dict = [b.model_dump(exclude_none=True) for b in add_blocks] or None
    update_params_dict = [p.model_dump(exclude_none=True) for p in update_params] or None
    update_states_dict = [s.model_dump() for s in update_states] or None

    res = change_graph(
        ctx.deps,
        add_blocks=add_blocks_dict,
        remove_blocks=remove_blocks or None,
        update_params=update_params_dict,
        update_states=update_states_dict,
        add_connections=add_connections or None,
        remove_connections=remove_connections or None,
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
        if res.get("rollback_failed") and isinstance(ctx.deps, SupportsNotifyEdit):
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
    if isinstance(ctx.deps, SupportsNotifyEdit):
        await ctx.deps.notify_edit(relayout=bool(res.get("relayout")))
    # The `reason` argument is consumed by the approval UI, not by the engine;
    # it is echoed into the result so the persisted transcript carries the
    # edit's intent next to its outcome.
    return json.dumps({**res, "reason": reason})


async def get_run_log_func(ctx: RunContext[FlowgraphDeps]) -> str:
    """Read stdout + stderr from the most recent flowgraph run.

    Diagnoses runtime faults a static graph cannot show — missing hardware,
    driver errors, parameter mismatches. The result carries its own status
    fields for truncation, an in-flight run, and a graph edited since.
    """
    # A missing monitor is a wiring fault, not an empty result: reporting it as
    # ordinary data made it indistinguishable from "no run yet". ToolFailed is
    # the framework's own terminal-failure signal — the model sees the result
    # and adapts instead of being told in prose not to retry.
    if not isinstance(ctx.deps, SupportsGetRunLog):
        raise ToolFailed(
            "Run-log capture is not wired up in this environment, so no execution log "
            "exists to read. Tell the user and continue without it."
        )
    data = ctx.deps.get_run_log()
    if data is None:
        return json.dumps(
            {
                "log_text": "",
                "message": "No flowgraph has been run yet. Use GRC's Execute button to run the flowgraph first.",
            }
        )
    return json.dumps(data)


async def save_block_func(
    ctx: RunContext[FlowgraphDeps],
    instance_name: str,
    block_id: str | None = None,
    label: str | None = None,
    category: str | None = None,
    overwrite: bool = False,
) -> str:
    """Save an existing Embedded Python Block (epy_block) into GNU Radio's hier-block library, making it a reusable catalog block.

    The flowgraph's own epy_block is untouched; the saved block is a separate
    catalog entry usable by later change_graph calls anywhere. This is the
    hier-block library, not an out-of-tree module — say so if asked.

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
    if isinstance(ctx.deps, SupportsSaveBlock):
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


def _validate_run_flowgraph(ctx: RunContext[FlowgraphDeps], action: str = "start", **_: Any) -> None:
    """Decide approval before the tool body runs.

    Starting a flowgraph is a physical-world side effect (RF transmission) and
    is approval-gated; stopping one is the remedy, not the risk, and executes
    immediately. Raising ApprovalRequired here rather than inside the tool is
    the framework's documented placement: invalid arguments are rejected
    before a human is asked to approve them, the deferral costs no retry
    budget, and the validator re-runs with tool_call_approved set once the
    user approves. The action value itself needs no check — the Literal in the
    signature already rejects anything but 'start' or 'stop'.
    """
    if action == "start" and not ctx.tool_call_approved:
        raise ApprovalRequired()


async def run_flowgraph_func(
    ctx: RunContext[FlowgraphDeps],
    action: Literal["start", "stop"] = "start",
    wait: bool = True,
    timeout_seconds: float = 60.0,
    stop_after_seconds: float | None = None,
) -> str:
    """Control execution of the active GNU Radio flowgraph (start or stop).

    Runs through GRC's own runner, streaming console output live. Starting is
    approval-gated (RF safety); stopping is not.

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
    if not isinstance(ctx.deps, SupportsRunFlowgraph):
        raise ToolFailed(
            "Flowgraph execution is not wired up in this environment. Tell the user to "
            "use GRC's own Execute/Stop button and continue without this tool."
        )
    try:
        res = await ctx.deps.run_flowgraph(
            action=action,
            wait=wait,
            timeout_seconds=timeout_seconds,
            stop_after_seconds=stop_after_seconds,
        )
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    return json.dumps(res)


async def save_graph_func(ctx: RunContext[FlowgraphDeps]) -> str:
    """Save the active flowgraph to disk — the agent-side equivalent of Ctrl+S, with no dialog.

    An unsaved page lands in the project directory under a derived name; a
    saved one is rewritten in place. GRC generates into the saved graph's
    directory and runs from there, so save before run_flowgraph when the
    flowgraph is untitled.
    """
    if not isinstance(ctx.deps, SupportsSaveGraph):
        raise ToolFailed(
            "Saving the flowgraph is not wired up in this environment. Tell the user to "
            "save it in GRC (File > Save) and continue without this tool."
        )
    try:
        res = await ctx.deps.save_graph()
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    return json.dumps(res)


# Every domain tool derives its description and its per-argument schema text
# from its own google-style docstring, so the model-visible contract cannot
# drift from the real signature, and every one gets the same retry budget.
_TOOL_DEFAULTS: dict[str, Any] = {
    "docstring_format": "google",
    "require_parameter_descriptions": True,
    "max_retries": 3,
}


def grc_tools() -> list[Tool[Any]]:
    """The eight GRC domain tools, in the order the model sees them."""
    return [
        Tool(inspect_graph_func, name="inspect_graph", **_TOOL_DEFAULTS),
        Tool(query_knowledge_func, name="query_knowledge", **_TOOL_DEFAULTS),
        Tool(generate_python_func, name="generate_python", **_TOOL_DEFAULTS),
        Tool(
            change_graph_func,
            name="change_graph",
            # Pydantic AI's own human-in-the-loop mechanism: the call is never
            # executed — the run ends with a DeferredToolRequests output the
            # sidebar resolves with ToolApproved()/ToolDenied() first.
            requires_approval=True,
            **_TOOL_DEFAULTS,
        ),
        Tool(get_run_log_func, name="get_run_log", **_TOOL_DEFAULTS),
        Tool(
            run_flowgraph_func,
            name="run_flowgraph",
            # Approval is conditional on the action, so it is decided in the
            # validator rather than by a blanket requires_approval flag.
            args_validator=_validate_run_flowgraph,
            **_TOOL_DEFAULTS,
        ),
        # Agent-side save (Ctrl+S parity): the unsaved-run gate sends the model
        # here before a first run. No approval — a save is local and atomic,
        # and its guards fail before anything is touched.
        Tool(save_graph_func, name="save_graph", **_TOOL_DEFAULTS),
        Tool(save_block_func, name="save_block", **_TOOL_DEFAULTS),
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


async def validate_flowgraph_state(ctx: RunContext[FlowgraphDeps], output: Any) -> Any:
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
