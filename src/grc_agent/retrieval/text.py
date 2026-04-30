"""Search-text normalization helpers for retrieval."""

from __future__ import annotations

import re
from collections.abc import Iterable

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

ALIAS_EXPANSIONS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("audio", "smoother"), ("low", "pass", "filter", "lowpass")),
    (("automatic", "gain", "control"), ("agc",)),
    (("spectrum",), ("frequency", "waterfall", "sink")),
    (("rate", "limiter"), ("throttle",)),
    (("scope",), ("time", "sink")),
    (("trace",), ("time", "sink")),
)


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
    for token in _expanded_term_stream(materialized):
        if token not in seen:
            ordered.append(token)
            seen.add(token)
    for index in range(len(materialized) - 1):
        joined = materialized[index] + materialized[index + 1]
        if joined and joined not in seen:
            ordered.append(joined)
            seen.add(joined)
    return tuple(ordered)


def _expanded_term_stream(tokens: tuple[str, ...]) -> Iterable[str]:
    yield from tokens
    for phrase, expansion in ALIAS_EXPANSIONS:
        if _contains_phrase(tokens, phrase):
            yield from expansion


def _contains_phrase(tokens: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    if not phrase or len(phrase) > len(tokens):
        return False
    phrase_length = len(phrase)
    return any(tokens[index : index + phrase_length] == phrase for index in range(len(tokens)))
