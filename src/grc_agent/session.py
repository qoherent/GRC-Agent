"""Read-oriented session inspection, GNU validation, and insertion logic.

Consolidated from session/__init__.py + session/gnu_validation.py + session/insertion.py.
"""

from __future__ import annotations

import copy
import functools
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from grc_agent._payload import ErrorCode, build_error_payload
from grc_agent.catalog.loaders import describe_block, get_catalog_snapshot
from grc_agent.catalog.schema import BlockDescription, NormalizedParameter, NormalizedPort
from grc_agent.flowgraph_session import (
    DEFAULT_CONTEXT_MAX_NODES,
    DEFAULT_SUMMARY_BLOCK_LIMIT,
    FlowgraphSession,
)
from grc_agent._payload import Block, Connection
from grc_agent.runtime.clarification import ClarificationOption, ClarificationRequest
from grc_agent.session_ops import parse_connection_id

logger = logging.getLogger(__name__)

__all__ = [
    "get_grc_context",
    "load_grc",
    "summarize_graph",
    "suggest_insertions",
    "auto_insert_block",
    "validate_raw_flowgraph",
    "validate_graph",
]


# =========================================================================
# inspect / context / load / summary
# =========================================================================


def require_loaded_session(session: FlowgraphSession) -> FlowgraphSession:
    if session.flowgraph is None:
        raise ValueError("No flowgraph loaded.")
    return session


def get_grc_context(
    session: FlowgraphSession,
    node_id: str,
    *,
    hops: int = 1,
    max_nodes: int = DEFAULT_CONTEXT_MAX_NODES,
) -> dict[str, Any]:
    try:
        require_loaded_session(session)
        return session.context_payload(node_id, hops=hops, max_nodes=max_nodes)
    except KeyError:
        return build_error_payload(
            error_type=ErrorCode.BLOCK_NOT_FOUND,
            message=f"Unknown session node: {node_id}",
            details={"node_id": node_id},
        )
    except ValueError as exc:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST, message=str(exc)
        )


def load_grc(file_path: str | Path) -> FlowgraphSession | dict[str, Any]:
    session = FlowgraphSession()
    try:
        session.load(file_path)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return build_error_payload(
            error_type=ErrorCode.FILE_LOAD_ERROR,
            message=str(exc),
        )
    except (ValueError, yaml.YAMLError) as exc:
        return build_error_payload(
            error_type=ErrorCode.INVALID_GRC,
            message=str(exc),
        )
    return session


def summarize_graph(
    session: FlowgraphSession,
    *,
    max_blocks: int = DEFAULT_SUMMARY_BLOCK_LIMIT,
) -> dict[str, Any]:
    try:
        require_loaded_session(session)
        return session.summary_payload(max_blocks=max_blocks)
    except ValueError as exc:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST, message=str(exc)
        )


# =========================================================================
# GNU validation
# =========================================================================

_GRC_BLOCKS_PATHS: list[str] = [
    "/usr/share/gnuradio/grc/blocks",
    "/usr/local/share/gnuradio/grc/blocks",
]


@functools.lru_cache(maxsize=1)
def _get_gnu_platform() -> Any:
    """Return a lazily-constructed ``Platform`` instance backed by a cached library."""
    os.environ.setdefault("GRC_BLOCKS_PATH", ":".join(_GRC_BLOCKS_PATHS))

    try:
        from gnuradio.grc.core.platform import Platform
    except Exception as exc:
        logger.warning("GNU Radio GRC Platform not importable: %s", exc)
        raise

    try:
        platform = Platform(version="3.10.9.2", version_parts=["3", "10", "9", "2"])
        platform.build_library()
    except Exception as exc:
        logger.warning("GNU Radio Platform library build failed: %s", exc)
        raise

    logger.info(
        "GNU Radio Platform ready: %d block classes",
        len(getattr(platform, "block_classes", {})),
    )
    return platform


def _ensure_platform() -> Any:
    """Return the cached ``Platform`` or ``None`` if unavailable."""
    try:
        return _get_gnu_platform()
    except Exception as exc:
        logger.warning("GNU Radio Platform unavailable: %s", exc)
        return None


def validate_raw_flowgraph(raw_data: dict[str, Any]) -> dict[str, Any]:
    """Validate raw `.grc` data through GNU Radio's headless FlowGraph API."""
    platform = _ensure_platform()
    if platform is None:
        return {
            "ok": False,
            "available": False,
            "valid": None,
            "errors": ["GNU Radio Platform is not available."],
        }
    try:
        fg = platform.make_flow_graph()
        fg.import_data(copy.deepcopy(raw_data))
        fg.validate()
        errors = [str(message) for message in fg.get_error_messages()]
        return {
            "ok": True,
            "available": True,
            "valid": bool(fg.is_valid()),
            "errors": errors,
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": True,
            "valid": False,
            "errors": [str(exc)],
        }





# =========================================================================
# Insertion candidates
# =========================================================================

_CLARIFICATION_THRESHOLD = 2
_MAX_MCQ_OPTIONS = 3

_CATALOG_FREQUENCY: dict[str, int] | None = None


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
    candidates: list[InsertionCandidate] | None = None
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


def _has_safe_defaults(
    desc: BlockDescription, resolved_dtype: str | None = None
) -> tuple[bool, dict[str, Any], list[str]]:
    from grc_agent.runtime.block_semantics import evaluated_param_hides

    required_params: dict[str, Any] = {}
    missing: list[str] = []
    # Use the GRC-core-evaluated 'hide' so any expression that references
    # other params is resolved consistently with the rest of the runtime.
    # Falls back to static 'none' if the platform evaluator is unavailable.
    evaluated_hides = evaluated_param_hides(desc.block_type, {})
    for param in desc.parameters:
        if param.default is not None:
            required_params[param.id] = param.default
        elif param.options:
            if param.id == "type" and resolved_dtype:
                if resolved_dtype in param.options:
                    required_params[param.id] = resolved_dtype
                else:
                    required_params[param.id] = param.options[0]
            else:
                required_params[param.id] = param.options[0]
        else:
            hide = evaluated_hides.get(param.id, str(param.hide))
            if hide not in ("all", "part"):
                missing.append(param.id)
    return (not missing, required_params, missing)


# ---------------------------------------------------------------------------
# Block classification — one uniform rule per category, no substring matching.
# Categories are matched by exact path component; flags by exact membership.
# Add new categories / flags here, not inline.
# ---------------------------------------------------------------------------
_CORE_CATEGORIES: frozenset[str] = frozenset({"core", "general", "filters", "math"})
_HARDWARE_OR_EXTERNAL_CATEGORIES: frozenset[str] = frozenset(
    {"hardware", "uhd", "usrp", "rfnoc", "oot", "external"}
)
_HARDWARE_OR_EXTERNAL_FLAGS: frozenset[str] = frozenset({"python_module"})


def _is_core_block(desc: BlockDescription) -> bool:
    return bool({p.lower() for p in desc.category_path} & _CORE_CATEGORIES)


def _is_hardware_or_external(desc: BlockDescription) -> bool:
    return bool(
        {p.lower() for p in desc.category_path} & _HARDWARE_OR_EXTERNAL_CATEGORIES
        or {f.lower() for f in desc.flags} & _HARDWARE_OR_EXTERNAL_FLAGS
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

    block_types: dict[str, str] = {}
    block_params: dict[str, dict[str, str]] = {}
    for block in session.flowgraph.blocks:
        block_types[block.instance_name] = block.block_type
        if isinstance(block.params, dict):
            params = block.params.get("parameters", {})
            if isinstance(params, dict):
                block_params[block.instance_name] = params

    src_type = block_types.get(src_block)
    dst_type = block_types.get(dst_block)

    catalog_blocks: dict[str, BlockDescription] = {}
    if src_type:
        try:
            payload = describe_block(src_type)
            if payload.get("ok"):
                catalog_blocks[src_block] = _payload_to_desc(payload)
        except Exception as exc:
            logger.warning("insertion_suggestions describe_block failed for src=%s: %s", src_type, exc)
    if dst_type:
        try:
            payload = describe_block(dst_type)
            if payload.get("ok"):
                catalog_blocks[dst_block] = _payload_to_desc(payload)
        except Exception as exc:
            logger.warning("insertion_suggestions describe_block failed for dst=%s: %s", dst_type, exc)

    source_spec = _port_spec_from_connection(target, "src", catalog_blocks)
    dest_spec = _port_spec_from_connection(target, "dst", catalog_blocks)

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

    if target_dtype is None:
        for endpoint in (src_block, dst_block):
            params = block_params.get(endpoint, {})
            if "type" in params and params["type"]:
                target_dtype = params["type"]
                break

    candidates = _find_candidates(
        target_domain=target_domain,
        target_dtype=target_dtype,
        target_vlen=target_vlen,
        connection_id=connection_id_str,
        existing_names={b.instance_name for b in session.flowgraph.blocks} if session.flowgraph else set(),
        k=max(k * 3, 500),
        resolved_dtype=target_dtype,
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
    resolved_dtype: str | None = None,
) -> list[InsertionCandidate]:
    """Search catalog for blocks matching insertion criteria."""
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

        if not desc.inputs or not desc.outputs:
            continue

        if _is_hardware_or_external(desc):
            continue

        for inp in desc.inputs:
            for out in desc.outputs:
                if inp.domain != target_domain or out.domain != target_domain:
                    continue
                if not _port_compatible_for_insertion(
                    inp, out, target_dtype, target_vlen
                ):
                    continue

                has_defaults, required_params, missing = _has_safe_defaults(desc, resolved_dtype)
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
                break
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


# =========================================================================
# Candidate validation helpers
# =========================================================================


def _try_candidate(
    session,
    conn_id: str,
    candidate: InsertionCandidate,
    catalog_root: str | None,
) -> dict[str, Any]:
    """Try one candidate via apply_edit. Live session is only mutated on success."""
    from grc_agent.transaction import apply_edit

    if candidate.insert_tool_args is None:
        return {
            "ok": False,
            "message": "Candidate missing insert_tool_args.",
            "error_type": "MISSING_INSERT_TOOL_ARGS",
        }
    transaction = {
        "op_type": "insert_block_on_connection",
        "connection_id": conn_id,
        "block_type": candidate.block_type,
        "instance_name": candidate.insert_tool_args["instance_name"],
        "params": candidate.insert_tool_args.get("params", {}),
    }
    return apply_edit(session, transaction, catalog_root)


def _try_candidate_on_clone(
    session,
    conn_id: str,
    candidate: InsertionCandidate,
    catalog_root: str | None,
) -> dict[str, Any]:
    """Validate one candidate on a cloned session. Does NOT mutate live session."""
    from grc_agent.transaction import clone_session

    clone = clone_session(session)
    return _try_candidate(clone, conn_id, candidate, catalog_root)


def _build_attempted_entry(
    score: int,
    conn_id: str,
    candidate: InsertionCandidate,
    result: dict[str, Any],
    intent: dict[str, Any],
) -> dict[str, Any]:
    """Build a diagnostic entry for one candidate attempt."""
    return {
        "connection_id": conn_id,
        "block_type": candidate.block_type,
        "instance_name": candidate.insert_tool_args.get("instance_name") if candidate.insert_tool_args else None,
        "score": score,
        "goal_fit": _candidate_matches_family(candidate, intent.get("family_tokens", [])),
        "ok": result.get("ok", False),
        "error_type": result.get("error_type"),
        "message": result.get("message"),
    }


def _safe_rejection(attempted: list[dict[str, Any]], intent: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "message": f"All {len(attempted)} insertion candidates failed validation.",
        "committed": None,
        "attempted": attempted,
        "error_type": "AUTO_INSERT_ALL_CANDIDATES_FAILED",
        "attempt_count": len(attempted),
        "goal_mode": intent.get("mode"),
    }


def _build_clarification_payload(
    validated: list[tuple[int, str, InsertionCandidate, dict[str, Any]]],
    attempted: list[dict[str, Any]],
    goal: str,
    intent: dict[str, Any],
) -> dict[str, Any]:
    """Build a clarification payload from validated candidates. No live mutation."""
    options: list[ClarificationOption] = []
    for index, (score, conn_id, candidate, _entry) in enumerate(validated[:_MAX_MCQ_OPTIONS]):
        label = chr(ord("A") + index)
        options.append(
            ClarificationOption(
                label=label,
                title=f"Insert '{candidate.block_type}' into {conn_id}",
                description=f"{candidate.reason} (confidence: {candidate.confidence})",
                tool_name="insert_block_on_connection",
                tool_args=candidate.insert_tool_args or {},
                metadata={"score": score, "goal_mode": intent.get("mode"), "connection_id": conn_id},
            )
        )

    req = ClarificationRequest(
        kind="choose_insert_candidate",
        question="Multiple compatible blocks were found for the requested insertion goal.",
        options=options,
        state_revision=session.state_revision,
    )

    payload = req.to_dict()
    if len(validated) > _MAX_MCQ_OPTIONS:
        payload["options_truncated"] = f"... [TRUNCATED by chat-history compactor: was {len(validated)} items, kept {_MAX_MCQ_OPTIONS}]"
    payload["ok"] = False
    payload["attempted"] = attempted
    payload["attempt_count"] = len(attempted)
    payload["goal_mode"] = intent.get("mode")
    return payload


def _stream_connections(session) -> list[str]:
    """Return connection_id strings for all stream connections (integer ports only)."""
    conn_ids: list[str] = []
    if session.flowgraph is None:
        return conn_ids
    for conn in session.flowgraph.connections:
        if isinstance(conn.src_port, int) and isinstance(conn.dst_port, int):
            conn_ids.append(
                f"{conn.src_block}:{conn.src_port}->{conn.dst_block}:{conn.dst_port}"
            )
    return conn_ids


def _error(error_type: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "message": message,
        "error_type": error_type,
        "committed": None,
        "attempted": [],
        "attempt_count": 0,
    }


# =========================================================================
# Goal classification
# =========================================================================


def _catalog_token_frequency() -> dict[str, int]:
    """Return token -> count in all block_ids (cached)."""
    global _CATALOG_FREQUENCY
    if _CATALOG_FREQUENCY is not None:
        return _CATALOG_FREQUENCY
    snapshot = get_catalog_snapshot()
    freq: dict[str, int] = {}
    for block_id in snapshot.blocks.keys():
        tokens = re.split(r"[^a-z]", block_id.lower())
        tokens = [t for t in tokens if len(t) > 1]
        for t in set(tokens):
            freq[t] = freq.get(t, 0) + 1
    _CATALOG_FREQUENCY = freq
    return freq


def _classify_goal(goal: str, preferred_block_type: str | None) -> dict[str, Any]:
    """Classify user goal to determine insertion mode.

    Returns dict with:
        mode: "generic" | "explicit_family" | "preferred_type"
        family_tokens: list[str] — tokens identifying the desired block family
        min_score: int — minimum relevance score for explicit-family goals
    """
    if preferred_block_type and preferred_block_type.strip():
        return {
            "mode": "preferred_type",
            "family_tokens": _extract_tokens(preferred_block_type),
            "min_score": 0,
        }

    tokens = _extract_tokens(goal)
    freq = _catalog_token_frequency()
    family_tokens = [t for t in tokens if len(t) > 2 and freq.get(t, 0) > 0]

    if family_tokens:
        return {
            "mode": "explicit_family",
            "family_tokens": family_tokens,
            "min_score": 3,
        }

    return {"mode": "generic", "family_tokens": [], "min_score": 0}


def _extract_tokens(text: str) -> list[str]:
    """Extract normalized tokens from text, dropping any single-character noise."""
    from grc_agent.runtime.text_utils import tokenize_identifier

    return [t for t in tokenize_identifier(text) if len(t) > 1]


def _candidate_matches_family(candidate: InsertionCandidate, family_tokens: list[str]) -> bool:
    """Check whether a candidate matches the desired block family."""
    if not family_tokens:
        return True

    block_type_lower = candidate.block_type.lower()
    for token in family_tokens:
        if token in block_type_lower:
            return True

    desc = _describe_block_safe(candidate.block_type)
    if desc:
        label = (desc.get("label") or "").lower()
        category = " ".join(desc.get("category_path", [])).lower()
        for token in family_tokens:
            if token in label or token in category:
                return True

    return False


def _describe_block_safe(block_type: str) -> dict[str, Any] | None:
    try:
        d = describe_block(block_type)
        if d.get("ok"):
            return d
    except Exception as exc:
        logger.debug("_describe_block_safe failed for block_type=%s: %s", block_type, exc)
    return None


def _score_candidates(
    candidates: list[tuple[str, InsertionCandidate]],
    goal: str,
    preferred_block_type: str | None,
) -> list[tuple[int, str, InsertionCandidate]]:
    """Score candidates using generic signals only."""
    scored: list[tuple[int, str, InsertionCandidate]] = []
    freq = _catalog_token_frequency()
    goal_words = [
        w
        for w in _extract_tokens(goal)
        if len(w) > 2 and freq.get(w, 0) > 0
    ]
    for conn_id, candidate in candidates:
        score = 0
        block_type = candidate.block_type
        if preferred_block_type and block_type == preferred_block_type:
            score += 5
        elif preferred_block_type and preferred_block_type in block_type:
            score += 3

        for word in goal_words:
            if word in block_type.lower():
                score += 3

        if candidate.confidence == "high":
            score += 2
        elif candidate.confidence == "medium":
            score += 1

        scored.append((score, conn_id, candidate))
    return scored


# =========================================================================
# Public API — auto insert
# =========================================================================


def auto_insert_block(
    session,
    goal: str,
    preferred_block_type: str | None = None,
    target_hint: str | None = None,
    max_candidates: int = 10,
    catalog_root: str | None = None,
) -> dict[str, Any]:
    """Autonomously insert a compatible block into the graph.

    1. Enumerate stream connections.
    2. Gather insertion candidates from suggest_insertions.
    3. Classify goal (explicit family vs generic vs unsupported).
    4. Filter candidates to matching family when goal is explicit.
    5. Score remaining candidates using generic signals.
    6. Try top candidates on cloned sessions to find validated options.
    7. If exactly one validates, commit on live session.
    8. If multiple validate, return clarification with real executable options.
    9. If none succeed, return safe rejection with diagnostics.
    """
    if session.flowgraph is None:
        return _error("NO_GRAPH_LOADED", "No flowgraph loaded in session.")

    stream_connections = _stream_connections(session)
    if not stream_connections:
        return _error(
            "UNSUPPORTED_GOAL_FOR_AUTO_INSERT",
            "No stream connections found. Auto-insert requires a graph with at least one stream connection.",
        )

    intent = _classify_goal(goal, preferred_block_type)
    if intent["mode"] == "unsupported":
        return _error(
            "UNSUPPORTED_GOAL_FOR_AUTO_INSERT",
            f"Goal '{goal}' is not supported for automated insertion.",
        )

    suggest_k = 500 if intent["mode"] in ("explicit_family", "preferred_type") else 5
    all_candidates: list[tuple[str, InsertionCandidate]] = []
    for conn_id in stream_connections:
        suggestions = suggest_insertions(session, conn_id, k=suggest_k)
        if suggestions.ok and suggestions.candidates:
            for c in suggestions.candidates:
                all_candidates.append((conn_id, c))

    if not all_candidates:
        return _error(
            "UNSUPPORTED_GOAL_FOR_AUTO_INSERT",
            "No compatible insertion candidates found for any stream connection.",
        )

    if intent["mode"] == "explicit_family" and intent["family_tokens"]:
        filtered = [
            (conn_id, cand)
            for conn_id, cand in all_candidates
            if _candidate_matches_family(cand, intent["family_tokens"])
        ]
        if not filtered:
            return _error(
                "AUTO_INSERT_NO_GOAL_MATCH",
                f"No compatible candidates match goal '{goal}'. "
                f"Tried {len(all_candidates)} compatible candidates across {len(stream_connections)} connections; "
                f"none matched block family {intent['family_tokens']}.",
            )
        all_candidates = filtered

    if intent["mode"] == "preferred_type" and preferred_block_type:
        filtered = [
            (conn_id, cand)
            for conn_id, cand in all_candidates
            if preferred_block_type in cand.block_type or cand.block_type == preferred_block_type
        ]
        if not filtered:
            return _error(
                "AUTO_INSERT_NO_GOAL_MATCH",
                f"No compatible candidates match preferred_block_type '{preferred_block_type}'. "
                f"Tried {len(all_candidates)} candidates across {len(stream_connections)} connections.",
            )
        all_candidates = filtered

    scored = _score_candidates(all_candidates, goal, preferred_block_type)
    ranked = sorted(scored, key=lambda x: x[0], reverse=True)[:max_candidates]

    if intent["mode"] == "explicit_family":
        top_score = ranked[0][0] if ranked else -1
        if top_score < intent.get("min_score", 3):
            return _error(
                "AUTO_INSERT_NO_GOAL_MATCH",
                f"Goal '{goal}' was too specific; top candidate relevance score {top_score} "
                f"is below threshold {intent.get('min_score', 3)}. "
                f"No semantically relevant candidate found.",
            )

    validated: list[tuple[int, str, InsertionCandidate, dict[str, Any]]] = []
    attempted: list[dict[str, Any]] = []
    for score, conn_id, candidate in ranked:
        result = _try_candidate_on_clone(session, conn_id, candidate, catalog_root)
        entry = _build_attempted_entry(score, conn_id, candidate, result, intent)
        attempted.append(entry)
        if result.get("ok"):
            validated.append((score, conn_id, candidate, entry))
            if len(validated) >= _CLARIFICATION_THRESHOLD:
                break
        if len(attempted) >= max_candidates:
            break

    if len(validated) == 0:
        return _safe_rejection(attempted, intent)

    if len(validated) == 1:
        _score_val, conn_id, candidate, entry = validated[0]
        live_result = _try_candidate(session, conn_id, candidate, catalog_root)
        entry_from_live = _build_attempted_entry(
            entry["score"], conn_id, candidate, live_result, intent
        )
        if live_result.get("ok"):
            return {
                "ok": True,
                "message": f"Inserted '{candidate.block_type}' into {conn_id}.",
                "committed": entry_from_live,
                "attempted": [entry_from_live],
                "attempt_count": 1,
                "goal_mode": intent["mode"],
            }
        full_attempted = list(attempted)
        full_attempted.append(entry_from_live)
        return _safe_rejection(full_attempted, intent)

    return _build_clarification_payload(validated, attempted, goal, intent)
