"""Deterministic cleaner for bundled GNU Radio wiki Markdown exports."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .schema import ManualChunk, ManualPage

_RETRIEVED_MARKDOWN_RE = re.compile(r'Retrieved from "\[(?P<url>https://[^\]]+)\]\(')
_RETRIEVED_URL_RE = re.compile(r'Retrieved from "?(?P<url>https://[^\s"]+)')
_OLDID_RE = re.compile(r"[?&]oldid=(?P<oldid>\d+)")
_LAST_EDITED_RE = re.compile(r"This page was last edited on (?P<date>.+?)\.")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def clean_manual_page(path: str | Path) -> ManualPage:
    """Clean one wiki-exported Markdown page into cited manual chunks."""
    source = Path(path)
    lines = source.read_text(encoding="utf-8").splitlines()
    title = _extract_title(lines, source)
    source_url, oldid = _extract_retrieved(lines)
    last_edited = _extract_last_edited(lines)
    license_text = _extract_license(lines)
    body = _clean_body(lines)
    page_id = _slug(title or source.stem)
    chunks = _chunk_body(
        body,
        page_id=page_id,
        title=title,
    )
    return ManualPage(
        page_id=page_id,
        title=title,
        source_path=str(source),
        source_url=source_url,
        oldid=oldid,
        last_edited=last_edited,
        license=license_text,
        chunks=tuple(chunks),
    )


def _extract_title(lines: list[str], source: Path) -> str:
    for line in lines:
        if line.startswith("# "):
            return line[2:].strip()
    return source.stem.replace("_", " ")


def _extract_retrieved(lines: list[str]) -> tuple[str, str | None]:
    for line in lines:
        match = _RETRIEVED_MARKDOWN_RE.search(line) or _RETRIEVED_URL_RE.search(line)
        if match:
            url = match.group("url").replace(r"\(", "(").replace(r"\)", ")")
            oldid_match = _OLDID_RE.search(url)
            oldid = oldid_match.group("oldid") if oldid_match else None
            return url.rstrip("]"), oldid
    return "", None


def _extract_last_edited(lines: list[str]) -> str | None:
    for line in lines:
        match = _LAST_EDITED_RE.search(line)
        if match:
            return match.group("date")
    return None


def _extract_license(lines: list[str]) -> str | None:
    for line in lines:
        if "Content is available under" in line:
            return _strip_links(line).strip(" *")
    return None


def _clean_body(lines: list[str]) -> list[tuple[int, str]]:
    cleaned: list[tuple[int, str]] = []
    in_code = False
    in_contents = False
    started = False

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            in_code = not in_code
            if started:
                cleaned.append((line_number, line))
            continue

        if not in_code:
            if stripped == "## Navigation menu":
                break
            if stripped.startswith("Retrieved from "):
                break
            if stripped == "## Contents":
                in_contents = True
                continue
            if in_contents:
                if stripped.startswith("## ") and stripped != "## Contents":
                    in_contents = False
                else:
                    continue
            if _is_boilerplate_line(stripped):
                continue
            line = _strip_links(line)
            stripped = line.strip()
            if not started:
                if stripped.startswith("# "):
                    started = True
                    cleaned.append((line_number, stripped))
                elif _is_meaningful_start(stripped):
                    started = True
                    cleaned.append((line_number, stripped))
                continue

        if started:
            cleaned.append((line_number, line))

    return _collapse_blank_lines(cleaned)


def _is_boilerplate_line(stripped: str) -> bool:
    if not stripped:
        return False
    if stripped == "From GNU Radio":
        return True
    if stripped.startswith("[Jump to navigation]"):
        return True
    if stripped.startswith("[![]("):
        return True
    if stripped in {"|", "| --- |", "English", "More", "### Search"}:
        return True
    if stripped.startswith("  * [") or stripped.startswith("* ["):
        return True
    if stripped.startswith("[](https://wiki.gnuradio.org"):
        return True
    return False


def _is_meaningful_start(stripped: str) -> bool:
    if not stripped or stripped.startswith("|") or stripped.startswith("["):
        return False
    if re.match(r"^\d+\. ", stripped):
        return False
    return any(character.isalpha() for character in stripped)


def _strip_links(text: str) -> str:
    return _MARKDOWN_LINK_RE.sub(r"\1", text)


def _collapse_blank_lines(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    collapsed: list[tuple[int, str]] = []
    previous_blank = False
    for line_number, line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        collapsed.append((line_number, line))
        previous_blank = blank
    return collapsed


def _chunk_body(
    body: list[tuple[int, str]],
    *,
    page_id: str,
    title: str,
    max_chars: int = 1200,
) -> list[ManualChunk]:
    chunks: list[ManualChunk] = []
    heading_path: list[str] = []
    buffer: list[tuple[int, str]] = []

    def flush() -> None:
        if not buffer:
            return
        text = "\n".join(line for _, line in buffer).strip()
        if not text:
            buffer.clear()
            return
        ordinal = len(chunks)
        line_start = buffer[0][0]
        line_end = buffer[-1][0]
        digest = hashlib.sha1(  # noqa: S324 - stable non-security chunk id.
            f"{page_id}:{ordinal}:{line_start}:{line_end}".encode()
        ).hexdigest()[:12]
        chunks.append(
            ManualChunk(
                chunk_id=f"manual:{page_id}:{digest}",
                page_id=page_id,
                title=title,
                heading_path=tuple(heading_path),
                text=text,
                line_start=line_start,
                line_end=line_end,
                ordinal=ordinal,
                content_kind="code" if text.startswith("```") else "text",
            )
        )
        buffer.clear()

    for line_number, line in body:
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            flush()
            heading_path = [stripped.lstrip("#").strip()]
        elif stripped.startswith("### "):
            flush()
            if heading_path:
                heading_path = [heading_path[0], stripped.lstrip("#").strip()]
            else:
                heading_path = [stripped.lstrip("#").strip()]

        current_size = sum(len(item[1]) + 1 for item in buffer)
        if buffer and current_size + len(line) > max_chars:
            flush()
        buffer.append((line_number, line))
    flush()
    return chunks


def _slug(text: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return "_".join(tokens) or "manual_page"
