"""Read-only helper to suggest catalog-backed blocks for connection insertion.

No graph mutation. No hardcoded recipes. No blacklists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from grc_agent.catalog.describe import describe_block
from grc_agent.catalog.schema import BlockDescription, NormalizedParameter, NormalizedPort
from grc_agent.models import Connection
from grc_agent.session_ops import parse_connection_id


@dataclass(frozen=True)
class PortSpec:
    block: str
    port: int | str
    dtype: str | None = None
    vlen: int | str | None = None
    domain: str | None = None


@dataclass(frozen=True)
class InsertionCandidate:
    block_type: str
    reason: str
    required_params: dict[str, Any]
    confidence: str
    insert_tool_args: dict[str, Any] | None = None


@dataclass(frozen=True)
class InsertionSuggestions:
    ok: bool
    connection_id: str
    source: PortSpec | None = None
    destination: PortSpec | None = None
    candidates: list[InsertionCandidate] = None
    error_type: str | None = None
    message: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "candidates", self.candidates or [])


def _port_spec_from_connection(
    conn: Connection,
    port_attr: str,
    catalog_blocks: dict[str, BlockDescription],
) -> PortSpec:
    block_name = getattr(conn, f"{port_attr}_block")
    port_name = getattr(conn, f"{port_attr}_port")
    desc = catalog_blocks.get(block_name)
    if desc is None:
        return PortSpec(block=block_name, port=port_name)
    ports = desc.outputs if port_attr == "src" else desc.inputs
    port = _find_port(ports, port_name)
    if port is None:
        return PortSpec(block=block_name, port=port_name)
    return PortSpec(
        block=block_name,
        port=port_name,
        dtype=port.dtype,
        vlen=port.vlen,
        domain=port.domain,
    )


def _find_port(ports: list[NormalizedPort], port_id: int | str) -> NormalizedPort | None:
    for p in ports:
        if p.id == str(port_id):
            return p
        if isinstance(port_id, int) and p.label and str(port_id) in p.label:
            return p
    return None


def _is_template(value: str | None) -> bool:
    return value is not None and "${" in value


def _port_compatible(port: NormalizedPort, spec: PortSpec) -> bool:
    if port.domain and spec.domain and port.domain != spec.domain:
        return False
    if not _is_template(port.dtype) and spec.dtype and port.dtype != spec.dtype:
        return False
    if not _is_template(str(port.vlen)) and spec.vlen is not None:
        if str(port.vlen) != str(spec.vlen):
            return False
    return True


def _has_safe_defaults(desc: BlockDescription) -> tuple[bool, dict[str, Any], list[str]]:
    required_params: dict[str, Any] = {}
    missing: list[str] = []
    for param in desc.parameters:
        if param.default is not None:
            required_params[param.id] = param.default
        elif param.options:
            required_params[param.id] = param.options[0]
        else:
            if param.hide not in ("all", "part"):
                missing.append(param.id)
    return (not missing, required_params, missing)


def _is_core_block(desc: BlockDescription) -> bool:
    path = "/".join(desc.category_path).lower()
    return "core" in path or "general" in path or "filters" in path or "math" in path


def _is_hardware_or_external(desc: BlockDescription) -> bool:
    path = "/".join(desc.category_path).lower()
    flags = [f.lower() for f in desc.flags]
    return (
        "hardware" in path
        or "uhd" in path
        or "usrp" in path
        or "rfnoc" in path
        or "oot" in path
        or "external" in path
        or "python_module" in flags
    )


def suggest_insertions(
    session,
    connection_id_str: str,
    k: int = 5,
) -> InsertionSuggestions:
    """Suggest catalog-backed blocks that can be inserted into an existing connection.

    Does not mutate the graph.
    """
    if session.flowgraph is None:
        return InsertionSuggestions(
            ok=False,
            connection_id=connection_id_str,
            error_type="NO_GRAPH_LOADED",
            message="No flowgraph loaded in session.",
        )

    parsed = parse_connection_id(connection_id_str)
    if parsed is None:
        return InsertionSuggestions(
            ok=False,
            connection_id=connection_id_str,
            error_type="INVALID_CONNECTION_ID",
            message="connection_id must be in form 'src_block:src_port->dst_block:dst_port'.",
        )

    src_block, src_port, dst_block, dst_port = parsed

    target = None
    for conn in session.flowgraph.connections:
        if (
            conn.src_block == src_block
            and conn.src_port == src_port
            and conn.dst_block == dst_block
            and conn.dst_port == dst_port
        ):
            target = conn
            break

    if target is None:
        return InsertionSuggestions(
            ok=False,
            connection_id=connection_id_str,
            error_type="CONNECTION_NOT_FOUND",
            message=f"Connection not found: {connection_id_str}",
        )

    # Resolve endpoint block types from session
    block_types: dict[str, str] = {}
    for block in session.flowgraph.blocks:
        block_types[block.instance_name] = block.block_type

    src_type = block_types.get(src_block)
    dst_type = block_types.get(dst_block)

    # Fetch catalog descriptions for endpoints
    catalog_blocks: dict[str, BlockDescription] = {}
    if src_type:
        try:
            payload = describe_block(src_type)
            if payload.get("ok"):
                catalog_blocks[src_block] = _payload_to_desc(payload)
        except Exception:
            pass
    if dst_type:
        try:
            payload = describe_block(dst_type)
            if payload.get("ok"):
                catalog_blocks[dst_block] = _payload_to_desc(payload)
        except Exception:
            pass

    source_spec = _port_spec_from_connection(target, "src", catalog_blocks)
    dest_spec = _port_spec_from_connection(target, "dst", catalog_blocks)

    # Domain check — insertion only supported for stream connections
    if source_spec.domain == "message" or dest_spec.domain == "message":
        return InsertionSuggestions(
            ok=False,
            connection_id=connection_id_str,
            error_type="MESSAGE_CONNECTION_NOT_SUPPORTED",
            message="Insertion suggestions are not supported for message connections.",
        )

    target_domain = source_spec.domain or dest_spec.domain or "stream"
    target_dtype = source_spec.dtype or dest_spec.dtype
    target_vlen = source_spec.vlen or dest_spec.vlen

    candidates = _find_candidates(
        target_domain=target_domain,
        target_dtype=target_dtype,
        target_vlen=target_vlen,
        connection_id=connection_id_str,
        existing_names={b.instance_name for b in session.flowgraph.blocks} if session.flowgraph else set(),
        k=k * 3,  # over-fetch for ranking
    )

    ranked = _rank_candidates(candidates)[:k]

    return InsertionSuggestions(
        ok=True,
        connection_id=connection_id_str,
        source=source_spec,
        destination=dest_spec,
        candidates=ranked,
    )


def _payload_to_desc(payload: dict[str, Any]) -> BlockDescription:
    """Build a minimal BlockDescription from describe_block payload."""
    return BlockDescription(
        block_id=payload["block_id"],
        label=payload.get("label", payload["block_id"]),
        category_path=payload.get("category_path", []),
        flags=payload.get("flags", []),
        loaded_from=payload.get("loaded_from", ""),
        parameters=[
            NormalizedParameter(
                id=p["id"],
                label=p.get("label"),
                dtype=p.get("dtype"),
                default=p.get("default"),
                category=p.get("category"),
                hide=p.get("hide"),
                options=p.get("options", []),
                option_labels=p.get("option_labels", []),
                option_attributes=p.get("option_attributes", {}),
                base_key=p.get("base_key"),
            )
            for p in payload.get("parameters", [])
        ],
        inputs=[
            NormalizedPort(
                label=p.get("label"),
                domain=p.get("domain"),
                id=p.get("id"),
                dtype=p.get("dtype"),
                vlen=p.get("vlen"),
                multiplicity=p.get("multiplicity"),
                optional=p.get("optional"),
                hide=p.get("hide"),
                color=p.get("color"),
            )
            for p in payload.get("inputs", [])
        ],
        outputs=[
            NormalizedPort(
                label=p.get("label"),
                domain=p.get("domain"),
                id=p.get("id"),
                dtype=p.get("dtype"),
                vlen=p.get("vlen"),
                multiplicity=p.get("multiplicity"),
                optional=p.get("optional"),
                hide=p.get("hide"),
                color=p.get("color"),
            )
            for p in payload.get("outputs", [])
        ],
        asserts=payload.get("asserts", []),
        documentation=payload.get("documentation"),
        doc_url=payload.get("doc_url"),
        warnings=payload.get("warnings", []),
        signature=payload.get("signature", ""),
    )


def _find_candidates(
    target_domain: str,
    target_dtype: str | None,
    target_vlen: int | str | None,
    connection_id: str,
    existing_names: set[str],
    k: int,
) -> list[InsertionCandidate]:
    """Search catalog for blocks matching insertion criteria."""
    from grc_agent.catalog.loaders import get_catalog_snapshot

    snapshot = get_catalog_snapshot()
    candidates: list[InsertionCandidate] = []

    for block_id in sorted(snapshot.blocks.keys()):
        try:
            payload = describe_block(block_id)
            if not payload.get("ok"):
                continue
        except Exception:
            continue

        desc = _payload_to_desc(payload)

        # Must have at least one input and one output
        if not desc.inputs or not desc.outputs:
            continue

        # Exclude hardware/external blocks generically
        if _is_hardware_or_external(desc):
            continue

        # For insertion, prefer one-input / one-output blocks
        # But allow multi-port if they have matching domains
        for inp in desc.inputs:
            for out in desc.outputs:
                if inp.domain != target_domain or out.domain != target_domain:
                    continue
                if not _port_compatible_for_insertion(
                    inp, out, target_dtype, target_vlen
                ):
                    continue

                has_defaults, required_params, missing = _has_safe_defaults(desc)
                reason = _build_reason(
                    desc, inp, out, has_defaults, missing
                )
                confidence = _confidence(
                    desc, inp, out, has_defaults, missing
                )
                instance_name = _generate_instance_name(block_id, existing_names)
                existing_names.add(instance_name)
                insert_tool_args: dict[str, Any] = {
                    "connection_id": connection_id,
                    "block_type": block_id,
                    "instance_name": instance_name,
                    "params": dict(required_params),
                }
                candidates.append(
                    InsertionCandidate(
                        block_type=block_id,
                        reason=reason,
                        required_params=required_params,
                        confidence=confidence,
                        insert_tool_args=insert_tool_args,
                    )
                )
                break  # one candidate per block is enough
            break

    return candidates


def _generate_instance_name(block_type: str, existing_names: set[str]) -> str:
    """Generate a unique instance name from the block type."""
    suffix = block_type.split("_", 1)[-1] if "_" in block_type else block_type
    base = "_".join(suffix.split("_")).rstrip("_") or "block"
    candidate = base
    if candidate not in existing_names:
        return candidate
    for i in range(1, 100):
        candidate = f"{base}_{i}"
        if candidate not in existing_names:
            return candidate
    return f"{base}_999"


def _port_compatible_for_insertion(
    inp: NormalizedPort,
    out: NormalizedPort,
    target_dtype: str | None,
    target_vlen: int | str | None,
) -> bool:
    # Both ports must match target dtype/vlen (or be templates)
    inp_ok = _port_dtype_vlen_ok(inp, target_dtype, target_vlen)
    out_ok = _port_dtype_vlen_ok(out, target_dtype, target_vlen)
    return inp_ok and out_ok


def _port_dtype_vlen_ok(
    port: NormalizedPort,
    target_dtype: str | None,
    target_vlen: int | str | None,
) -> bool:
    if target_dtype and not _is_template(port.dtype):
        if port.dtype != target_dtype:
            return False
    if target_vlen is not None and not _is_template(str(port.vlen)):
        if str(port.vlen) != str(target_vlen):
            return False
    return True


def _build_reason(
    desc: BlockDescription,
    inp: NormalizedPort,
    out: NormalizedPort,
    has_defaults: bool,
    missing: list[str],
) -> str:
    parts: list[str] = []
    parts.append(f"block '{desc.block_id}' has {len(desc.inputs)} input(s) and {len(desc.outputs)} output(s)")
    if inp.domain:
        parts.append(f"input domain={inp.domain}")
    if out.domain:
        parts.append(f"output domain={out.domain}")
    if inp.dtype and not _is_template(inp.dtype):
        parts.append(f"input dtype={inp.dtype}")
    if out.dtype and not _is_template(out.dtype):
        parts.append(f"output dtype={out.dtype}")
    if has_defaults:
        parts.append("all required params have catalog defaults")
    elif missing:
        parts.append(f"missing required params: {', '.join(missing)}")
    return "; ".join(parts)


def _confidence(
    desc: BlockDescription,
    inp: NormalizedPort,
    out: NormalizedPort,
    has_defaults: bool,
    missing: list[str],
) -> str:
    score = 0
    if len(desc.inputs) == 1 and len(desc.outputs) == 1:
        score += 2
    if has_defaults:
        score += 2
    if _is_core_block(desc):
        score += 1
    if not missing:
        score += 1
    if score >= 5:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def _rank_candidates(candidates: list[InsertionCandidate]) -> list[InsertionCandidate]:
    """Rank by confidence then block_type for determinism."""
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        candidates,
        key=lambda c: (confidence_order.get(c.confidence, 3), c.block_type),
    )
