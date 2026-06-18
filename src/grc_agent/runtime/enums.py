"""Shared string enums for runtime models.

One home for every string literal that has been copy-pasted across
change_graph.py, tool_schemas.py, and inspect_graph.py:

- BlockState: enabled/disabled/bypass (was hardcoded in change_graph.py:739
  and tool_schemas.py:407,457)
- ValidationStatus: valid/invalid/failed/skipped (was a hardcoded tuple
  in change_graph.py:591)
- SearchDomain: catalog/docs (was a literal 'catalog'/'docs' string in
  inspect_graph.py:1065,1073 and tool_schemas.py:371)

Using a StrEnum everywhere ensures member comparisons read off the
canonical symbol and prevents drift between producer and consumer.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "BlockState",
    "ValidationStatus",
    "SearchDomain",
]


class BlockState(StrEnum):
    """Valid states for an update_states operation."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    BYPASS = "bypass"


class ValidationStatus(StrEnum):
    """Status field on a validation result payload."""

    VALID = "valid"
    INVALID = "invalid"
    FAILED = "failed"
    SKIPPED = "skipped"


class SearchDomain(StrEnum):
    """Domains accepted by the query_knowledge / inspect_graph search."""

    CATALOG = "catalog"
    DOCS = "docs"


class ValidationErrorCode(StrEnum):
    """Structured codes emitted by the validation layer.

    Consumer sites (``change_graph.py``, etc.) check these codes
    uniformly instead of substring-matching GRC's free-text error
    strings. Adding a new error code: define the member here and
    emit it at the producer site in ``validation/checks.py``.
    """

    OCCUPIED_INPUT_PORT = "occupied_input_port"
    PORT_OUT_OF_RANGE = "port_out_of_range"
    INVALID_PORT = "invalid_port"
    BLOCK_NOT_FOUND = "block_not_found"
    PARAMETER_NOT_FOUND = "parameter_not_found"
