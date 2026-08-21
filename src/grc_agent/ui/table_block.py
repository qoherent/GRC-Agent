# ruff: noqa: E402
"""A Markdown table rendered as a real ``Gtk.Grid`` (no ASCII art).

Replaces the old ``_format_table`` pipe/plus/dash drawing. Cells are laid out
in a grid inside a horizontally-scrolling ``ScrolledWindow``; header cells are
bold. A ``render_inline(text, bold)`` callback supplies badge-aware cell
content (so block names inside a table become clickable pills) — passed in by
the markdown renderer so this widget stays decoupled from the canvas.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk


def parse_table(table_soup) -> tuple[list[str], list[list[str]]]:
    """Extract (headers, rows) from a BeautifulSoup <table> element, padding
    ragged rows to a uniform column count. Returns ([], []) for an empty table."""
    headers: list[str] = []
    thead = table_soup.find("thead")
    if thead:
        headers = [th.get_text().strip() for th in thead.find_all("th")]

    tbody = table_soup.find("tbody")
    rows: list[list[str]] = []
    tr_iter = tbody.find_all("tr") if tbody else table_soup.find_all("tr")
    for tr in tr_iter:
        rows.append([td.get_text().strip() for td in tr.find_all(["td", "th"])])

    if not headers and rows:
        headers = rows[0]
        rows = rows[1:]

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
        if render_inline is not None:
            return render_inline(text or "", bold)
        safe = GLib.markup_escape_text(text or "", -1)
        lbl = Gtk.Label()
        lbl.set_markup(f"<b>{safe}</b>" if bold else safe)
        lbl.set_xalign(0.0)
        lbl.set_selectable(True)
        return lbl
