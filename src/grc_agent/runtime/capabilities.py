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
    """Declarative metadata for one mutable change_graph capability."""

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


@dataclass(frozen=True, slots=True)
class ExperimentalOperationSpec:
    """Metadata for non-release-gating operation kinds still exposed in schema."""

    operation_kind: str
    status: CapabilityStatus
    release_gating: bool


@lru_cache(maxsize=1)
def change_graph_operation_kinds() -> tuple[str, ...]:
    """Return operation kinds declared by the model-facing change_graph schema."""

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


# Control outcomes are explicit non-mutation responses from change_graph.
CONTROL_OUTCOME_KINDS: frozenset[str] = frozenset({"clarify", "unsupported"})

# Exposed but not part of release/beta capability gating.
EXPERIMENTAL_OPERATION_SPECS: dict[str, ExperimentalOperationSpec] = {
    "auto_insert": ExperimentalOperationSpec(
        operation_kind="auto_insert",
        status="unvalidated",
        release_gating=False,
    )
}

# Mutable operation capabilities tracked for validation progress.
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
        status="beta_validated",
        required_args=("variable_name", "variable_value"),
        allowed_aliases=(),
        target_ref_policy="not_applicable",
        graph_delta_contract="add_one_variable_block",
        eval_suite="R4C_ADD_VARIABLE",
        negative_tests=(
            "duplicate_name_refused",
            "invalid_variable_name_refused",
            "invalid_expression_refused",
            "missing_value_refused",
            "preview_no_mutation",
        ),
        supports_preview=True,
        supports_commit=True,
    ),
}


def get_capability_spec(operation_kind: str) -> CapabilitySpec:
    """Return the mutable capability spec for a known operation kind."""

    return CAPABILITY_SPECS[operation_kind]


def get_experimental_operation_spec(operation_kind: str) -> ExperimentalOperationSpec:
    """Return experimental non-gating operation metadata."""

    return EXPERIMENTAL_OPERATION_SPECS[operation_kind]


def capability_specs() -> tuple[CapabilitySpec, ...]:
    """Return mutable capability specs in stable declaration order."""

    return tuple(CAPABILITY_SPECS[kind] for kind in CAPABILITY_SPECS)


def non_capability_operation_kinds() -> tuple[str, ...]:
    """Return schema operation kinds not tracked as mutable capabilities."""

    kinds = set(change_graph_operation_kinds())
    kinds -= set(CAPABILITY_SPECS)
    return tuple(sorted(kinds))
