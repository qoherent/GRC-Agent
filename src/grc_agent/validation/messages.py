"""Stable text helpers for preflight validation issues."""

from __future__ import annotations

from collections.abc import Iterable


def format_allowed_values(options: Iterable[object]) -> str:
    rendered = ", ".join(str(option) for option in options)
    return f"Valid values: {rendered}." if rendered else ""


def format_endpoint(block_name: str, port: int | str) -> str:
    return f"{block_name}({port})"


def format_port_range(port_count: int) -> str:
    if port_count <= 0:
        return "none"
    if port_count == 1:
        return "0"
    return f"0-{port_count - 1}"


def format_catalog_lookup_message(block_type: str) -> str:
    return f"Could not resolve GNU catalog metadata for block type '{block_type}'."
