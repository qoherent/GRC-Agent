"""Search-text normalization helpers for retrieval."""

from __future__ import annotations

import re
from collections.abc import Iterable

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def normalize_text(text: str) -> str:
    """Lowercase and normalize a string into whitespace-separated search tokens."""
    return " ".join(TOKEN_PATTERN.findall(text.lower()))


def tokenize_text(text: str) -> tuple[str, ...]:
    """Tokenize text into normalized alphanumeric search tokens."""
    normalized = normalize_text(text)
    if not normalized:
        return ()
    return tuple(normalized.split())


def expand_terms(tokens: Iterable[str]) -> tuple[str, ...]:
    """Expand tokens with adjacent joined forms like `band pass` -> `bandpass`."""
    ordered: list[str] = []
    seen: set[str] = set()
    materialized = tuple(token for token in tokens if token)
    for token in materialized:
        if token not in seen:
            ordered.append(token)
            seen.add(token)
    for index in range(len(materialized) - 1):
        joined = materialized[index] + materialized[index + 1]
        if joined and joined not in seen:
            ordered.append(joined)
            seen.add(joined)
    return tuple(ordered)
