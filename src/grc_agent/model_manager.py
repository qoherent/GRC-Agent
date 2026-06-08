"""Read-only model discovery and system-spec probes for the GUI/CLI.

This module owns two independent concerns used by the model-selector UI
and the ``grc-agent model`` subcommands:

1. :func:`discover_cached_models` — scan the local Hugging Face cache
   (and an optional user-configured models directory) for ``.gguf`` files
   that can be loaded by llama.cpp. Pure filesystem walk, no model
   download. Skips in-progress downloads (``.downloadInProgress`` files
   and incomplete snapshot symlinks).
2. :func:`list_system_specs` — probe the local machine for VRAM/GPU,
   RAM, and CPU. Probes run once at GUI startup (cached for the
   session). Probes are deliberately cross-platform but conservative:
   if a probe is not available, the field is ``None`` and the caller
   renders "unknown" rather than guessing.

The mutating counterpart (:meth:`LlamaServerLauncher.swap_model`) lives
in :mod:`grc_agent.llama_launcher` and is added in Phase 3 of the
model-selector rollout.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# Hugging Face hub layout: ~/.cache/huggingface/hub/models--<org>--<repo>/
# Each repo dir contains snapshots/<rev>/<files>. The symlink "refs/main"
# points to the latest snapshot revision.
_HF_HUB_DEFAULT = Path.home() / ".cache" / "huggingface" / "hub"
_HF_REPO_DIR_RE = re.compile(r"^models--(?P<org>[^/]+)--(?P<repo>[^/]+)$")


@dataclass(frozen=True)
class CachedModel:
    """A ``.gguf`` file the local llama.cpp runtime can load.

    The fields are populated by :func:`discover_cached_models`. The
    ``hf_repo`` and ``filename`` together form the canonical
    ``hf_model`` token that the launcher passes to ``llama-server -hf``
    (i.e. ``"<hf_repo>:<filename>"``).
    """

    hf_repo: str  # e.g. "unsloth/Qwen3.5-2B-GGUF"
    filename: str  # e.g. "Qwen3.5-2B-UD-Q4_K_XL.gguf"
    snapshot_path: Path  # absolute path to the .gguf file
    size_bytes: int
    last_used: datetime | None  # file mtime in UTC; None if stat failed

    @property
    def hf_model_token(self) -> str:
        """Return the ``"<repo>:<filename>"`` token used by llama-server."""
        return f"{self.hf_repo}:{self.filename}"

    @property
    def display_name(self) -> str:
        """Return a short label suitable for a dropdown row."""
        return self.filename


@dataclass(frozen=True)
class SystemSpecs:
    """Snapshot of the local machine's accelerator and memory.

    Every field is ``None`` when its probe did not produce a result.
    The GUI and CLI render ``None`` as "unknown" so a partial probe on
    macOS, AMD-only Linux, or a CI sandbox is still usable.
    """

    gpu_name: str | None
    gpu_vram_bytes: int | None
    ram_bytes: int | None
    cpu_name: str | None
    cpu_cores_logical: int | None


def _is_mmproj_file(filename: str) -> bool:
    """Return True if ``filename`` is a multimodal projector weight.

    These files accompany vision-capable models (e.g. Llama 3.2 Vision,
    LLaVA, Qwen-VL) and are loaded alongside the chat model via the
    ``--mmproj`` flag. Our launcher passes ``--no-mmproj`` and never
    wants to load a mmproj file as a chat model, so the dropdown
    filters them out.
    """
    lowered = filename.lower()
    return (
        lowered.startswith("mmproj")
        or lowered.startswith("proj-")
        or "-mmproj-" in lowered
        or lowered.endswith("-mmproj.gguf")
    )


def _is_complete_snapshot_dir(snapshot_dir: Path) -> bool:
    """Return True if ``snapshot_dir`` is a real, complete HF snapshot.

    A snapshot is considered incomplete if it contains any
    ``.downloadInProgress`` file or any ``.incomplete`` marker. We do
    not try to validate that every blob is fully present (HF stores
    them under ``blobs/`` and the snapshot symlinks reference them);
    we only skip snapshots that are obviously mid-download.
    """
    try:
        entries = list(snapshot_dir.iterdir())
    except OSError:
        return False
    for entry in entries:
        if entry.name.endswith(".downloadInProgress") or entry.name.endswith(
            ".incomplete"
        ):
            return False
    return True


def _iter_hf_gguf_files(repo_dir: Path) -> Iterable[Path]:
    """Yield absolute paths to ``.gguf`` files in any snapshot under ``repo_dir``."""
    snapshots_root = repo_dir / "snapshots"
    if not snapshots_root.is_dir():
        return
    try:
        snapshot_dirs = [p for p in snapshots_root.iterdir() if p.is_dir()]
    except OSError as exc:
        logger.debug("discover: iterdir failed on %s: %s", snapshots_root, exc)
        return
    for snapshot_dir in snapshot_dirs:
        if not _is_complete_snapshot_dir(snapshot_dir):
            continue
        try:
            for entry in snapshot_dir.iterdir():
                # HF stores snapshot files as symlinks into ``blobs/``.
                # We do NOT resolve() them: the symlink path is what
                # carries the original GGUF filename, which is what the
                # dropdown displays and what the launcher passes as
                # ``hf_model_token``. Resolving the symlink would
                # replace the friendly filename with a content hash.
                if (
                    entry.is_file() or entry.is_symlink()
                ) and entry.suffix.lower() == ".gguf":
                    if _is_mmproj_file(entry.name):
                        # mmproj-* files are multimodal projector weights,
                        # not chat models. The launcher passes
                        # ``--no-mmproj`` (llama_launcher.py) and would
                        # fail to load one as a chat model.
                        continue
                    yield entry
        except OSError as exc:
            logger.debug("discover: iterdir failed on %s: %s", snapshot_dir, exc)


def _iter_models_dir_gguf_files(models_dir: Path) -> Iterable[Path]:
    """Yield ``.gguf`` files directly under ``models_dir`` (non-recursive).

    Hand-placed model directories are typically flat (``~/models/foo.gguf``)
    rather than HF-shaped. Recursing into arbitrary user directories is
    out of scope for Phase 1; users with nested layouts can set
    ``model_path`` explicitly to load such a model even if it does not
    show up in the dropdown.
    """
    try:
        entries = list(models_dir.iterdir())
    except OSError as exc:
        logger.debug("discover: iterdir failed on %s: %s", models_dir, exc)
        return
    for entry in entries:
        if (
            entry.is_file()
            and entry.suffix.lower() == ".gguf"
            and not _is_mmproj_file(entry.name)
        ):
            yield entry.resolve()


def _read_refs_main(repo_dir: Path) -> str | None:
    """Return the latest revision from ``refs/main`` if it exists.

    Falls back to the most recently-modified snapshot directory. Returns
    ``None`` when the repo has no snapshots at all.
    """
    refs_main = repo_dir / "refs" / "main"
    if refs_main.is_file():
        try:
            return refs_main.read_text(encoding="utf-8").strip() or None
        except OSError:
            pass
    snapshots_root = repo_dir / "snapshots"
    if not snapshots_root.is_dir():
        return None
    try:
        candidates = [p for p in snapshots_root.iterdir() if p.is_dir()]
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0].name


def _cached_model_from_snapshot(
    snapshot_file: Path, repo_dir: Path
) -> CachedModel | None:
    """Build a :class:`CachedModel` from a single ``.gguf`` snapshot file."""
    match = _HF_REPO_DIR_RE.match(repo_dir.name)
    if match is None:
        return None
    hf_repo = f"{match.group('org')}/{match.group('repo')}"
    try:
        stat_result = snapshot_file.stat()
    except OSError as exc:
        logger.debug("discover: stat failed on %s: %s", snapshot_file, exc)
        return None
    return CachedModel(
        hf_repo=hf_repo,
        filename=snapshot_file.name,
        snapshot_path=snapshot_file,
        size_bytes=stat_result.st_size,
        last_used=datetime.fromtimestamp(
            stat_result.st_mtime, tz=UTC
        ),
    )


def _cached_model_from_models_dir(
    gguf_path: Path, hf_repo_label: str
) -> CachedModel | None:
    """Build a :class:`CachedModel` for a hand-placed ``.gguf`` file.

    ``hf_repo_label`` is a synthetic ``org/repo`` string used so the
    dropdown can group local files. We pick ``local/<models_dir_name>``
    by convention; it is never resolved against the Hugging Face API.
    """
    try:
        stat_result = gguf_path.stat()
    except OSError as exc:
        logger.debug("discover: stat failed on %s: %s", gguf_path, exc)
        return None
    return CachedModel(
        hf_repo=hf_repo_label,
        filename=gguf_path.name,
        snapshot_path=gguf_path,
        size_bytes=stat_result.st_size,
        last_used=datetime.fromtimestamp(
            stat_result.st_mtime, tz=UTC
        ),
    )


def discover_cached_models(
    *,
    hf_cache: Path | None = None,
    models_dir: Path | None = None,
) -> list[CachedModel]:
    """Return a sorted list of every ``.gguf`` the runtime can load.

    The list is sorted by ``hf_repo`` then ``filename`` so the dropdown
    ordering is deterministic. ``hf_cache`` defaults to
    ``~/.cache/huggingface/hub/``; ``models_dir`` is opt-in (driven by
    ``[llama].models_dir``).

    The function never raises. Missing or unreadable directories yield
    an empty result for that source while still returning whatever the
    other source produced, so a partial install does not blank the
    dropdown.
    """
    cache_root = hf_cache if hf_cache is not None else _HF_HUB_DEFAULT
    models: dict[tuple[str, str], CachedModel] = {}

    if cache_root.is_dir():
        try:
            repo_dirs = [p for p in cache_root.iterdir() if p.is_dir()]
        except OSError as exc:
            logger.debug("discover: iterdir failed on %s: %s", cache_root, exc)
            repo_dirs = []
        for repo_dir in repo_dirs:
            latest_rev = _read_refs_main(repo_dir)
            if latest_rev is None:
                continue
            for gguf in _iter_hf_gguf_files(repo_dir):
                model = _cached_model_from_snapshot(gguf, repo_dir)
                if model is not None:
                    key = (model.hf_repo, model.filename)
                    # Dedupe: if multiple snapshots expose the same
                    # filename, keep the first one encountered. The
                    # dropdown is for selection, not for inspecting
                    # revision history; the launcher reads the
                    # canonical path under ``refs/main`` when it
                    # actually loads the model.
                    if key in models:
                        continue
                    models[key] = model

    if models_dir is not None and models_dir.is_dir():
        label = f"local/{models_dir.name}" if models_dir.name else "local"
        for gguf in _iter_models_dir_gguf_files(models_dir):
            model = _cached_model_from_models_dir(gguf, label)
            if model is not None:
                key = (model.hf_repo, model.filename)
                if key not in models:
                    models[key] = model

    return sorted(
        models.values(), key=lambda m: (m.hf_repo.lower(), m.filename.lower())
    )


def _probe_nvidia_smi() -> tuple[str | None, int | None]:
    """Return (gpu_name, total_vram_bytes) from nvidia-smi, or (None, None).

    ``nvidia-smi --query-gpu=name,memory.total --format=csv,noheader`` is
    the only probe that gives both fields in one call. We only need the
    primary GPU; multi-GPU sums are out of scope for Phase 1.
    """
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None, None
    if result.returncode != 0 or not result.stdout.strip():
        return None, None
    first_line = result.stdout.strip().splitlines()[0]
    parts = [p.strip() for p in first_line.split(",")]
    if len(parts) < 2:
        return None, None
    name, mem_mib = parts[0], parts[1]
    try:
        vram_bytes = int(mem_mib) * 1024 * 1024
    except ValueError:
        return name, None
    return name, vram_bytes


def _probe_meminfo() -> int | None:
    """Return total RAM in bytes from ``/proc/meminfo`` (Linux only)."""
    meminfo = Path("/proc/meminfo")
    if not meminfo.is_file():
        return None
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    # MemTotal is reported in kB.
                    return int(parts[1]) * 1024
    except OSError:
        return None
    return None


def _probe_cpuinfo() -> tuple[str | None, int | None]:
    """Return (cpu_model, logical_cores) from ``/proc/cpuinfo`` (Linux only)."""
    cpuinfo = Path("/proc/cpuinfo")
    if not cpuinfo.is_file():
        return None, None
    try:
        text = cpuinfo.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    cpu_name: str | None = None
    processor_count = 0
    for line in text.splitlines():
        if line.startswith("model name") and cpu_name is None:
            _, _, value = line.partition(":")
            cpu_name = value.strip() or None
        elif line.startswith("processor"):
            processor_count += 1
    return cpu_name, (processor_count if processor_count else None)


def list_system_specs() -> SystemSpecs:
    """Probe the local machine for VRAM/GPU, RAM, and CPU.

    The probes are deliberately conservative: any platform where a
    probe is not available returns ``None`` for that field. The GUI
    renders ``None`` as "unknown" so partial results are still useful.
    """
    gpu_name, vram_bytes = _probe_nvidia_smi()
    ram_bytes = _probe_meminfo()
    cpu_name, cpu_cores = _probe_cpuinfo()
    return SystemSpecs(
        gpu_name=gpu_name,
        gpu_vram_bytes=vram_bytes,
        ram_bytes=ram_bytes,
        cpu_name=cpu_name,
        cpu_cores_logical=cpu_cores,
    )


def cached_model_to_dict(model: CachedModel) -> dict[str, object]:
    """JSON-serializable view of a :class:`CachedModel` for the CLI's --json output."""
    return {
        "hf_repo": model.hf_repo,
        "filename": model.filename,
        "hf_model_token": model.hf_model_token,
        "size_bytes": model.size_bytes,
        "last_used": (
            model.last_used.isoformat() if model.last_used is not None else None
        ),
    }


def system_specs_to_dict(specs: SystemSpecs) -> dict[str, object]:
    """JSON-serializable view of a :class:`SystemSpecs` for the CLI's --json output."""
    return {
        "gpu_name": specs.gpu_name,
        "gpu_vram_bytes": specs.gpu_vram_bytes,
        "ram_bytes": specs.ram_bytes,
        "cpu_name": specs.cpu_name,
        "cpu_cores_logical": specs.cpu_cores_logical,
    }


def system_specs_to_json(specs: SystemSpecs) -> str:
    """Return a compact JSON string of the system specs."""
    return json.dumps(system_specs_to_dict(specs), sort_keys=True)


__all__ = [
    "CachedModel",
    "SystemSpecs",
    "cached_model_to_dict",
    "discover_cached_models",
    "list_system_specs",
    "system_specs_to_dict",
    "system_specs_to_json",
]
