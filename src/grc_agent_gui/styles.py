from __future__ import annotations

from dataclasses import dataclass


def ui_font_metrics(zoom_factor: float = 1.0) -> UIFontMetrics:
    """Return the unified UI font metrics for a given zoom factor.

    One source of truth for every font size in the GUI. ``chat_pt`` is the
    point size passed to :func:`QTextDocument.setDefaultFont`; Qt converts
    pt → px using the configured DPI (``1pt ≈ 1.333 CSS px``), so
    ``chat_pt * 0.75`` ≈ the ``body_px`` the QTextBrowser will inherit.
    """
    body = max(12, int(15 * zoom_factor))
    mono = max(11, int(14 * zoom_factor))
    small = max(10, int(12 * zoom_factor))
    chat_pt = max(9, round(body * 0.8))
    return UIFontMetrics(
        body_px=body,
        mono_px=mono,
        small_px=small,
        chat_pt=chat_pt,
    )


@dataclass(frozen=True)
class UIFontMetrics:
    """Pixel / point sizes for every text style in the GUI at a given zoom."""

    body_px: int
    mono_px: int
    small_px: int
    chat_pt: int


def get_stylesheet(zoom_factor: float = 1.0) -> str:
    f = ui_font_metrics(zoom_factor)

    scrollbar_width = max(6, int(8 * zoom_factor))
    scrollbar_radius = max(3, int(4 * zoom_factor))
    padding_lineedit_y = max(4, int(6 * zoom_factor))
    padding_lineedit_x = max(6, int(10 * zoom_factor))
    padding_button_y = max(3, int(5 * zoom_factor))
    padding_button_x = max(8, int(14 * zoom_factor))
    padding_statusbar_y = max(1, int(2 * zoom_factor))
    padding_statusbar_x = max(4, int(8 * zoom_factor))

    return f"""
QMainWindow {{
    background-color: #1e1e2e;
}}
QWidget {{
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: {f.body_px}px;
}}
QScrollBar:vertical {{
    border: none;
    background: #11111b;
    width: {scrollbar_width}px;
    margin: 0px;
    border-radius: {scrollbar_radius}px;
}}
QScrollBar::handle:vertical {{
    background: #45475a;
    min-height: 20px;
    border-radius: {scrollbar_radius}px;
}}
QScrollBar::handle:vertical:hover {{
    background: #585b70;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}
QScrollBar:horizontal {{
    border: none;
    background: #11111b;
    height: {scrollbar_width}px;
    margin: 0px;
    border-radius: {scrollbar_radius}px;
}}
QScrollBar::handle:horizontal {{
    background: #45475a;
    min-width: 20px;
    border-radius: {scrollbar_radius}px;
}}
QScrollBar::handle:horizontal:hover {{
    background: #585b70;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}
QTextBrowser {{
    background-color: #181825;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 6px;
    selection-background-color: #45475a;
    selection-color: #cdd6f4;
}}
QLineEdit {{
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: {padding_lineedit_y}px {padding_lineedit_x}px;
    color: #cdd6f4;
    font-size: {f.body_px}px;
}}
QLineEdit:focus {{
    border: 1px solid #89b4fa;
}}
QTableWidget, QTreeWidget, QListWidget {{
    background-color: #181825;
    border: 1px solid #45475a;
    border-radius: 4px;
    alternate-background-color: #1e1e2e;
    outline: none;
}}
QTableWidget::item, QTreeWidget::item, QListWidget::item {{
    padding: 3px 6px;
    border: none;
}}
QTableWidget::item:selected, QTreeWidget::item:selected, QListWidget::item:selected {{
    background-color: #45475a;
    color: #cdd6f4;
}}
QHeaderView::section {{
    background-color: #313244;
    color: #cdd6f4;
    padding: 4px 8px;
    border: none;
    border-bottom: 1px solid #45475a;
    font-weight: bold;
}}
QPlainTextEdit {{
    background-color: #181825;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px;
    color: #a6e3a1;
    font-family: monospace;
    font-size: {f.mono_px}px;
}}
QSplitter::handle {{
    background-color: #45475a;
}}
QSplitter::handle:horizontal {{
    width: 2px;
}}
QSplitter::handle:vertical {{
    height: 2px;
}}
QStatusBar {{
    background-color: #11111b;
    color: #a6adc8;
    border-top: 1px solid #45475a;
    font-size: {f.small_px}px;
    padding: {padding_statusbar_y}px {padding_statusbar_x}px;
}}
QStatusBar::item {{
    border: none;
}}
QPushButton {{
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: {padding_button_y}px {padding_button_x}px;
    font-size: {f.body_px}px;
}}
QPushButton:hover {{
    background-color: #45475a;
}}
QPushButton:pressed {{
    background-color: #585b70;
}}
QPushButton:disabled {{
    background-color: #1e1e2e;
    color: #585b70;
    border-color: #45475a;
}}
QLabel {{
    color: #cdd6f4;
    font-size: {f.body_px}px;
}}
QToolTip {{
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px;
}}
"""


def get_model_toolbar_style(zoom_factor: float = 1.0) -> str:
    f = ui_font_metrics(zoom_factor)
    combo_padding_y = max(2, int(3 * zoom_factor))
    combo_padding_x = max(4, int(8 * zoom_factor))
    combo_min_height = max(15, int(20 * zoom_factor))
    combo_drop_width = max(12, int(18 * zoom_factor))
    btn_padding_y = max(2, int(3 * zoom_factor))
    btn_padding_x = max(4, int(6 * zoom_factor))

    return f"""
QFrame#modelToolbar {{
    background-color: #181825;
    border-bottom: 1px solid #313244;
}}
QLabel#toolbarLabel {{
    color: #a6adc8;
    font-size: {f.small_px}px;
}}
QComboBox {{
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: {combo_padding_y}px {combo_padding_x}px;
    min-height: {combo_min_height}px;
    font-size: {f.body_px}px;
}}
QComboBox:hover {{
    border-color: #585b70;
}}
QComboBox::drop-down {{
    border: none;
    width: {combo_drop_width}px;
}}
QComboBox QAbstractItemView {{
    background-color: #313244;
    color: #cdd6f4;
    selection-background-color: #45475a;
    border: 1px solid #45475a;
}}
QToolButton {{
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: {btn_padding_y}px {btn_padding_x}px;
    font-size: {f.body_px}px;
}}
QToolButton:hover {{
    border-color: #89b4fa;
    color: #89b4fa;
}}
QToolButton:disabled {{
    color: #585b70;
    border-color: #313244;
}}
"""
