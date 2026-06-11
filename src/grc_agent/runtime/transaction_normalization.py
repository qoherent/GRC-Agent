"""Standalone transaction normalization for model-generated tool arguments.

All repairs are generic (wrappers, lists, malformed JSON, quoted keys).
There is no prompt-text inspection, fixture-specific repair logic, or
graph-specific recipes in this module.
"""

from __future__ import annotations

import json
import re
from typing import Any

from grc_agent.runtime.output_policy import is_variable_block


class TransactionNormalizer:
    """Normalize malformed transactions produced by small models.

    The optional *session* gives access to graph blocks for symbol resolution and
    connection-completion hints.  When it is ``None`` those heuristics are skipped.
    """

    def __init__(self, session: Any = None) -> None:
        self._session = session

    # ------------------------------------------------------------------- #
    # Entry points used by GrcAgent
    # ------------------------------------------------------------------- #

    def normalize_transaction_instance_names(self, transaction: Any) -> Any:
        if isinstance(transaction, dict):
            normalized = self._normalize_transaction_mapping(transaction, allow_embedded_operations=True)
            if isinstance(normalized, list):
                return self._dedupe_transaction_operations(normalized)
            return normalized
        if isinstance(transaction, list):
            normalized_items: list[Any] = []
            for item in transaction:
                if not isinstance(item, dict):
                    normalized_items.append(item)
                    continue
                normalized_item = self._normalize_transaction_mapping(item, allow_embedded_operations=True)
                if isinstance(normalized_item, list):
                    normalized_items.extend(normalized_item)
                else:
                    normalized_items.append(normalized_item)
            return self._dedupe_transaction_operations(normalized_items)
        return transaction

    def normalize_tool_argument_key(self, key: Any) -> tuple[Any, Any | None]:
        if not isinstance(key, str):
            return key, None
        normalized = key.strip().replace('\\"', '"').replace("\\'", "'")
        normalized = normalized.replace("<|", "").replace(">", "")
        normalized = normalized.replace("<tool_call|>", " ").replace("<eos>", " ")
        for candidate in self._tool_argument_candidates():
            if re.fullmatch(rf"[\s{{\[(\"']*{re.escape(candidate)}[\s}}\]\)\"':,]*", normalized):
                return candidate, None
        for candidate in self._tool_argument_candidates():
            match = re.search(rf"\b{re.escape(candidate)}\b\s*[:=]\s*(.+)", normalized)
            if match is not None:
                return candidate, self.normalize_inline_argument_value(match.group(1))
        for candidate in self._tool_argument_candidates():
            if re.search(rf"\b{re.escape(candidate)}\b", normalized):
                return candidate, None
        return normalized.strip("\"' "), None

    @staticmethod
    def normalize_inline_argument_value(raw_value: str) -> Any:
        cleaned = raw_value.replace("<tool_call|>", " ").replace("<eos>", " ").strip()
        token_match = re.match(r'^(".*?"|\'.*?\'|[^,}\]\s]+)', cleaned)
        token = token_match.group(1) if token_match is not None else cleaned
        try:
            return json.loads(token)
        except json.JSONDecodeError:
            return token.strip("\"'")

    # ------------------------------------------------------------------- #
    # Transaction-key / value normalisation
    # ------------------------------------------------------------------- #

    def _normalize_transaction_mapping(self, transaction: dict[str, Any], *, allow_embedded_operations: bool = False) -> Any:
        normalized: dict[str, Any] = {}
        embedded_operations: list[dict[str, Any]] = []
        for raw_key, raw_value in transaction.items():
            key = self._normalize_transaction_key(raw_key)
            value = self._normalize_transaction_value(raw_value)
            if allow_embedded_operations and key == "op_type" and isinstance(value, dict):
                repaired_embedded = self._unwrap_embedded_transaction_operation(value)
                if repaired_embedded is not None:
                    embedded_operations.append(self._repair_partial_update_params_operation(repaired_embedded))
                    continue
                if TransactionNormalizer.looks_like_transaction_payload(value):
                    embedded_operations.append(value)
                    continue
            normalized[key] = value

        normalized = self._repair_wrapped_transaction_mapping(normalized)
        normalized = self._repair_list_encoded_transaction_operation(normalized)
        normalized = self._repair_malformed_op_type_string(normalized)

        if embedded_operations and not normalized:
            return embedded_operations if len(embedded_operations) != 1 else embedded_operations[0]

        if "op_type" not in normalized:
            instance_name = normalized.get("instance_name")
            if isinstance(instance_name, str) and isinstance(normalized.get("params"), dict):
                normalized["op_type"] = "update_params"
            elif isinstance(instance_name, str) and isinstance(normalized.get("state"), str):
                normalized["op_type"] = "update_states"
        normalized = self._repair_partial_update_params_operation(normalized)
        normalized = self._repair_partial_remove_connection_operation(normalized)
        normalized = self._repair_partial_add_block_operation(normalized)
        instance_name = normalized.get("instance_name")
        if isinstance(instance_name, str):
            resolved = self._resolve_symbol_like_name(instance_name)
            if resolved is not None:
                normalized["instance_name"] = resolved
        return normalized

    def _normalize_transaction_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return self._normalize_transaction_mapping(value)
        if isinstance(value, list):
            return [self._normalize_transaction_mapping(item) if isinstance(item, dict) else item for item in value]
        return value

    @staticmethod
    def _normalize_transaction_key(key: Any) -> Any:
        if not isinstance(key, str):
            return key
        normalized = key.strip().replace('\\"', '"').replace("\\'", "'")
        normalized = normalized.replace("<|", "").replace(">", "")
        while normalized and normalized[0] in "{[(":
            normalized = normalized[1:].lstrip()
        while normalized and normalized[-1] in "}]),:":
            normalized = normalized[:-1].rstrip()
        normalized = normalized.strip("\"'")
        # Strip remaining control-token pipe markers
        while normalized and normalized[0] == "|":
            normalized = normalized[1:].lstrip()
        while normalized and normalized[-1] == "|":
            normalized = normalized[:-1].rstrip()
        normalized = normalized.strip("\"'")
        if normalized:
            for candidate in (
                "update_params", "update_states", "add_connection", "remove_connection",
                "remove_block", "add_block", "insert_block_on_connection",
                "op_type", "instance_name", "params",
                "parameters", "state", "src_block", "src_port", "dst_block", "dst_port", "block_type",
            ):
                if re.search(rf"\b{re.escape(candidate)}\b", normalized):
                    return candidate
            return normalized
        return key

    # ------------------------------------------------------------------- #
    # Concrete-shape repairs
    # ------------------------------------------------------------------- #

    def _unwrap_embedded_transaction_operation(self, value: dict[str, Any]) -> dict[str, Any] | None:
        if len(value) != 1:
            return None
        op_type, op_payload = next(iter(value.items()))
        if op_type not in self._supported_transaction_op_types():
            return None
        if isinstance(op_payload, list):
            if len(op_payload) != 1 or not isinstance(op_payload[0], dict):
                return None
            op_payload = op_payload[0]
        if not isinstance(op_payload, dict):
            return None
        repaired = dict(op_payload)
        repaired["op_type"] = op_type
        return self._repair_wrapped_transaction_mapping(repaired)

    @staticmethod
    def _repair_wrapped_transaction_mapping(normalized: dict[str, Any]) -> dict[str, Any]:
        if len(normalized) == 1:
            only_key, only_value = next(iter(normalized.items()))
            if only_key in TransactionNormalizer._supported_transaction_op_types() and isinstance(only_value, dict):
                repaired = dict(only_value)
                repaired["op_type"] = only_key
                return repaired
        op_type = normalized.get("op_type")
        if isinstance(op_type, dict) and len(op_type) == 1:
            wrapped_op_type, wrapped_payload = next(iter(op_type.items()))
            if isinstance(wrapped_payload, list):
                if len(wrapped_payload) != 1 or not isinstance(wrapped_payload[0], dict):
                    return normalized
                wrapped_payload = wrapped_payload[0]
            if wrapped_op_type in TransactionNormalizer._supported_transaction_op_types() and isinstance(wrapped_payload, dict):
                repaired = dict(normalized)
                repaired["op_type"] = wrapped_op_type
                for k, v in wrapped_payload.items():
                    repaired.setdefault(k, v)
                return repaired
        return normalized

    def _repair_list_encoded_transaction_operation(self, normalized: dict[str, Any]) -> dict[str, Any]:
        op_type = normalized.get("op_type")
        if not isinstance(op_type, list) or not op_type:
            return normalized
        first_item = op_type[0]
        if first_item not in TransactionNormalizer._supported_transaction_op_types():
            return normalized
        repaired = dict(normalized)
        repaired["op_type"] = first_item
        tail = list(op_type[1:])
        while len(tail) >= 2:
            raw_key = tail.pop(0)
            raw_value = tail.pop(0)
            key = TransactionNormalizer._normalize_transaction_key(raw_key)
            if not isinstance(key, str):
                continue
            value = self._normalize_transaction_value(raw_value)
            repaired.setdefault(key, value)
        return repaired

    @staticmethod
    def _repair_malformed_op_type_string(normalized: dict[str, Any]) -> dict[str, Any]:
        op_type = normalized.get("op_type")
        if not isinstance(op_type, str):
            return normalized
        for candidate in TransactionNormalizer._supported_transaction_op_types():
            if op_type == candidate:
                return normalized
            if not op_type.startswith(candidate):
                continue
            repaired = dict(normalized)
            repaired["op_type"] = candidate
            return repaired
        return normalized

    # ------------------------------------------------------------------- #
    # Partial-operation repairs
    # ------------------------------------------------------------------- #

    def _repair_partial_update_params_operation(self, normalized: dict[str, Any]) -> dict[str, Any]:
        if normalized.get("op_type") != "update_params":
            return normalized
        if "params" not in normalized and isinstance(normalized.get("instance_name"), dict):
            normalized["params"] = normalized.pop("instance_name")
        if "params" not in normalized:
            param_fields = {k: v for k, v in normalized.items() if k not in {"op_type", "instance_name", "params"}}
            if param_fields:
                normalized["params"] = param_fields
                for k in param_fields:
                    normalized.pop(k, None)
        if not isinstance(normalized.get("instance_name"), str):
            default_owner = self._default_update_params_instance_name(normalized)
            if default_owner is not None:
                normalized["instance_name"] = default_owner
        return normalized

    def _repair_partial_remove_connection_operation(self, normalized: dict[str, Any]) -> dict[str, Any]:
        if normalized.get("op_type") != "remove_connection":
            return normalized
        missing_fields = [f for f in ("src_block", "src_port", "dst_block", "dst_port") if f not in normalized]
        if not missing_fields or self._session is None or self._session.flowgraph is None:
            return normalized
        instance_name = normalized.get("src_block") or normalized.get("dst_block")
        if not isinstance(instance_name, str):
            return normalized
        conns = [c for c in self._session.flowgraph.connections if c.src_block == instance_name or c.dst_block == instance_name]
        if len(conns) == 1:
            c = conns[0]
            repaired = dict(normalized)
            repaired.setdefault("src_block", c.src_block)
            repaired.setdefault("src_port", c.src_port)
            repaired.setdefault("dst_block", c.dst_block)
            repaired.setdefault("dst_port", c.dst_port)
            return repaired
        return normalized

    @staticmethod
    def _repair_partial_add_block_operation(normalized: dict[str, Any]) -> dict[str, Any]:
        if normalized.get("op_type") != "add_block":
            return normalized
        if "parameters" not in normalized and isinstance(normalized.get("params"), dict):
            normalized["parameters"] = normalized.pop("params")
        instance_name = normalized.get("instance_name")
        if isinstance(instance_name, dict):
            nested_instance_name = instance_name.get("instance_name")
            if isinstance(nested_instance_name, str):
                normalized["instance_name"] = nested_instance_name
            nested_parameters = instance_name.get("parameters")
            if isinstance(nested_parameters, dict) and "parameters" not in normalized:
                normalized["parameters"] = nested_parameters
        if "block_type" not in normalized:
            return normalized
        return normalized

    def _default_update_params_instance_name(self, normalized: dict[str, Any]) -> str | None:
        params = normalized.get("params")
        if self._session is None or self._session.flowgraph is None or not isinstance(params, dict):
            return None
        if set(params) != {"value"}:
            return None
        variable_names = [block.instance_name for block in self._session.flowgraph.blocks if is_variable_block(block.block_type)]
        if len(variable_names) == 1:
            return variable_names[0]
        return None

    # ------------------------------------------------------------------- #
    # Dependency / connection helpers
    # ------------------------------------------------------------------- #

    # (Dead dependency/connection hint helpers removed)

    # ------------------------------------------------------------------- #
    # Symbol / instance-name helpers
    # ------------------------------------------------------------------- #

    def _resolve_symbol_like_name(self, identifier: str) -> str | None:
        if self._session is None or self._session.flowgraph is None or not isinstance(identifier, str):
            return None
        exact_match = next(
            (block.instance_name for block in self._session.flowgraph.blocks if block.instance_name == identifier),
            None,
        )
        if exact_match is not None:
            return exact_match
        matches: list[str] = []
        for block in self._session.flowgraph.blocks:
            parameters = block.params.get("parameters")
            if not isinstance(parameters, dict):
                continue
            if parameters.get("id") == identifier:
                matches.append(block.instance_name)
        if len(matches) == 1:
            return matches[0]
        return None

    # ------------------------------------------------------------------- #
    # Static helpers
    # ------------------------------------------------------------------- #

    @staticmethod
    def transaction_hint() -> str:
        return ""

    @staticmethod
    def looks_like_transaction_payload(payload: Any) -> bool:
        if isinstance(payload, list):
            return bool(payload) and all(TransactionNormalizer.looks_like_transaction_payload(item) for item in payload)
        if not isinstance(payload, dict):
            return False
        return any(key in payload for key in ("op_type", "instance_name", "src_block", "dst_block", "block_type"))

    @staticmethod
    def _supported_transaction_op_types() -> tuple[str, ...]:
        return (
            "update_params",
            "update_states",
            "add_connection",
            "remove_connection",
            "remove_block",
            "add_block",
            "insert_block_on_connection",
        )

    @staticmethod
    def _tool_argument_candidates() -> tuple[str, ...]:
        return (
            "transaction", "node_id", "hops", "max_nodes", "block_id", "file_path",
            "graph_id", "profile", "query", "scope", "k", "path", "max_blocks",
        )

    @staticmethod
    def _dedupe_transaction_operations(operations: list[Any]) -> list[Any]:
        deduped: list[Any] = []
        seen_serialized: set[str] = set()
        for operation in operations:
            if not isinstance(operation, dict):
                deduped.append(operation)
                continue
            serialized = json.dumps(operation, sort_keys=True)
            if serialized in seen_serialized:
                continue
            seen_serialized.add(serialized)
            deduped.append(operation)
        return deduped
