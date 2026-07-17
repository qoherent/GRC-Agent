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
# Both change_graph() (agent edits) and native_canvas.py's manual drag-save
# path write to the SAME target .grc file from the same process (single-thread
# via gbulb), so the undo/redo history lives on disk for persistence across
# sessions. Mirrors GRC's own native undo (gnuradio.grc.gui's
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
    native_canvas.py's manual drag-save path, so both mutation sources share
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
