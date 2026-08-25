# ruff: noqa: E402
"""A Markdown table rendered as a real ``Gtk.Grid`` (no ASCII art).

Replaces the old ``_format_table`` pipe/plus/dash drawing. Cells are laid out
in a grid inside a horizontally-scrolling ``ScrolledWindow``; header cells are
bold. A ``render_inline(text, bold)`` callback supplies badge-aware cell
content (so block names inside a table become clickable pills) — passed in by
the markdown renderer so this widget stays decoupled from the canvas.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

if TYPE_CHECKING:
    from markdown_it.tree import SyntaxTreeNode


def _extract_cell_text(node: SyntaxTreeNode) -> str:
    """Extract plain text from a table cell node."""
    if node.type in ("text", "code_inline"):
        return node.content
    if node.type in ("softbreak", "hardbreak"):
        return " "
    if node.content and not node.children:
        return node.content
    return "".join(_extract_cell_text(child) for child in node.children)


def _extract_row_cells(tr: SyntaxTreeNode) -> list[str]:
    """Extract trimmed string cells from a row node."""
    return [_extract_cell_text(c).strip() for c in tr.children if c.type in ("th", "td")]


def _extract_section_rows(section: SyntaxTreeNode) -> list[list[str]]:
    return [_extract_row_cells(tr) for tr in section.children if tr.type == "tr"]


def parse_table(table_node: SyntaxTreeNode) -> tuple[list[str], list[list[str]]]:
    """Extract (headers, rows) from a markdown-it SyntaxTreeNode <table> element,
    padding ragged rows to a uniform column count. Returns ([], []) for an empty table."""
    headers: list[str] = []
    rows: list[list[str]] = []

    for section in table_node.children:
        if section.type == "thead":
            thead_rows = _extract_section_rows(section)
            if thead_rows:
                headers = thead_rows[0]
        elif section.type == "tbody":
            rows.extend(_extract_section_rows(section))
        elif section.type == "tr":
            cells = _extract_row_cells(section)
            if not headers:
                headers = cells
            else:
                rows.append(cells)

    if not headers and rows:
        headers, rows = rows[0], rows[1:]

    num_cols = max(len(headers), max((len(r) for r in rows), default=0))
    if num_cols == 0:
        return [], []

    headers += [""] * (num_cols - len(headers))
    for r in rows:
        r += [""] * (num_cols - len(r))
    return headers, rows


class TableBlock(Gtk.ScrolledWindow):
    """A horizontally-scrollable, badge-aware table grid."""

    def __init__(self, headers: list[str], rows: list[list[str]], render_inline=None) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.set_min_content_height(28)
        self.set_propagate_natural_height(True)
        self.get_style_context().add_class("chat-code-block")
        for prop in ("margin_start", "margin_end", "margin_top", "margin_bottom"):
            getattr(self, "set_" + prop)(4)

        grid = Gtk.Grid(column_spacing=10, row_spacing=3)
        grid.set_margin_start(6)
        grid.set_margin_end(6)
        grid.set_margin_top(6)
        grid.set_margin_bottom(6)
        grid.set_column_homogeneous(False)

        for c, h in enumerate(headers):
            grid.attach(self._cell(h, render_inline, bold=True), c, 0, 1, 1)
        for r, row in enumerate(rows, start=1):
            for c, val in enumerate(row):
                grid.attach(self._cell(val, render_inline, bold=False), c, r, 1, 1)

        self.add(grid)

    @staticmethod
    def _cell(text: str, render_inline, *, bold: bool):
        return render_inline(text or "", bold)
