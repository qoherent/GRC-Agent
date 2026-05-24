"""Cleaned GNU Radio manual corpus helpers used by vector indexing."""

from pathlib import Path

from .clean import clean_manual_page

DEFAULT_MANUAL_ROOT = Path(__file__).resolve().parents[3] / "docs" / "wiki_gnuradio_org"

__all__ = ["DEFAULT_MANUAL_ROOT", "clean_manual_page"]
