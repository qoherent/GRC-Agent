"""Shared runtime-data directory resolution — package-relative, not
repo-root-relative, so it works identically for a dev checkout or an
installed package. Each can be overridden via an env var for flexibility."""

import os
from pathlib import Path


def vectors_dir() -> Path:
    override = os.environ.get("GRC_AGENT_VECTORS_DIR")
    return Path(override) if override else Path(__file__).resolve().parent / "vectors"


def docs_dir() -> Path:
    override = os.environ.get("GRC_AGENT_DOCS_DIR")
    if override:
        return Path(override)
    # Check if bundled package docs exist first (for wheel installations),
    # then fall back to the source repository location (for dev checkouts).
    pkg_docs = Path(__file__).resolve().parent / "docs" / "wiki_gnuradio_org"
    if pkg_docs.exists():
        return pkg_docs
    return Path(__file__).resolve().parent.parent.parent / "docs" / "wiki_gnuradio_org"
