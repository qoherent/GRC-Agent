"""Provenance-checked reads from native GNU Radio Companion File Sinks."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

from grc_agent.adapter.graph import flow_graph_content_hash
from grc_agent.fs_tools import GrcFileSystemToolset

_SINK_DTYPES = {
    "complex": np.dtype("=c8"),
    "float": np.dtype("=f4"),
    "int": np.dtype("=i4"),
    "short": np.dtype("=i2"),
    "byte": np.dtype("=u1"),
}


@dataclass
class Observation:
    """Identity and format of one fresh File Sink output for a graph run."""

    run_id: int | None
    graph_hash: str
    graph_path: Path
    sink_name: str
    path: Path
    dtype: np.dtype
    vlen: int
    completed_file_identity: tuple[int, int, int, int, int] | None = None
    completion: Literal["natural", "stopped"] | None = None


def _evaluated(block: Any, param: str) -> Any:
    try:
        return block.params[param].evaluate()
    except Exception as exc:
        raise ValueError(f"Cannot evaluate File Sink parameter {param!r}: {exc}") from exc


def _resolved_graph_path(graph: Any, graph_path: str) -> Path:
    path = Path(graph_path).resolve()
    native_path = graph.grc_file_path
    if not native_path or Path(native_path).resolve() != path:
        raise ValueError("The supplied graph_path does not identify this saved GRC graph.")
    if not path.is_file():
        raise ValueError(f"The saved graph file does not exist: {path}")
    return path


def _enabled_file_sinks(graph: Any) -> list[Any]:
    return [block for block in graph.get_enabled_blocks() if block.key == "blocks_file_sink"]


def _output_path(value: Any, graph_path: Path, sink_name: str) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise ValueError(f"File Sink {sink_name!r} must have a non-empty output file path.")
    path = Path(value)
    if not path.is_absolute():
        path = graph_path.parent / path
    return Path(os.path.abspath(path))


def _check_output_path(
    graph: Any, sink: Any, sink_name: str, graph_path: Path
) -> Path:
    selected_path = _output_path(_evaluated(sink, "file"), graph_path, sink_name)
    for other_sink in _enabled_file_sinks(graph):
        if other_sink is sink:
            continue
        other_path = _output_path(_evaluated(other_sink, "file"), graph_path, other_sink.name)
        if other_path == selected_path:
            raise ValueError(
                f"Enabled File Sinks {sink_name!r} and {other_sink.name!r} target the same "
                f"output path: {selected_path}"
            )

    if os.path.lexists(selected_path):
        raise ValueError(
            f"File Sink output already exists (old artifacts are retained): {selected_path}"
        )
    try:
        safe_path = GrcFileSystemToolset()._safe_resolve(str(selected_path), write=True)
    except (OSError, ValueError) as exc:
        raise ValueError(f"File Sink output is outside the allowed project sandbox: {exc}") from exc
    if safe_path != selected_path:
        raise ValueError(f"File Sink output path traverses a symlink: {selected_path}")
    if os.path.lexists(safe_path):
        raise ValueError(
            f"File Sink output already exists (old artifacts are retained): {safe_path}"
        )
    if not safe_path.parent.is_dir():
        raise ValueError(f"File Sink output directory does not exist: {safe_path.parent}")
    return safe_path


def _sink_format(sink: Any, sink_name: str) -> tuple[np.dtype, int]:
    try:
        vlen = int(_evaluated(sink, "vlen"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"File Sink {sink_name!r} has an invalid vector length.") from exc
    if vlen <= 0:
        raise ValueError(f"File Sink {sink_name!r} vector length must be positive.")
    ports = list(sink.active_sinks)
    if len(ports) != 1:
        raise ValueError(
            f"File Sink {sink_name!r} must expose exactly one active input port; found {len(ports)}."
        )
    native_dtype = str(ports[0].dtype)
    try:
        dtype = _SINK_DTYPES[native_dtype]
    except KeyError as exc:
        raise ValueError(
            f"File Sink {sink_name!r} has unsupported native input type {native_dtype!r}."
        ) from exc
    return dtype, vlen


def prepare_observation(graph: Any, sink_name: str, graph_path: str) -> Observation:
    """Validate a native File Sink and its not-yet-created output path.

    The helper does not change the graph or filesystem. Its output path is
    relative to the saved graph directory when the native ``file`` value is
    relative.
    """
    resolved_graph = _resolved_graph_path(graph, graph_path)
    sinks = [block for block in _enabled_file_sinks(graph) if block.name == sink_name]
    if len(sinks) != 1:
        raise ValueError(
            f"Expected one enabled File Sink named {sink_name!r}; found {len(sinks)}."
        )
    sink = sinks[0]

    append = _evaluated(sink, "append")
    if append is not False:
        raise ValueError(f"File Sink {sink_name!r} must have append disabled.")

    safe_path = _check_output_path(graph, sink, sink_name, resolved_graph)
    dtype, vlen = _sink_format(sink, sink_name)

    return Observation(
        run_id=None,
        graph_hash=flow_graph_content_hash(graph),
        graph_path=resolved_graph,
        sink_name=sink_name,
        path=safe_path,
        dtype=dtype,
        vlen=vlen,
    )


def _stat_identity(result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
    )


_STOPPED_CAPTURE_NOTE = (
    "Only samples observed before the bounded native stop are available; this capture may be "
    "partial and does not represent whole-stream completion."
)


def _validate_completion_evidence(
    run_id: int | None, run_log: dict, *, stopped: bool
) -> Literal["natural", "stopped"]:
    if run_id is None or run_log.get("run_id") != run_id:
        raise ValueError("Observation run_id and retained run log run_id do not match.")
    if run_log.get("run_in_progress") is not False:
        raise ValueError("The requested flowgraph run is still in progress or has no completion proof.")
    if run_log.get("process_exited") is not True:
        raise ValueError("Observation requires process_exited=True in the retained run log.")
    if stopped:
        if run_log.get("terminal_observed") is not True:
            raise ValueError("A stopped capture requires terminal_observed=True.")
        if run_log.get("runtime_error") is not False:
            raise ValueError("A stopped capture requires runtime_error=False.")
        return_code = run_log.get("return_code")
        if type(return_code) is not int or return_code not in (0, -15):
            raise ValueError("A stopped capture requires return_code 0 or -15.")
        if any(
            run_log.get(flag)
            for flag in ("spawn_failed", "forced", "synthesized", "terminated_by_kill", "killed")
        ):
            raise ValueError("A stopped capture cannot use a spawned-failed, forced, or synthesized run.")
        return "stopped"

    if run_log.get("ran_successfully") is not True:
        raise ValueError("Observation requires a naturally completed successful flowgraph run.")
    if any(
        run_log.get(flag)
        for flag in ("spawn_failed", "forced", "synthesized", "terminated_by_kill", "killed")
    ):
        raise ValueError("Observation is unavailable for a forced, synthesized, or failed run.")
    return "natural"


def seal_observation(record: Observation, run_log: dict, *, stopped: bool = False) -> None:
    """Bind a prepared record to natural completion or a confirmed bounded stop.

    Call immediately after the native blocking wait returns, after assigning
    ``record.run_id``. Set ``stopped`` only for a confirmed bounded native stop.
    This seals file identity before any later analysis read.
    """
    completion = _validate_completion_evidence(record.run_id, run_log, stopped=stopped)
    if record.completion is not None and record.completion != completion:
        raise ValueError("Observation completion type changed after sealing.")

    path = Path(record.path)
    try:
        sandbox_path = GrcFileSystemToolset()._safe_resolve(str(path), write=True)
        before_path = path.lstat()
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot seal observation outside the current project sandbox: {exc}") from exc
    if sandbox_path != path or path.resolve() != path:
        raise ValueError(f"Cannot seal observation through a symlinked path: {path}")
    if stat.S_ISLNK(before_path.st_mode) or not stat.S_ISREG(before_path.st_mode):
        raise ValueError(f"Cannot seal observation: output is not a regular file: {path}")

    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ValueError(f"Cannot safely open completed observation output {path}: {exc}") from exc
    try:
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
        after_path = path.lstat()
    except OSError as exc:
        raise ValueError(f"Could not seal completed observation output {path}: {exc}") from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or _stat_identity(opened) != _stat_identity(before_path)
        or _stat_identity(opened) != _stat_identity(after_path)
        or stat.S_ISLNK(after_path.st_mode)
    ):
        raise ValueError("Observation output changed while its completed-file identity was sealed.")
    identity = _stat_identity(opened)
    if record.completed_file_identity is not None and record.completed_file_identity != identity:
        raise ValueError("Observation output changed after its completed-file identity was sealed.")
    record.completed_file_identity = identity
    record.completion = completion


def _validate_run_provenance(
    record: Observation, graph: Any, run_log: dict, run_id: int
) -> None:
    if record.run_id is None or record.run_id != run_id:
        raise ValueError("Observation run_id does not match the requested run and retained run log.")
    if record.completion is None:
        raise ValueError("Observation completion is not sealed; call seal_observation after the run.")
    completion = _validate_completion_evidence(
        record.run_id, run_log, stopped=record.completion == "stopped"
    )
    if completion != record.completion:
        raise ValueError("Run completion evidence no longer matches the sealed observation.")
    if flow_graph_content_hash(graph) != record.graph_hash:
        raise ValueError("The in-memory flowgraph changed after this observation was prepared.")
    current_graph_path = graph.grc_file_path
    if not current_graph_path or Path(current_graph_path).resolve() != record.graph_path:
        raise ValueError("The active graph path changed after this observation was prepared.")
    if record.completed_file_identity is None:
        raise ValueError("Observation file identity is not sealed; call seal_observation after the run.")


def _read_open_file(
    path: Path,
    dtype: np.dtype,
    offset: int,
    selected_count: int,
    before_path: os.stat_result,
    completed_file_identity: tuple[int, int, int, int, int] | None,
) -> tuple[np.ndarray, os.stat_result, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"Cannot safely open observation output {path}: {exc}") from exc
    try:
        with os.fdopen(fd, "rb") as stream:
            before_fd = os.fstat(stream.fileno())
            if not stat.S_ISREG(before_fd.st_mode) or _stat_identity(before_fd) != _stat_identity(before_path):
                raise ValueError("Observation file changed while it was being opened.")
            if (
                completed_file_identity is not None
                and _stat_identity(before_fd) != completed_file_identity
            ):
                raise ValueError(
                    "Observation file was replaced or modified after native completion was sealed."
                )
            stream.seek(offset * dtype.itemsize)
            values = np.fromfile(stream, dtype=dtype, count=selected_count)
            after_fd = os.fstat(stream.fileno())
        after_path = path.lstat()
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"Could not read observation output {path}: {exc}") from exc
    return values, after_fd, after_path


def _read_selected_samples(
    path: Path,
    dtype: np.dtype,
    vlen: int,
    offset: int,
    count: int,
    completed_file_identity: tuple[int, int, int, int, int] | None,
) -> tuple[np.ndarray, int, tuple[int, int, int, int, int]]:
    try:
        before_path = path.lstat()
    except OSError as exc:
        raise ValueError(f"Observation output is unavailable: {path}: {exc}") from exc
    if stat.S_ISLNK(before_path.st_mode):
        raise ValueError(f"Observation output must not be a symlink: {path}")
    if not stat.S_ISREG(before_path.st_mode):
        raise ValueError(f"Observation output must be a regular file: {path}")
    if path.resolve() != path:
        raise ValueError(f"Observation output path now resolves through a symlink: {path}")

    record_size = dtype.itemsize * vlen
    if before_path.st_size % record_size:
        raise ValueError(
            f"Observation file size {before_path.st_size} is not aligned to "
            f"{record_size}-byte vector records (dtype itemsize {dtype.itemsize}, vlen {vlen})."
        )
    total_samples = before_path.st_size // dtype.itemsize
    if offset > total_samples:
        raise ValueError(
            f"offset {offset} is beyond the available {total_samples} scalar samples."
        )
    selected_count = min(count, total_samples - offset)

    try:
        sandbox_path = GrcFileSystemToolset()._safe_resolve(str(path), write=True)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Observation output is outside the current project sandbox: {exc}") from exc
    if sandbox_path != path:
        raise ValueError(f"Observation output path traverses a symlink: {path}")

    values, after_fd, after_path = _read_open_file(
        path, dtype, offset, selected_count, before_path, completed_file_identity
    )
    if (
        _stat_identity(before_path) != _stat_identity(after_fd)
        or _stat_identity(after_fd) != _stat_identity(after_path)
        or stat.S_ISLNK(after_path.st_mode)
        or not stat.S_ISREG(after_path.st_mode)
    ):
        raise ValueError("Observation file changed during the read; discard this partial result.")
    if values.size != selected_count:
        raise ValueError(
            f"Observation file changed during the read: expected {selected_count} samples, "
            f"read {values.size}."
        )
    return values, total_samples, _stat_identity(after_fd)


def read_observation(
    record: Observation,
    graph: Any,
    run_log: dict,
    run_id: int,
    offset: int,
    count: int,
) -> tuple[np.ndarray, dict]:
    """Read a bounded flat sample range after matching run and graph proof."""
    if offset < 0:
        raise ValueError("offset must be a non-negative scalar-sample index.")
    if count <= 0:
        raise ValueError("count must be a positive number of scalar samples.")
    _validate_run_provenance(record, graph, run_log, run_id)
    path = Path(record.path)
    dtype = np.dtype(record.dtype)
    vlen = int(record.vlen)
    if vlen <= 0:
        raise ValueError("Recorded vector length must be positive.")
    values, total_samples, file_identity = _read_selected_samples(
        path, dtype, vlen, offset, count, record.completed_file_identity
    )
    if file_identity != record.completed_file_identity:
        raise ValueError("Observation file changed since native run completion was sealed.")

    metadata = {
        "run_id": run_id,
        "graph_hash": record.graph_hash,
        "graph_path": str(record.graph_path),
        "sink_name": record.sink_name,
        "path": str(path),
        "dtype": dtype.str,
        "vlen": vlen,
        "offset": offset,
        "selected_count": int(values.size),
        "total_samples": int(total_samples),
        "omitted_count": int(total_samples - values.size),
        "interpretation": "flat scalar samples; vector records are flattened and grouped by vlen",
        "sha256": hashlib.sha256(values.tobytes(order="C")).hexdigest(),
        "completion": record.completion,
    }
    if record.completion == "stopped":
        metadata["capture_note"] = _STOPPED_CAPTURE_NOTE
    return values, metadata
