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
    assert "line5" not in out  # pages 2-4 only; line5 lives on the next page
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


def _assert_no_raw_flowgraph_source(out: str) -> None:
    """The fixtures are YAML-format GRC files, so `<?xml` proves nothing.
    Assert against markers that appear in the RAW fixture source but cannot
    appear in the structural JSON: bare YAML keys (`options:`, `blocks:`,
    `connections:` — the JSON quotes its keys, so the literal `key:` form
    never occurs) and the YAML block-id header (`- id:`)."""
    for marker in ("options:", "blocks:", "connections:", "- id:"):
        assert marker not in out, f"raw flowgraph source leaked (found {marker!r})"
    assert json.loads(out.split("\n", 1)[1])["graph"]  # valid structural JSON


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
    _assert_no_raw_flowgraph_source(out)


def test_active_grc_prefers_live_object_over_disk(toolset, _saved):
    """Discriminate live vs disk: corrupt the disk copy AFTER loading the
    live object — a live read still inspects; a disk read would fail to parse."""
    fg = load_flow_graph(str(_saved))
    import grc_agent.fs_tools as ft

    orig = ft._active_flow_graph_fn
    ft._active_flow_graph_fn = lambda: fg
    try:
        _saved.write_text("}}} corrupted yaml {{{", encoding="utf-8")
        out = read(toolset, "proj.grc")
    finally:
        ft._active_flow_graph_fn = orig
    assert "live in-memory flowgraph" in out
    assert "graph" in _payload(out)  # parsed fine — the disk copy was never read


def test_other_grc_in_folder_loaded_headless(toolset, _saved):
    shutil.copy(FIXTURES / "dial_tone.grc", _saved.parent / "colleague.grc")
    out = read(toolset, "colleague.grc")
    assert "structural view via the inspect_graph engine" in out
    assert "file on disk" in out
    _assert_no_raw_flowgraph_source(out)


def test_active_grc_without_live_object_reads_disk(toolset, _saved):
    out = read(toolset, "proj.grc")
    assert "active file on disk" in out
    assert "graph" in _payload(out)


def test_malformed_grc_is_model_retry(toolset, _saved):
    (_saved.parent / "broken.grc").write_text("not xml at all", encoding="utf-8")
    with pytest.raises(ModelRetry, match="flowgraph"):
        read(toolset, "broken.grc")


# -- audit fixes: name-rule coverage, nested denies, symlink write bypass ------


def test_uppercase_GRC_routed_not_raw(toolset, _saved):
    shutil.copy(FIXTURES / "dial_tone.grc", _saved.parent / "UPPER.GRC")
    out = read(toolset, "UPPER.GRC")
    assert "structural view via the inspect_graph engine" in out
    _assert_no_raw_flowgraph_source(out)


def test_grc_backup_suffix_routed_not_raw(toolset, _saved):
    shutil.copy(FIXTURES / "dial_tone.grc", _saved.parent / "backup.grc~")
    out = read(toolset, "backup.grc~")
    assert "structural view via the inspect_graph engine" in out
    _assert_no_raw_flowgraph_source(out)


def test_nested_env_denied(toolset, _saved):
    pkg = _saved.parent / "pkg"
    pkg.mkdir()
    (pkg / ".env").write_text("SECRET=2\n", encoding="utf-8")
    with pytest.raises(ModelRetry, match="denied"):
        read(toolset, "pkg/.env")


def test_git_config_denied_read(toolset, _saved):
    git_dir = _saved.parent / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[remote]\n", encoding="utf-8")
    with pytest.raises(ModelRetry, match="denied"):
        read(toolset, ".git/config")


def test_walkers_do_not_surface_env_or_git(toolset, _saved):
    root = _saved.parent
    (root / ".env").write_text("X=1\n", encoding="utf-8")
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / ".env").write_text("X=2\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    listing = run(toolset.list_directory("."))
    assert ".env" not in listing and "pkg/" in listing
    pkg_listing = run(toolset.list_directory("pkg"))
    assert ".env" not in pkg_listing
    found = run(toolset.find_files("*.env"))
    assert found == "No matches found."


def test_symlink_alias_to_grc_write_bypass_denied(toolset, _saved):
    """write/edit via an in-root symlink naming a .grc target must be rejected
    — the resolved target's name is checked too (adversarial audit repro)."""
    import os

    root = _saved.parent
    target = root / "real.grc"
    shutil.copy(FIXTURES / "dial_tone.grc", target)
    alias = root / "alias.py"
    os.symlink(target, alias)
    with pytest.raises(ModelRetry, match="change_graph"):
        write(toolset, "alias.py", "overwritten")
    with pytest.raises(ModelRetry, match="change_graph"):
        edit(toolset, "alias.py", "options:", "x")
    assert target.read_text(encoding="utf-8") != "overwritten"
    # And an alias naming the ACTIVE graph is likewise protected
    active_alias = root / "active_alias.py"
    os.symlink(_saved, active_alias)
    with pytest.raises(ModelRetry, match="change_graph"):
        write(toolset, "active_alias.py", "pwned")
    assert "pwned" not in _saved.read_text(encoding="utf-8")


# -- write_file -------------------------------------------------------------


def write(toolset, path, content, **kwargs):
    return run(toolset.write_file(path, content, **kwargs))


def test_write_allowed_suffix_atomic(toolset, _saved):
    out = write(toolset, "helper.py", "print('hi')\n")
    assert "Wrote" in out and "hash:" in out
    assert (_saved.parent / "helper.py").read_text(encoding="utf-8") == "print('hi')\n"
    # atomic replace leaves no temp strays
    assert [p.name for p in _saved.parent.iterdir() if p.name.startswith("helper.py.")] == []


def test_write_overwrite_existing(toolset, _saved):
    f = _saved.parent / "helper.py"
    f.write_text("old", encoding="utf-8")
    write(toolset, "helper.py", "new")
    assert f.read_text(encoding="utf-8") == "new"


def test_write_grc_rejected_points_to_change_graph(toolset, _saved):
    with pytest.raises(ModelRetry, match="change_graph"):
        write(toolset, "evil.grc", "<?xml?>")


def test_write_unknown_suffix_rejected_lists_allowed(toolset, _saved):
    with pytest.raises(ModelRetry, match="Allowed extensions"):
        write(toolset, "run.sh", "echo hi\n")


def test_write_no_extension_rejected(toolset, _saved):
    with pytest.raises(ModelRetry, match="without an extension"):
        write(toolset, "Makefile", "all:\n")


def test_write_suffix_case_insensitive(toolset, _saved):
    out = write(toolset, "script.PY", "x = 1\n")
    assert "Wrote" in out
    assert (_saved.parent / "script.PY").exists()


def test_write_missing_parent_requires_create_directory(toolset, _saved):
    with pytest.raises(ModelRetry, match="create_directory"):
        write(toolset, "scripts/helper.py", "x = 1\n")


def test_write_stale_hash_conflict(toolset, _saved):
    f = _saved.parent / "helper.py"
    f.write_text("current", encoding="utf-8")
    with pytest.raises(ModelRetry, match="Conflict"):
        write(toolset, "helper.py", "new", expected_hash="deadbeefdead")
    assert f.read_text(encoding="utf-8") == "current"  # unchanged


def test_write_matching_hash_succeeds(toolset, _saved):
    f = _saved.parent / "helper.py"
    f.write_text("current", encoding="utf-8")
    import hashlib

    good = hashlib.sha256(b"current").hexdigest()[:12]
    out = write(toolset, "helper.py", "new", expected_hash=good)
    assert "Wrote" in out


def test_write_env_denied(toolset, _saved):
    # `.env` has no extension, so the suffix allowlist rejects it before the
    # deny pattern is even consulted — either way it can never be written.
    with pytest.raises(ModelRetry, match="not allowed"):
        write(toolset, ".env", "X=1")


def test_write_protected_secrets_rejected(toolset, _saved):
    # `.pem`/`.key` are already rejected by the suffix allowlist; secrets.py
    # has an ALLOWED suffix but matches the harness-protected '**/secrets*'.
    with pytest.raises(ModelRetry, match="protected"):
        write(toolset, "secrets.py", "API_KEY = 'x'")


def test_write_gated_when_unsaved(toolset):
    with pytest.raises(ModelRetry, match="save the flowgraph"):
        write(toolset, "helper.py", "x")


# -- edit_file ----------------------------------------------------------------


def edit(toolset, path, old, new, **kwargs):
    return run(toolset.edit_file(path, old, new, **kwargs))


def test_edit_exact_replacement_atomic(toolset, _saved):
    f = _saved.parent / "helper.py"
    f.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    out = edit(toolset, "helper.py", "b = 2", "b = 42")
    assert "Edited helper.py" in out and "hash:" in out
    assert f.read_text(encoding="utf-8") == "a = 1\nb = 42\nc = 3\n"
    assert [p.name for p in _saved.parent.iterdir() if p.name.startswith("helper.py.")] == []


def test_edit_old_text_not_found(toolset, _saved):
    (_saved.parent / "helper.py").write_text("a = 1\n", encoding="utf-8")
    with pytest.raises(ModelRetry, match="not found"):
        edit(toolset, "helper.py", "zzz", "y")


def test_edit_ambiguous_old_text(toolset, _saved):
    (_saved.parent / "helper.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    with pytest.raises(ModelRetry, match="2 times"):
        edit(toolset, "helper.py", "x = 1", "y")


def test_edit_grc_rejected(toolset, _saved):
    with pytest.raises(ModelRetry, match="change_graph"):
        edit(toolset, "proj.grc", "<block>", "<block>")


def test_edit_unknown_suffix_rejected(toolset, _saved):
    (_saved.parent / "run.sh").write_text("echo hi\n", encoding="utf-8")
    with pytest.raises(ModelRetry, match="Allowed extensions"):
        edit(toolset, "run.sh", "hi", "bye")


def test_edit_missing_file(toolset, _saved):
    with pytest.raises(ModelRetry, match="File not found"):
        edit(toolset, "ghost.py", "a", "b")


def test_edit_stale_hash_conflict(toolset, _saved):
    f = _saved.parent / "helper.py"
    f.write_text("current", encoding="utf-8")
    with pytest.raises(ModelRetry, match="Conflict"):
        edit(toolset, "helper.py", "current", "new", expected_hash="badbadbadbad")
    assert f.read_text(encoding="utf-8") == "current"


def test_edit_cannot_create_files(toolset, _saved):
    # edit_file requires an existing file with exactly one old_text match
    with pytest.raises(ModelRetry, match="File not found"):
        edit(toolset, "brand_new.py", "", "content")


# -- inherited tools: list_directory / search_files / find_files -------------


def test_list_directory_entries_and_dotfile_skipping(toolset, _saved):
    root = _saved.parent
    (root / "a.py").write_text("x", encoding="utf-8")
    (root / "sub").mkdir()
    (root / ".hidden").write_text("x", encoding="utf-8")
    out = run(toolset.list_directory("."))
    assert "a.py" in out and "(1 bytes)" in out
    assert "sub/" in out
    assert ".hidden" not in out
    assert "proj.grc" in out  # listings still SEE .grc files


def test_list_directory_cap_at_200(toolset, _saved):
    root = _saved.parent
    for i in range(205):
        (root / f"f{i:03}.txt").write_text("x", encoding="utf-8")
    out = run(toolset.list_directory("."))
    assert "[... truncated at 200 entries]" in out
    assert "f204" not in out  # sorted order: the tail was dropped


def test_list_directory_gated_when_unsaved(toolset):
    with pytest.raises(ModelRetry, match="save the flowgraph"):
        run(toolset.list_directory("."))


def test_search_files_greps_contents_including_grc(toolset, _saved):
    root = _saved.parent
    (root / "helper.py").write_text("samp_rate = 32000\n", encoding="utf-8")
    out = run(toolset.search_files("32000"))
    assert "helper.py:1:samp_rate = 32000" in out
    # .grc XML is greppable (decision: allow — writes are what's forbidden)
    assert "proj.grc:" in out


def test_search_files_include_glob(toolset, _saved):
    root = _saved.parent
    (root / "a.py").write_text("needle\n", encoding="utf-8")
    (root / "b.txt").write_text("needle\n", encoding="utf-8")
    out = run(toolset.search_files("needle", include_glob="*.py"))
    assert "a.py:1" in out and "b.txt" not in out


def test_find_files_glob(toolset, _saved):
    root = _saved.parent
    (root / "x.py").write_text("", encoding="utf-8")
    out = run(toolset.find_files("*.py"))
    assert "x.py" in out
    out_grc = run(toolset.find_files("*.grc"))
    assert "proj.grc" in out_grc


def test_find_files_absolute_pattern_rejected(toolset, _saved):
    with pytest.raises(ModelRetry, match="must be relative"):
        run(toolset.find_files("/etc/*.py"))


# -- create_directory / file_info ---------------------------------------------


def test_create_directory_with_parents(toolset, _saved):
    out = run(toolset.create_directory("scripts/utils"))
    assert "Created directory" in out
    assert (_saved.parent / "scripts" / "utils").is_dir()


def test_create_directory_gated_when_unsaved(toolset):
    with pytest.raises(ModelRetry, match="save the flowgraph"):
        run(toolset.create_directory("x"))


def test_file_info_hash_matches_read_header(toolset, _saved):
    f = _saved.parent / "helper.py"
    f.write_text("a\nb\n", encoding="utf-8")
    info = run(toolset.file_info("helper.py"))
    read_out = read(toolset, "helper.py")
    info_hash = next(ln.split(": ")[1] for ln in info.splitlines() if ln.startswith("hash:"))
    assert f"hash:{info_hash}" in read_out
    assert "lines: 2" in info
    assert "type: file" in info


def test_file_info_grc_still_metadata_only(toolset, _saved):
    info = run(toolset.file_info("proj.grc"))
    assert "type: file" in info
    assert "binary: False" in info  # XML is text; no content is dumped either way


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
