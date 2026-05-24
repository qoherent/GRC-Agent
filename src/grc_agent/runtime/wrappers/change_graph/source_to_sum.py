"""Composite signal-source-to-summing-target operation for change_graph."""

from __future__ import annotations

import copy
import json
from typing import Any, Callable

from grc_agent._payload import ErrorCode
from grc_agent.catalog.describe import _describe_block_with_root
from grc_agent.models import Block, Connection
from grc_agent.validation.rules import BlockRules, get_block_rules, resolve_port_slots

from .context import ChangeGraphOperationContext, ChangeGraphOperationResult, ToolResult


def handle_add_signal_source_to_sum(
    *,
    ctx: ChangeGraphOperationContext,
    user_goal: str,
    target_ref: dict[str, Any] | None,
    block_id: str | None,
    instance_name: str | None,
    dst_block: str | None,
    dst_port: int | str | None,
    insert_params: dict[str, Any] | None,
    operation_args: dict[str, Any],
    tx_tool: Callable[[Any], ToolResult],
    kind_mismatch_result: Callable[..., ToolResult | None],
) -> ChangeGraphOperationResult:
    """Add a signal source and connect it to one inferred summing destination."""

    operation_summary = "add_signal_source_to_sum"
    mismatch = kind_mismatch_result("add_signal_source_to_sum")
    if mismatch is not None:
        return ChangeGraphOperationResult(
            handled=True,
            operation_summary=operation_summary,
            terminal_result=_terminal(ctx, mismatch, operation_summary),
        )

    flowgraph = ctx.agent.session.flowgraph
    if flowgraph is None:
        missing = ctx.agent._missing_session_result("change_graph")
        return ChangeGraphOperationResult(
            handled=True,
            operation_summary=operation_summary,
            terminal_result=_terminal(ctx, missing or {}, operation_summary),
        )

    source_block_id = _text(
        operation_args.get("source_block_id")
        or block_id
        or operation_args.get("block_id")
    )
    if source_block_id is None:
        return _clarification(
            ctx,
            operation_summary,
            "add_signal_source_to_sum requires args.block_id or args.source_block_id.",
            [
                "Use search_blocks if the source block id is unknown.",
                "For GNU Radio Signal Source, use block_id analog_sig_source_x when catalog evidence supports it.",
            ],
        )

    frequency = operation_args.get("freq", operation_args.get("frequency"))
    if frequency is None or (isinstance(frequency, str) and not frequency.strip()):
        return _clarification(
            ctx,
            operation_summary,
            "add_signal_source_to_sum requires an explicit frequency in args.freq.",
            ["Retry with args.freq set to the requested frequency."],
        )

    source_rules_lookup = get_block_rules(source_block_id, catalog_root=ctx.agent.catalog_root)
    if not source_rules_lookup.ok or source_rules_lookup.rules is None:
        return _refusal(
            ctx,
            operation_summary,
            ErrorCode.CATALOG_LOAD_ERROR,
            source_rules_lookup.message or f"Unknown source block id: {source_block_id}",
        )
    source_block_error = _source_block_guard(
        ctx=ctx,
        source_block_id=source_block_id,
        source_rules=source_rules_lookup.rules,
        blocks=flowgraph.blocks,
    )
    if source_block_error is not None:
        return _invalid_request(
            ctx,
            operation_summary,
            source_block_error["message"],
            source_block_error["options"],
        )

    target_result = _resolve_target_block(
        blocks=flowgraph.blocks,
        connections=flowgraph.connections,
        source_block_id=source_block_id,
        target_ref=target_ref,
        dst_block=dst_block,
    )
    if target_result.get("error"):
        return _clarification(
            ctx,
            operation_summary,
            str(target_result["message"]),
            list(target_result.get("options", [])),
        )
    target_block = target_result["block"]
    assert isinstance(target_block, Block)

    target_params = _parameters(target_block)
    target_rules_lookup = get_block_rules(target_block.block_type, catalog_root=ctx.agent.catalog_root)
    if not target_rules_lookup.ok or target_rules_lookup.rules is None:
        return _refusal(
            ctx,
            operation_summary,
            ErrorCode.CATALOG_LOAD_ERROR,
            target_rules_lookup.message or f"Unknown target block id: {target_block.block_type}",
        )
    target_rules = target_rules_lookup.rules
    target_sum_error = _target_sum_guard(ctx, target_block)
    if target_sum_error is not None:
        return _invalid_request(
            ctx,
            operation_summary,
            target_sum_error["message"],
            target_sum_error["options"],
        )

    existing_sources = _existing_sources_to_target(
        blocks=flowgraph.blocks,
        connections=flowgraph.connections,
        source_block_id=source_block_id,
        target_name=target_block.instance_name,
    )
    inheritance_error = _inheritance_ambiguity(
        source_rules=source_rules_lookup.rules,
        existing_sources=existing_sources,
        insert_params=insert_params,
        operation_args=operation_args,
    )
    if inheritance_error is not None:
        return _clarification(
            ctx,
            operation_summary,
            inheritance_error["message"],
            inheritance_error["options"],
        )
    source_params = _build_source_params(
        source_rules=source_rules_lookup.rules,
        source_block_id=source_block_id,
        target_params=target_params,
        existing_sources=existing_sources,
        frequency=frequency,
        insert_params=insert_params,
        operation_args=operation_args,
    )
    source_port_result = _single_stream_output_port(
        source_rules_lookup.rules,
        source_params,
    )
    if source_port_result.get("error"):
        return _refusal(
            ctx,
            operation_summary,
            ErrorCode.INVALID_REQUEST,
            str(source_port_result["message"]),
        )
    source_port = source_port_result["port"]

    target_port_result = _target_input_port_and_update(
        target_block=target_block,
        target_rules=target_rules,
        target_params=target_params,
        connections=flowgraph.connections,
        requested_dst_port=dst_port,
        allow_increase=operation_args.get("allow_increase_num_inputs") is not False,
    )
    if target_port_result.get("error"):
        return _clarification(
            ctx,
            operation_summary,
            str(target_port_result["message"]),
            list(target_port_result.get("options", [])),
        )

    new_instance_name = _new_instance_name(
        blocks=flowgraph.blocks,
        source_block_id=source_block_id,
        requested=instance_name,
    )
    operations: list[dict[str, Any]] = []
    update_op = target_port_result.get("update_params")
    if isinstance(update_op, dict):
        operations.append(update_op)
    operations.append(
        {
            "op_type": "add_block",
            "block_type": source_block_id,
            "instance_name": new_instance_name,
            "parameters": source_params,
        }
    )
    operations.append(
        {
            "op_type": "add_connection",
            "src_block": new_instance_name,
            "src_port": source_port,
            "dst_block": target_block.instance_name,
            "dst_port": target_port_result["port"],
        }
    )
    if not ctx.dry_run:
        preview_error = ctx.agent._validate_change_graph_preview_token(
            preview_token=_text(operation_args.get("preview_token")),
            operation_summary=operation_summary,
            operations=operations,
        )
        if preview_error is not None:
            return _invalid_request(
                ctx,
                operation_summary,
                preview_error,
                [
                    "Run change_graph with dry_run=true for this edit first.",
                    "Commit with the preview_token returned by the matching preview.",
                ],
            )

    ctx.handlers.append("propose_edit(add_signal_source_to_sum)" if ctx.dry_run else "apply_edit(add_signal_source_to_sum)")
    tool_result = tx_tool(operations)
    if isinstance(tool_result, dict):
        preview_token = None
        if ctx.dry_run and tool_result.get("ok") is True:
            preview_token = ctx.agent._register_change_graph_preview(
                operation_summary=operation_summary,
                operations=operations,
            )
        assumptions = [
            f"target={target_block.instance_name}",
            f"source_block_id={source_block_id}",
            f"freq={_param_text(frequency)}",
            f"connect_to_input={target_port_result['port']}",
        ]
        if update_op is not None:
            assumptions.append(
                "increase_input_multiplicity="
                + str(update_op.get("params", {}))
            )
        tool_result = copy.deepcopy(tool_result)
        if preview_token is not None:
            tool_result["preview_token"] = preview_token
            tool_result["commit_hint"] = (
                "To commit this structural preview, call change_graph with "
                f"dry_run=false, state_revision={ctx.agent.session.state_revision}, "
                f"and args.preview_token={preview_token}."
            )
        tool_result["assumptions"] = assumptions
        tool_result["operation_goal"] = user_goal
    return ChangeGraphOperationResult(
        handled=True,
        operation_summary=operation_summary,
        tool_result=tool_result,
    )


def _terminal(
    ctx: ChangeGraphOperationContext,
    result: ToolResult,
    action: str,
) -> ToolResult:
    return ctx.agent._attach_wrapper_dispatch_telemetry(
        debug=ctx.debug,
        wrapper_name="change_graph",
        wrapper_action=action,
        internal_handlers=ctx.handlers or ["none"],
        started=ctx.started,
        before_revision=ctx.before_revision,
        before_dirty=ctx.before_dirty,
        result=result,
        validation_run=False,
        output_truncated=False,
    )


def _clarification(
    ctx: ChangeGraphOperationContext,
    operation_summary: str,
    message: str,
    options: list[str],
) -> ChangeGraphOperationResult:
    result = ctx.agent._payload_result(
        "change_graph",
        {
            "ok": False,
            "dry_run": ctx.dry_run,
            "operation_kind": ctx.resolved_operation_kind,
            "operation_summary": operation_summary,
            "error_type": "clarification_required",
            "message": message,
            "clarification_options": options,
            "state_revision": ctx.agent.session.state_revision,
        },
    )
    return ChangeGraphOperationResult(
        handled=True,
        operation_summary=operation_summary,
        terminal_result=_terminal(ctx, result, operation_summary),
    )


def _refusal(
    ctx: ChangeGraphOperationContext,
    operation_summary: str,
    error_type: str,
    message: str,
) -> ChangeGraphOperationResult:
    result = ctx.agent._payload_result(
        "change_graph",
        {
            "ok": False,
            "dry_run": ctx.dry_run,
            "operation_kind": ctx.resolved_operation_kind,
            "operation_summary": operation_summary,
            "error_type": error_type,
            "message": message,
            "state_revision": ctx.agent.session.state_revision,
        },
    )
    return ChangeGraphOperationResult(
        handled=True,
        operation_summary=operation_summary,
        terminal_result=_terminal(ctx, result, operation_summary),
    )


def _invalid_request(
    ctx: ChangeGraphOperationContext,
    operation_summary: str,
    message: str,
    options: list[str],
) -> ChangeGraphOperationResult:
    result = ctx.agent._payload_result(
        "change_graph",
        {
            "ok": False,
            "dry_run": ctx.dry_run,
            "operation_kind": ctx.resolved_operation_kind,
            "operation_summary": operation_summary,
            "error_type": ErrorCode.INVALID_REQUEST,
            "message": message,
            "clarification_options": options,
            "state_revision": ctx.agent.session.state_revision,
        },
    )
    return ChangeGraphOperationResult(
        handled=True,
        operation_summary=operation_summary,
        terminal_result=_terminal(ctx, result, operation_summary),
    )


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _parameters(block: Block) -> dict[str, Any]:
    params = block.params.get("parameters")
    return copy.deepcopy(params) if isinstance(params, dict) else {}


def _resolve_target_block(
    *,
    blocks: list[Block],
    connections: list[Connection],
    source_block_id: str,
    target_ref: dict[str, Any] | None,
    dst_block: str | None,
) -> dict[str, Any]:
    by_name = {block.instance_name: block for block in blocks}
    target_name = _text(dst_block)
    if target_name is None and isinstance(target_ref, dict):
        target_name = _text(
            target_ref.get("expected_instance_name")
            or target_ref.get("instance_name")
        )
    if target_name is not None:
        block = by_name.get(target_name)
        if block is None:
            return {
                "error": True,
                "message": f"Target block not found: {target_name}",
                "options": ["Inspect graph details and retry with an exact target_ref or dst_block."],
            }
        return {"block": block}

    source_names = {
        block.instance_name
        for block in blocks
        if block.block_type == source_block_id
    }
    destination_counts: dict[str, int] = {}
    for connection in connections:
        if connection.src_block in source_names and isinstance(connection.dst_port, int):
            destination_counts[connection.dst_block] = destination_counts.get(connection.dst_block, 0) + 1
    if not destination_counts:
        return {
            "error": True,
            "message": (
                "No existing destination was found for that source block type. "
                "Provide args.dst_block or a target_ref from inspect_graph."
            ),
            "options": ["Inspect the intended destination block and retry with dst_block."],
        }
    ordered = sorted(destination_counts.items(), key=lambda item: (-item[1], item[0]))
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        return {
            "error": True,
            "message": "Multiple possible destinations were found for that source block type.",
            "options": [f"{name} ({count} existing source edge(s))" for name, count in ordered[:5]],
        }
    block = by_name.get(ordered[0][0])
    if block is None:
        return {
            "error": True,
            "message": f"Inferred destination no longer exists: {ordered[0][0]}",
            "options": ["Inspect graph details and retry."],
        }
    return {"block": block}


def _existing_sources_to_target(
    *,
    blocks: list[Block],
    connections: list[Connection],
    source_block_id: str,
    target_name: str,
) -> list[Block]:
    by_name = {block.instance_name: block for block in blocks}
    sources: list[Block] = []
    for connection in connections:
        if connection.dst_block != target_name:
            continue
        source = by_name.get(connection.src_block)
        if source is not None and source.block_type == source_block_id:
            sources.append(source)
    return sources


def _source_block_guard(
    *,
    ctx: ChangeGraphOperationContext,
    source_block_id: str,
    source_rules: BlockRules,
    blocks: list[Block],
) -> dict[str, Any] | None:
    """Validate that args.block_id names the source to add, not the destination."""

    has_frequency_param = "freq" in source_rules.parameters
    has_stream_input = any(port.domain == "stream" for port in source_rules.inputs)
    if has_frequency_param and not has_stream_input:
        return None
    candidates = _graph_source_block_candidates(ctx, blocks)
    option_text = (
        "Candidate source block ids in this graph: " + ", ".join(candidates) + "."
        if candidates
        else "Use search_blocks to find a source block id with a frequency parameter."
    )
    return {
        "message": (
            "add_signal_source_to_sum args.block_id must be the source block type "
            "to add, not the destination/summing block. "
            f"{source_block_id} is not a frequency-controlled source block for this operation."
        ),
        "options": [
            option_text,
            "Put the destination in args.dst_block or target_ref, not args.block_id.",
        ],
    }


def _target_sum_guard(
    ctx: ChangeGraphOperationContext,
    target_block: Block,
) -> dict[str, Any] | None:
    """Validate that the destination has catalog-backed additive semantics."""

    catalog_payload = _describe_block_with_root(
        target_block.block_type,
        catalog_root=ctx.agent.catalog_root,
    )
    label = str(catalog_payload.get("label") or "")
    category_path = " ".join(str(item) for item in catalog_payload.get("category_path") or [])
    semantic_text = " ".join(
        part for part in (target_block.block_type, label, category_path) if part
    ).lower()
    additive_words = {"add", "adder", "sum", "summing", "addition"}
    tokens = {
        token.strip("_- /")
        for token in semantic_text.replace("/", " ").replace("-", " ").split()
        if token.strip("_- /")
    }
    if tokens & additive_words:
        return None
    return {
        "message": (
            "add_signal_source_to_sum needs a destination whose installed catalog "
            f"metadata identifies it as additive/summing. {target_block.instance_name} "
            f"({target_block.block_type}) is not additive enough for this macro."
        ),
        "options": [
            "Inspect graph details and retry with a summing/additive destination.",
            "Use a more specific supported operation if this is not a summing edit.",
        ],
    }


def _graph_source_block_candidates(
    ctx: ChangeGraphOperationContext,
    blocks: list[Block],
) -> list[str]:
    candidates: set[str] = set()
    for block in blocks:
        rules_lookup = get_block_rules(block.block_type, catalog_root=ctx.agent.catalog_root)
        if not rules_lookup.ok or rules_lookup.rules is None:
            continue
        rules = rules_lookup.rules
        if "freq" not in rules.parameters:
            continue
        if any(port.domain == "stream" for port in rules.inputs):
            continue
        candidates.add(block.block_type)
    return sorted(candidates)


def _inheritance_ambiguity(
    *,
    source_rules: BlockRules,
    existing_sources: list[Block],
    insert_params: dict[str, Any] | None,
    operation_args: dict[str, Any],
) -> dict[str, Any] | None:
    if not existing_sources:
        return None
    aliases = {
        "samp_rate": ("samp_rate", "sample_rate"),
        "waveform": ("waveform",),
        "amp": ("amp", "amplitude"),
        "type": ("type",),
        "offset": ("offset",),
        "phase": ("phase",),
    }
    for key, arg_names in aliases.items():
        if key not in source_rules.parameters:
            continue
        if _explicit_source_param_supplied(key, arg_names, insert_params, operation_args):
            continue
        values: list[tuple[str, Any]] = []
        for block in existing_sources:
            block_params = _parameters(block)
            if key in block_params:
                values.append((block.instance_name, block_params[key]))
        if len({_stable_value(value) for _name, value in values}) <= 1:
            continue
        return {
            "message": (
                f"Existing {source_rules.block_id} sources disagree on {key}; "
                f"provide args.{arg_names[0]} explicitly before adding another source."
            ),
            "options": [
                f"{instance_name}.{key}={_param_text(value)}"
                for instance_name, value in values[:5]
            ],
        }
    return None


def _explicit_source_param_supplied(
    key: str,
    arg_names: tuple[str, ...],
    insert_params: dict[str, Any] | None,
    operation_args: dict[str, Any],
) -> bool:
    if isinstance(insert_params, dict) and key in insert_params:
        return True
    for arg_name in arg_names:
        value = operation_args.get(arg_name)
        if value is not None and not _is_inherit(value):
            return True
    return False


def _stable_value(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _build_source_params(
    *,
    source_rules: BlockRules,
    source_block_id: str,
    target_params: dict[str, Any],
    existing_sources: list[Block],
    frequency: Any,
    insert_params: dict[str, Any] | None,
    operation_args: dict[str, Any],
) -> dict[str, Any]:
    allowed = set(source_rules.parameters)
    params: dict[str, Any] = {}
    if isinstance(insert_params, dict):
        params.update(copy.deepcopy(insert_params))
    params["freq"] = _param_text(frequency)

    def common(name: str) -> Any:
        values: list[Any] = []
        for block in existing_sources:
            block_params = _parameters(block)
            if name in block_params:
                values.append(block_params[name])
        if values and all(value == values[0] for value in values):
            return values[0]
        return None

    direct_mapping = {
        "samp_rate": operation_args.get("samp_rate", operation_args.get("sample_rate")),
        "waveform": operation_args.get("waveform"),
        "amp": operation_args.get("amp", operation_args.get("amplitude")),
        "offset": operation_args.get("offset"),
        "phase": operation_args.get("phase"),
    }
    for key, value in direct_mapping.items():
        if key not in allowed or key in params:
            continue
        if _is_inherit(value) or value is None:
            inherited = common(key)
            if inherited is not None:
                params[key] = inherited
        else:
            params[key] = _param_text(value)

    if "type" in allowed and "type" not in params:
        inherited_type = common("type")
        if inherited_type is not None:
            params["type"] = inherited_type
        elif "type" in target_params:
            params["type"] = target_params["type"]
    return {key: value for key, value in params.items() if key in allowed}


def _is_inherit(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    return value.strip().lower() in {"inherit", "same", "existing", "reuse"}


def _param_text(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _single_stream_output_port(rules: BlockRules, parameters: dict[str, Any]) -> dict[str, Any]:
    outputs, _warnings = resolve_port_slots(
        block_rules=rules,
        parameters=parameters,
        direction="outputs",
    )
    stream_indexes = [
        index
        for index, port in enumerate(outputs)
        if port.domain == "stream"
    ]
    if len(stream_indexes) != 1:
        return {
            "error": True,
            "message": (
                f"Source block {rules.block_id} must resolve to exactly one stream output; "
                f"found {len(stream_indexes)}."
            ),
        }
    return {"port": stream_indexes[0]}


def _target_input_port_and_update(
    *,
    target_block: Block,
    target_rules: BlockRules,
    target_params: dict[str, Any],
    connections: list[Connection],
    requested_dst_port: int | str | None,
    allow_increase: bool,
) -> dict[str, Any]:
    if isinstance(requested_dst_port, str):
        try:
            requested_dst_port = int(requested_dst_port)
        except ValueError:
            return {
                "error": True,
                "message": "add_signal_source_to_sum supports stream input indexes only.",
                "options": ["Retry with integer dst_port or omit dst_port for automatic next input."],
            }
    inputs, _warnings = resolve_port_slots(
        block_rules=target_rules,
        parameters=target_params,
        direction="inputs",
    )
    stream_indexes = [
        index
        for index, port in enumerate(inputs)
        if port.domain == "stream"
    ]
    occupied = {
        connection.dst_port
        for connection in connections
        if connection.dst_block == target_block.instance_name
        and isinstance(connection.dst_port, int)
    }
    if isinstance(requested_dst_port, int):
        chosen_port = requested_dst_port
    else:
        chosen_port = 0
        while chosen_port in occupied:
            chosen_port += 1
    if chosen_port < 0:
        return {
            "error": True,
            "message": "Destination port must be non-negative.",
            "options": ["Retry with a non-negative integer dst_port."],
        }
    if chosen_port in occupied:
        return {
            "error": True,
            "message": f"Destination input is already occupied: {target_block.instance_name}:{chosen_port}",
            "options": ["Omit dst_port to use the next free input, or choose a free input."],
        }
    if chosen_port in stream_indexes:
        return {"port": chosen_port}

    control_param = _input_multiplicity_param(target_rules)
    if control_param is None:
        return {
            "error": True,
            "message": (
                f"Target block {target_block.instance_name} does not expose a simple "
                "catalog-backed stream input multiplicity parameter."
            ),
            "options": ["Choose an existing free input or inspect another destination block."],
        }
    if not allow_increase:
        return {
            "error": True,
            "message": (
                f"Target needs {control_param} increased before input {chosen_port} exists."
            ),
            "options": ["Retry with allow_increase_num_inputs=true or choose an existing free input."],
        }
    old_value = target_params.get(control_param)
    new_value = str(chosen_port + 1)
    return {
        "port": chosen_port,
        "update_params": {
            "op_type": "update_params",
            "instance_name": target_block.instance_name,
            "params": {control_param: new_value},
            "expected_params": {control_param: old_value} if old_value is not None else {},
        },
    }


def _input_multiplicity_param(rules: BlockRules) -> str | None:
    for port in rules.inputs:
        multiplicity = port.multiplicity
        if not isinstance(multiplicity, str):
            continue
        stripped = multiplicity.strip()
        if not (stripped.startswith("${") and stripped.endswith("}")):
            continue
        expression = stripped[2:-1].strip()
        if expression in rules.parameters:
            return expression
    return None


def _new_instance_name(
    *,
    blocks: list[Block],
    source_block_id: str,
    requested: str | None,
) -> str:
    existing = {block.instance_name for block in blocks}
    if isinstance(requested, str) and requested.strip() and requested.strip() not in existing:
        return requested.strip()
    base = source_block_id
    index = 0
    while f"{base}_{index}" in existing:
        index += 1
    return f"{base}_{index}"
