import fcntl
import hashlib
import json
from pathlib import Path
from typing import Any

MAX_BACKUPS_PER_DIR = 50


def _prune_old_backups(backup_dir: Path) -> None:
    """Every save snapshots the previous file into backup_dir with no
    pruning at all — left alone, this grows without bound over the life of
    a project. Backup filenames encode a timestamp
    (`{timestamp}-{hash}{suffix}`), so the oldest N beyond the cap are the
    ones to go; best-effort, a failure here shouldn't fail the save itself."""
    try:
        backups = sorted(backup_dir.iterdir(), key=lambda p: p.name)
        excess = len(backups) - MAX_BACKUPS_PER_DIR
        for old in backups[: max(0, excess)]:
            old.unlink(missing_ok=True)
    except Exception:
        pass


# ---- Undo/Redo: a shared, disk-based snapshot stack ----
#
# Both change_graph() (this process) and canvas_app.py's manual drag-save
# path write to the SAME target .grc file, from two separate processes —
# so the undo/redo history has to live on disk too, not as an in-memory
# stack in either process. Mirrors GRC's own native undo (gnuradio.grc.gui's
# StateCache), which is also just export_data()/import_data() snapshots —
# independent confirmation this is the right shape, not just a plausible
# analogy. Each snapshot is a plain numbered .grc file (same serialization
# as a real save), so restoring one is exactly write_flow_graph_atomic's
# own atomic-replace, and reading one back is exactly load_flow_graph.
UNDO_MAX_DEPTH = 50


def _undo_dir(target_path: Path) -> Path:
    # Per-filename (not per-directory, unlike backups/) since this needs a
    # stateful cursor, not just a pile of independently-timestamped files.
    return target_path.parent / ".grc_agent" / (target_path.name + ".undo")


def _read_undo_cursor(undo_dir: Path) -> dict:
    try:
        return json.loads((undo_dir / "cursor.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"index": -1, "count": 0, "hash": None}


def _write_undo_cursor(undo_dir: Path, cursor: dict) -> None:
    (undo_dir / "cursor.json").write_text(json.dumps(cursor), encoding="utf-8")


def _prune_undo_stack(undo_dir: Path, cursor: dict) -> None:
    """Bound depth like _prune_old_backups — but snapshots are addressed by
    contiguous 0-based index (not an independently-sortable timestamp), so
    the oldest ones must be deleted AND everything else renumbered down, or
    the index<->filename mapping breaks. Mutates `cursor` in place."""
    excess = cursor["count"] - UNDO_MAX_DEPTH
    if excess <= 0:
        return
    for i in range(excess):
        (undo_dir / f"{i:05d}.grc").unlink(missing_ok=True)
    for old_index in range(excess, cursor["count"]):
        old_path = undo_dir / f"{old_index:05d}.grc"
        if old_path.exists():
            old_path.rename(undo_dir / f"{old_index - excess:05d}.grc")
    cursor["index"] -= excess
    cursor["count"] -= excess


def push_undo_snapshot(flow_graph: Any, target_path: Path, initial_data: dict | None = None) -> None:
    """Push the CURRENT (post-edit) state of flow_graph onto target_path's
    undo stack. Called from both change_graph's own success path and
    canvas_app.py's manual drag-save path, so both mutation sources share
    one history. Deduplicates against a genuinely-unchanged state (e.g. a
    selection click that didn't move anything) via content hash. A new push
    after an undo discards the redo branch — standard undo/redo semantics.
    Best-effort: a failure here must never fail the caller's own
    already-committed save, so exceptions are logged, not raised.

    `initial_data` — change_graph's own pre-mutation
    flow_graph.export_data(), taken before any phase runs — seeds a
    baseline entry the first time this is ever called for a file, so undo
    can return all the way to "before the first tracked edit," not just
    between edits. Manual canvas saves have no pre-edit snapshot of their
    own to offer (the in-memory graph is already post-edit by the time
    _do_trigger_reload runs); they rely on a prior change_graph call (or an
    earlier canvas save) having already seeded the baseline.
    """
    from grc_agent.adapter.graph import _serialize_flow_graph

    try:
        target_path = Path(target_path)
        undo_dir = _undo_dir(target_path)
        undo_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        cursor = _read_undo_cursor(undo_dir)

        current_payload = _serialize_flow_graph(flow_graph)
        current_hash = hashlib.sha256(current_payload.encode()).hexdigest()

        if cursor["count"] == 0 and initial_data is not None:
            from gnuradio.grc.core.io import yaml as _grc_yaml

            baseline_payload = _grc_yaml.dump(initial_data)
            baseline_hash = hashlib.sha256(baseline_payload.encode()).hexdigest()
            (undo_dir / "00000.grc").write_text(baseline_payload, encoding="utf-8")
            cursor = {"index": 0, "count": 1, "hash": baseline_hash}

        if current_hash == cursor["hash"]:
            return  # nothing actually changed since the last tracked state

        for stale in undo_dir.glob("*.grc"):
            try:
                if int(stale.stem) > cursor["index"]:
                    stale.unlink()
            except ValueError:
                continue

        new_index = cursor["index"] + 1
        (undo_dir / f"{new_index:05d}.grc").write_text(current_payload, encoding="utf-8")
        cursor = {"index": new_index, "count": new_index + 1, "hash": current_hash}
        _prune_undo_stack(undo_dir, cursor)
        _write_undo_cursor(undo_dir, cursor)
    except Exception as e:
        print(f"[grc-agent] Failed to push undo snapshot for {target_path}: {e}")


def _apply_undo_redo_snapshot(
    target_path: Path, undo_dir: Path, cursor: dict, new_index: int
) -> dict:
    from grc_agent.adapter.graph import _atomic_write_text

    snapshot_path = undo_dir / f"{new_index:05d}.grc"
    if not snapshot_path.exists():
        return {"ok": False, "message": "Undo history is corrupted or incomplete."}
    payload = snapshot_path.read_text(encoding="utf-8")

    # Same lock file + atomic-replace convention as change_graph's own save
    # and canvas_app.py's drag-save — a reader can never observe a torn file
    # regardless of which of the three writers is active. The cursor
    # read-modify-write must be INSIDE this same critical section too (not
    # just the target-file write): push_undo_snapshot (canvas_app.py's
    # drag-save, and change_graph) always wraps its own cursor.json
    # read+write in this identical lock, so leaving this function's cursor
    # update unlocked let a concurrent drag-save read a stale cursor between
    # this function's file write and its cursor write, then both writers'
    # cursor.json updates race with whichever lands last silently winning.
    lock_path = target_path.parent / ".grc_agent" / (target_path.name + ".lock")
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            cursor = _read_undo_cursor(undo_dir)
            _atomic_write_text(payload, target_path)
            cursor["index"] = new_index
            cursor["hash"] = hashlib.sha256(payload.encode()).hexdigest()
            _write_undo_cursor(undo_dir, cursor)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    return {
        "ok": True,
        "can_undo": cursor["index"] > 0,
        "can_redo": cursor["index"] < cursor["count"] - 1,
    }


def undo_flowgraph(target_path: Path) -> dict:
    """Move target_path's undo cursor back one step and write that
    snapshot to disk. Does NOT touch any in-memory flow_graph object —
    callers (web.py's /grc/undo) reload from disk afterward, the same way
    grc_reload already does for a canvas-triggered disk change."""
    target_path = Path(target_path)
    undo_dir = _undo_dir(target_path)
    cursor = _read_undo_cursor(undo_dir)
    if cursor["index"] <= 0:
        return {"ok": False, "message": "Nothing to undo."}
    return _apply_undo_redo_snapshot(target_path, undo_dir, cursor, cursor["index"] - 1)


def redo_flowgraph(target_path: Path) -> dict:
    target_path = Path(target_path)
    undo_dir = _undo_dir(target_path)
    cursor = _read_undo_cursor(undo_dir)
    if cursor["index"] >= cursor["count"] - 1:
        return {"ok": False, "message": "Nothing to redo."}
    return _apply_undo_redo_snapshot(target_path, undo_dir, cursor, cursor["index"] + 1)


def undo_status(target_path: Path) -> dict:
    """Cheap read for /grc/status — no lock needed, just reflects the
    cursor file as it currently stands."""
    target_path = Path(target_path)
    undo_dir = _undo_dir(target_path)
    if not undo_dir.exists():
        return {"can_undo": False, "can_redo": False}
    cursor = _read_undo_cursor(undo_dir)
    return {
        "can_undo": cursor["index"] > 0,
        "can_redo": cursor["index"] < cursor["count"] - 1,
    }
