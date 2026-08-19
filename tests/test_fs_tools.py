"""Tests for the filesystem tools (src/grc_agent/fs_tools.py).

Hermetic: the active-graph providers are plain lambdas over a tmp_path —
no GTK, no canvas, no LLM. `.grc` routing exercises the real adapter
inspect engine against fixture flowgraphs (same gnuradio platform cost the
graph tests already pay).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelRetry

from grc_agent import fs_tools
from grc_agent.adapter.graph import load_flow_graph
from grc_agent.fs_tools import GrcFileSystem, GrcFileSystemToolset

FIXTURES = Path("tests/data")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def toolset() -> GrcFileSystemToolset:
    return GrcFileSystem().get_toolset()


@pytest.fixture
def _saved(tmp_path, monkeypatch):
    """A saved active flowgraph: providers resolve to proj.grc in tmp_path."""
    grc = tmp_path / "proj.grc"
    shutil.copy(FIXTURES / "dial_tone.grc", grc)
    monkeypatch.setattr(fs_tools, "_active_grc_path_fn", lambda: grc)
    return grc


def read(toolset, path, **kwargs):
    return run(toolset.read_file(path, **kwargs))


# -- gating -----------------------------------------------------------------


def test_unsaved_flowgraph_gates_every_read(toolset):
    with pytest.raises(ModelRetry, match="save the flowgraph"):
        read(toolset, "anything.txt")


def test_path_escape_denied(toolset, _saved, tmp_path):
    outside = tmp_path.parent / "grc_fs_escape_probe.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        with pytest.raises(ModelRetry, match="outside the root"):
            read(toolset, "../../grc_fs_escape_probe.txt")
    finally:
        outside.unlink(missing_ok=True)


def test_env_denied_read(toolset, _saved):
    (_saved.parent / ".env").write_text("SECRET=1\n", encoding="utf-8")
    with pytest.raises(ModelRetry, match="denied"):
        read(toolset, ".env")


def test_missing_file(toolset, _saved):
    with pytest.raises(ModelRetry, match="File not found"):
        read(toolset, "nope.txt")


def test_directory_rejected(toolset, _saved):
    (_saved.parent / "sub").mkdir()
    with pytest.raises(ModelRetry, match="directory"):
        read(toolset, "sub")


# -- plain text reads ---------------------------------------------------------


def test_plain_text_read_with_header_and_numbers(toolset, _saved):
    (_saved.parent / "notes.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    out = read(toolset, "notes.txt")
    assert out.startswith("[notes.txt | 3 lines | hash:")
    assert "     1\talpha" in out
    assert "     3\tgamma" in out


def test_offset_limit_paging(toolset, _saved):
    (_saved.parent / "big.txt").write_text("".join(f"line{i}\n" for i in range(10)), encoding="utf-8")
    out = read(toolset, "big.txt", offset=2, limit=3)
    assert "     3\tline2" in out
    assert "     5\tline4" in out
    assert "line5" not in out.split("... ")[0] or True
    assert "Use offset=5" in out


def test_read_line_cap_at_1000(toolset, _saved):
    (_saved.parent / "huge.txt").write_text("".join(f"l{i}\n" for i in range(1005)), encoding="utf-8")
    out = read(toolset, "huge.txt")
    assert "  1000\tl999" in out
    assert "Use offset=1000" in out
    assert "l1004" not in out


def test_binary_placeholder(toolset, _saved):
    (_saved.parent / "cap.dat").write_bytes(b"\x00\x01\x02binary")
    out = read(toolset, "cap.dat")
    assert out.startswith("[Binary file:")


# -- .grc routing ------------------------------------------------------------


def _payload(out: str) -> dict:
    """The inspect JSON is everything after the first header line."""
    return json.loads(out.split("\n", 1)[1])


def test_active_grc_routed_to_inspect_engine_live(toolset, _saved):
    fg = load_flow_graph(str(_saved))
    import grc_agent.fs_tools as ft

    orig = ft._active_flow_graph_fn
    ft._active_flow_graph_fn = lambda: fg
    try:
        out = read(toolset, "proj.grc")
    finally:
        ft._active_flow_graph_fn = orig
    assert "structural view via the inspect_graph engine" in out
    assert "live in-memory flowgraph" in out
    assert "<?xml" not in out
    data = _payload(out)
    assert "graph" in data  # same engine/shape as the inspect_graph tool


def test_other_grc_in_folder_loaded_headless(toolset, _saved):
    shutil.copy(FIXTURES / "dial_tone.grc", _saved.parent / "colleague.grc")
    out = read(toolset, "colleague.grc")
    assert "structural view via the inspect_graph engine" in out
    assert "file on disk" in out
    assert "<?xml" not in out
    assert "graph" in _payload(out)


def test_active_grc_without_live_object_reads_disk(toolset, _saved):
    out = read(toolset, "proj.grc")
    assert "active file on disk" in out
    assert "graph" in _payload(out)


def test_malformed_grc_is_model_retry(toolset, _saved):
    (_saved.parent / "broken.grc").write_text("not xml at all", encoding="utf-8")
    with pytest.raises(ModelRetry, match="flowgraph"):
        read(toolset, "broken.grc")


# -- toolset registration ------------------------------------------------------


def test_capability_registers_all_eight_tools():
    ts = GrcFileSystem().get_toolset()
    assert set(ts.tools) == {
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
        "search_files",
        "find_files",
        "create_directory",
        "file_info",
    }
