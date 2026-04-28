"""Schema objects for read-only manual retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ManualChunk:
    chunk_id: str
    page_id: str
    title: str
    heading_path: tuple[str, ...]
    text: str
    line_start: int
    line_end: int
    ordinal: int
    content_kind: str = "text"


@dataclass(frozen=True)
class ManualPage:
    page_id: str
    title: str
    source_path: str
    source_url: str
    oldid: str | None
    last_edited: str | None
    license: str | None
    chunks: tuple[ManualChunk, ...] = field(default_factory=tuple)
