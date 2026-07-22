import hashlib
import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

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


# ---- Undo snapshot stack (append-only) ----
#
# GRC's native StateCache (gnuradio.grc.gui) owns the in-session undo/redo
# the user actually interacts with via Ctrl+Z/Y. This module is kept only for
# the snapshot-push side effect during change_graph() and manual canvas saves
# — a durable record of tracked edits, one numbered .grc file per push,
# deduplicated by content hash. With the UI undo/redo buttons removed, there
# is no consumer of an index/cursor discipline, so this is now a plain
# append-only stack bounded by UNDO_MAX_DEPTH (oldest deleted by name).
UNDO_MAX_DEPTH = 50


def _undo_dir(target_path: Path) -> Path:
    # Per-filename (not per-directory, unlike backups/) since each target
    # keeps its own append-only stack.
    return target_path.parent / ".grc_agent" / (target_path.name + ".undo")


def _read_undo_cursor(undo_dir: Path) -> dict:
    """Read the push cursor. ``count`` is the monotonic next-file index
    (never decreases — pruning deletes oldest files but does not renumber),
    and ``hash`` is the last pushed snapshot's content hash for dedup."""
    try:
        return json.loads((undo_dir / "cursor.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"count": 0, "hash": None}


def _write_undo_cursor(undo_dir: Path, cursor: dict) -> None:
    (undo_dir / "cursor.json").write_text(json.dumps(cursor), encoding="utf-8")


def _prune_undo_stack(undo_dir: Path, count: int) -> None:
    """Bound depth: when ``count`` (next-file index) exceeds UNDO_MAX_DEPTH,
    delete the oldest files by name. No renumbering — file indices are
    monotonic, so the oldest are simply the lowest-numbered beyond the cap."""
    excess = count - UNDO_MAX_DEPTH
    if excess <= 0:
        return
    for i in range(excess):
        (undo_dir / f"{i:05d}.grc").unlink(missing_ok=True)


def push_undo_snapshot(flow_graph: Any, target_path: Path, initial_data: dict | None = None) -> None:
    """Push the CURRENT (post-edit) state of flow_graph onto target_path's
    undo stack. Called from both change_graph's own success path and
    native_canvas.py's manual drag-save path, so both mutation sources share
    one history. Deduplicates against a genuinely-unchanged state (e.g. a
    selection click that didn't move anything) via content hash. Best-effort:
    a failure here must never fail the caller's own already-committed save,
    so exceptions are logged, not raised.

    `initial_data` — change_graph's own pre-mutation
    flow_graph.export_data(), taken before any phase runs — seeds a
    baseline entry the first time this is ever called for a file, so the
    stack records "before the first tracked edit," not just the edits
    themselves. Manual canvas saves have no pre-edit snapshot of their own
    to offer (the in-memory graph is already post-edit by the time
    _do_trigger_reload runs); they rely on a prior change_graph call (or an
    earlier canvas save) having already seeded the baseline.
    """
    from grc_agent.adapter.graph import _atomic_write_text, _serialize_flow_graph

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
            _atomic_write_text(baseline_payload, undo_dir / "00000.grc")
            cursor = {"count": 1, "hash": baseline_hash}

        if current_hash == cursor["hash"]:
            return  # nothing actually changed since the last tracked state

        new_index = cursor["count"]
        _atomic_write_text(current_payload, undo_dir / f"{new_index:05d}.grc")
        cursor = {"count": new_index + 1, "hash": current_hash}
        _prune_undo_stack(undo_dir, cursor["count"])
        _write_undo_cursor(undo_dir, cursor)
    except Exception as e:
        _log.warning("Failed to push undo snapshot for %s: %s", target_path, e, exc_info=True)
