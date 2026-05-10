"""Lifecycle wrapper handlers extracted from the GrcAgent façade."""

from __future__ import annotations

from pathlib import Path
import time
from typing import TYPE_CHECKING

from grc_agent._payload import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session import load_grc, summarize_graph

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult


def save_graph_explicit(
    agent: "GrcAgent",
    *,
    path: str | None = None,
    overwrite: bool = False,
    debug: bool = False,
) -> "ToolResult":
    started = time.monotonic()
    before_revision = agent.session.state_revision
    before_dirty = agent.session.is_dirty
    handlers: list[str] = []
    missing_session = agent._missing_session_result("save_graph_explicit")
    if missing_session is not None:
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="save_graph_explicit",
            wrapper_action="missing_session",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=missing_session,
            validation_run=False,
            output_truncated=False,
        )
    if not agent._has_explicit_save_intent():
        result = agent._tool_result(
            "save_graph_explicit",
            ok=False,
            message=(
                "Explicit save intent is required. Use clear save wording like "
                "'save', 'persist', or 'write a copy'."
            ),
            error_type=ErrorCode.INVALID_REQUEST,
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="save_graph_explicit",
            wrapper_action="intent_required",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )

    explicit_path = isinstance(path, str) and bool(path.strip())
    target_path = Path(path).expanduser() if explicit_path else agent.session.path
    if target_path is None:
        result = agent._tool_result(
            "save_graph_explicit",
            ok=False,
            message="This graph has no file path yet. Provide `path` for explicit save/copy.",
            error_type="SAVE_PATH_REQUIRED",
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="save_graph_explicit",
            wrapper_action="missing_path",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )

    resolved_target = target_path.resolve(strict=False)
    unsafe_root = agent._unsafe_graph_root_for_path(resolved_target)
    if unsafe_root is not None:
        result = agent._tool_result(
            "save_graph_explicit",
            ok=False,
            message=(
                "Refusing to write to protected canonical/example graph paths. "
                f"Choose a copied working path outside {unsafe_root}."
            ),
            error_type=ErrorCode.SAVE_REFUSED,
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="save_graph_explicit",
            wrapper_action="unsafe_path",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )

    current_path = (
        agent.session.path.resolve(strict=False) if agent.session.path is not None else None
    )
    explicit_target_path = explicit_path and path is not None
    target_exists = resolved_target.exists()
    if (
        explicit_target_path
        and target_exists
        and current_path is not None
        and resolved_target != current_path
        and not overwrite
    ):
        result = agent._tool_result(
            "save_graph_explicit",
            ok=False,
            message=(
                "Refusing to overwrite existing destination without explicit overwrite permission. "
                "Set overwrite=true for that destination."
            ),
            error_type=ErrorCode.SAVE_REFUSED,
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="save_graph_explicit",
            wrapper_action="overwrite_refused",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )

    handlers.append("validate_graph")
    validation = agent._validate_graph()
    validation_result = {
        "valid": bool(validation.get("valid")),
        "returncode": validation.get("returncode"),
        "stderr": validation.get("stderr"),
    }
    if validation.get("ok") is not True:
        result = agent._payload_result(
            "save_graph_explicit",
            {
                "ok": False,
                "message": validation.get("message", "Graph validation failed before save."),
                "error_type": validation.get("error_type", ErrorCode.VALIDATION_ERROR),
                "path": str(resolved_target),
                "dirty_before": bool(before_dirty),
                "dirty_after": bool(agent.session.is_dirty),
                "validation_result": validation_result,
            },
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="save_graph_explicit",
            wrapper_action="validation_failed",
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=True,
            output_truncated=False,
        )
    if not bool(validation.get("valid")):
        result = agent._payload_result(
            "save_graph_explicit",
            {
                "ok": False,
                "message": "Refusing to save invalid graph state.",
                "error_type": ErrorCode.SAVE_REFUSED,
                "path": str(resolved_target),
                "dirty_before": bool(before_dirty),
                "dirty_after": bool(agent.session.is_dirty),
                "validation_result": validation_result,
            },
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="save_graph_explicit",
            wrapper_action="invalid_graph",
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=True,
            output_truncated=False,
        )

    handlers.append("save_graph")
    save_result = agent._save_graph(str(resolved_target) if explicit_target_path else None)
    payload = {
        "ok": bool(save_result.get("ok")),
        "message": save_result.get("message", "Save failed."),
        "error_type": save_result.get("error_type"),
        "path": save_result.get("path", str(resolved_target)),
        "dirty_before": bool(before_dirty),
        "dirty_after": bool(agent.session.is_dirty),
        "validation_result": validation_result,
    }
    wrapper_result = agent._payload_result("save_graph_explicit", payload)
    return agent._attach_wrapper_dispatch_telemetry(
        debug=debug,
        wrapper_name="save_graph_explicit",
        wrapper_action="save",
        internal_handlers=handlers,
        started=started,
        before_revision=before_revision,
        before_dirty=before_dirty,
        result=wrapper_result,
        validation_run=True,
        output_truncated=False,
    )


def load_graph_explicit(
    agent: "GrcAgent",
    *,
    path: str,
    debug: bool = False,
) -> "ToolResult":
    started = time.monotonic()
    before_revision = agent.session.state_revision
    before_dirty = agent.session.is_dirty
    handlers: list[str] = []
    if not agent._has_explicit_load_intent():
        result = agent._tool_result(
            "load_graph_explicit",
            ok=False,
            message=(
                "Explicit load intent is required. Use clear load wording like "
                "'load', 'open', or 'switch to'."
            ),
            error_type=ErrorCode.INVALID_REQUEST,
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="load_graph_explicit",
            wrapper_action="intent_required",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )
    if not isinstance(path, str) or not path.strip():
        result = agent._tool_result(
            "load_graph_explicit",
            ok=False,
            message="load_graph_explicit requires non-empty `path`.",
            error_type=ErrorCode.INVALID_REQUEST,
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="load_graph_explicit",
            wrapper_action="missing_path",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )

    resolved_path = Path(path).expanduser().resolve(strict=False)
    unsafe_root = agent._unsafe_graph_root_for_path(resolved_path)
    if unsafe_root is not None:
        result = agent._tool_result(
            "load_graph_explicit",
            ok=False,
            message=(
                "Refusing to load protected canonical/example graph directly for mutation. "
                f"Copy it to a working path outside {unsafe_root} and load the copy."
            ),
            error_type=ErrorCode.FILE_LOAD_ERROR,
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="load_graph_explicit",
            wrapper_action="unsafe_path",
            internal_handlers=["none"],
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )

    handlers.append("load_grc")
    loaded = load_grc(str(resolved_path))
    if not isinstance(loaded, FlowgraphSession):
        result = agent._tool_result(
            "load_graph_explicit",
            ok=False,
            message=loaded.get("message", "Failed to load .grc file."),
            error_type=loaded.get("error_type", ErrorCode.FILE_LOAD_ERROR),
        )
        return agent._attach_wrapper_dispatch_telemetry(
            debug=debug,
            wrapper_name="load_graph_explicit",
            wrapper_action="load_failed",
            internal_handlers=handlers,
            started=started,
            before_revision=before_revision,
            before_dirty=before_dirty,
            result=result,
            validation_run=False,
            output_truncated=False,
        )

    agent._replace_session(loaded, reason="load_graph_explicit")
    handlers.append("validate_graph")
    validation = agent._validate_graph()
    summary_payload = summarize_graph(agent.session)
    validation_result = {
        "valid": bool(validation.get("valid")),
        "returncode": validation.get("returncode"),
        "stderr": validation.get("stderr"),
    }
    valid_graph = bool(validation.get("ok")) and bool(validation.get("valid"))
    payload: dict[str, object] = {
        "ok": valid_graph,
        "path": str(agent.session.path) if agent.session.path is not None else str(resolved_path),
        "state_revision": agent.session.state_revision,
        "message": (
            "Graph loaded and validated."
            if valid_graph
            else "Graph loaded, but validation failed for the loaded state."
        ),
        "valid": bool(validation.get("valid")),
        "validation_result": validation_result,
        "graph_summary": summary_payload.get("summary"),
        "block_count": summary_payload.get("block_count"),
        "connection_count": summary_payload.get("connection_count"),
        "dirty": agent.session.is_dirty,
    }
    if not valid_graph:
        payload["error_type"] = (
            validation.get("error_type")
            if validation.get("ok") is False
            else ErrorCode.GNU_VALIDATION_FAILED
        )
    result = agent._payload_result("load_graph_explicit", payload)
    return agent._attach_wrapper_dispatch_telemetry(
        debug=debug,
        wrapper_name="load_graph_explicit",
        wrapper_action="load",
        internal_handlers=handlers,
        started=started,
        before_revision=before_revision,
        before_dirty=before_dirty,
        result=result,
        validation_run=True,
        output_truncated=False,
    )
