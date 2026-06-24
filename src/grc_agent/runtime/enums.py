"""Shared string enums for runtime models.

- SearchDomain: catalog/docs routing key for ``query_knowledge`` and
  ``inspect_graph``. Using a StrEnum everywhere ensures member
  comparisons read off the canonical symbol and prevents drift between
  producer and consumer.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["SearchDomain"]


class SearchDomain(StrEnum):
    """Domains accepted by the query_knowledge / inspect_graph search."""

    CATALOG = "catalog"
    DOCS = "docs"
