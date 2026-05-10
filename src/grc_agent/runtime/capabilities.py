"""Metadata-only capability contracts for change_graph operation kinds."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from grc_agent.runtime.tool_schemas import build_tool_schemas
from grc_agent.runtime.tool_surface import MVP_MODEL_TOOL_NAMES

CapabilityStatus = Literal["release_validated", "beta_validated", "unvalidated"]


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """Declarative metadata for one change_graph capability.

    This type is intentionally metadata-only. It is not used for planning,
    execution, routing, or graph mutation.
    """

    operation_kind: str
    status: CapabilityStatus
    required_args: tuple[str, ...]
    allowed_aliases: tuple[str, ...]
    target_ref_policy: str
    graph_delta_contract: str
    eval_suite: str | None
    negative_tests: tuple[str, ...]
    supports_preview: bool
    supports_commit: bool


@lru_cache(maxsize=1)
def change_graph_operation_kinds() -> tuple[str, ...]:
    """Return the operation kinds declared by the model-facing schema."""

    schemas = build_tool_schemas(MVP_MODEL_TOOL_NAMES)
    change_graph_schema = next(
        schema
        for schema in schemas
        if schema.get("function", {}).get("name") == "change_graph"
    )
    operation_kind = (
        change_graph_schema.get("function", {})
        .get("parameters", {})
        .get("properties", {})
        .get("operation_kind", {})
    )
    enum_values = operation_kind.get("enum", [])
    return tuple(str(value) for value in enum_values)


CAPABILITY_SPECS: dict[str, CapabilitySpec] = {
    "set_param": CapabilitySpec(
        operation_kind="set_param",
        status="release_validated",
        required_args=("param_key", "param_value", "instance_name|target_ref"),
        allowed_aliases=(),
        target_ref_policy="optional_guarded_or_exact_instance",
        graph_delta_contract="parameter_mutation_only",
        eval_suite="R1_SET_PARAM_ONLY",
        negative_tests=("missing_target", "missing_param_key", "preview_no_mutation"),
        supports_preview=True,
        supports_commit=True,
    ),
    "set_state": CapabilitySpec(
        operation_kind="set_state",
        status="beta_validated",
        required_args=("state", "instance_name|target_ref"),
        allowed_aliases=(),
        target_ref_policy="optional_guarded_or_exact_instance",
        graph_delta_contract="block_state_mutation_only",
        eval_suite="R1_SET_STATE",
        negative_tests=(
            "stale_target_ref",
            "invalid_state",
            "duplicate_target_clarification",
            "preview_no_mutation",
        ),
        supports_preview=True,
        supports_commit=True,
    ),
    "disconnect": CapabilitySpec(
        operation_kind="disconnect",
        status="beta_validated",
        required_args=("connection_id|endpoint_hints",),
        allowed_aliases=(),
        target_ref_policy="not_applicable",
        graph_delta_contract="remove_exact_connection",
        eval_suite="R2_DISCONNECT",
        negative_tests=(
            "invalid_disconnect_refused",
            "ambiguous_endpoint_clarification",
            "stale_revision",
            "preview_no_mutation",
        ),
        supports_preview=True,
        supports_commit=True,
    ),
    "rewire": CapabilitySpec(
        operation_kind="rewire",
        status="beta_validated",
        required_args=(
            "state_revision",
            "connection_id|old_endpoint_hints",
            "new_src_block",
            "new_src_port",
            "new_dst_block",
            "new_dst_port",
        ),
        allowed_aliases=(),
        target_ref_policy="not_applicable",
        graph_delta_contract="one_removed_connection_one_added_connection",
        eval_suite="R3_REWIRE",
        negative_tests=(
            "stale_revision",
            "invalid_new_endpoint",
            "ambiguous_candidate_clarification",
            "preview_no_mutation",
        ),
        supports_preview=True,
        supports_commit=True,
    ),
    "insert_block": CapabilitySpec(
        operation_kind="insert_block",
        status="beta_validated",
        required_args=("connection_id", "block_id|candidate_id"),
        allowed_aliases=("insert_block",),
        target_ref_policy="not_applicable",
        graph_delta_contract="one_added_block_one_removed_connection_two_added_connections",
        eval_suite="R4A_INSERT",
        negative_tests=(
            "incompatible_candidate_refused",
            "stale_revision",
            "missing_candidate_refused",
            "preview_no_mutation",
        ),
        supports_preview=True,
        supports_commit=True,
    ),
    "remove_block": CapabilitySpec(
        operation_kind="remove_block",
        status="beta_validated",
        required_args=("instance_name|target_ref",),
        allowed_aliases=(),
        target_ref_policy="guarded_target_ref_preferred_duplicate_safe",
        graph_delta_contract="removed_block_and_optional_explicit_removed_connections",
        eval_suite="R4B_REMOVE",
        negative_tests=(
            "attached_without_explicit_detach_refused",
            "stale_target_ref",
            "referenced_dependency_refused",
            "preview_no_mutation",
        ),
        supports_preview=True,
        supports_commit=True,
    ),
    "add_variable": CapabilitySpec(
        operation_kind="add_variable",
        status="unvalidated",
        required_args=("variable_name", "variable_value"),
        allowed_aliases=(),
        target_ref_policy="not_applicable",
        graph_delta_contract="add_one_variable_block",
        eval_suite=None,
        negative_tests=("duplicate_name_refused", "preview_no_mutation"),
        supports_preview=True,
        supports_commit=True,
    ),
    "auto_insert": CapabilitySpec(
        operation_kind="auto_insert",
        status="unvalidated",
        required_args=("connection_id",),
        allowed_aliases=(),
        target_ref_policy="not_applicable",
        graph_delta_contract="implementation_defined",
        eval_suite=None,
        negative_tests=("preview_no_mutation",),
        supports_preview=True,
        supports_commit=True,
    ),
    "clarify": CapabilitySpec(
        operation_kind="clarify",
        status="unvalidated",
        required_args=(),
        allowed_aliases=(),
        target_ref_policy="not_applicable",
        graph_delta_contract="none",
        eval_suite=None,
        negative_tests=("no_mutation",),
        supports_preview=True,
        supports_commit=False,
    ),
    "unsupported": CapabilitySpec(
        operation_kind="unsupported",
        status="unvalidated",
        required_args=(),
        allowed_aliases=(),
        target_ref_policy="not_applicable",
        graph_delta_contract="none",
        eval_suite=None,
        negative_tests=("no_mutation",),
        supports_preview=True,
        supports_commit=False,
    ),
}


def get_capability_spec(operation_kind: str) -> CapabilitySpec:
    """Return the capability spec for a known change_graph operation kind."""

    return CAPABILITY_SPECS[operation_kind]


def capability_specs_for_change_graph() -> tuple[CapabilitySpec, ...]:
    """Return specs in schema enum order for deterministic reporting/tests."""

    return tuple(get_capability_spec(kind) for kind in change_graph_operation_kinds())
