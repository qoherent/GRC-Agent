from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from grc_agent import fs_tools
from grc_agent.adapter.graph import get_platform
from grc_agent.observations import prepare_observation, read_observation, seal_observation


def _native_graph(tmp_path: Path, monkeypatch, *, dtype="float", vlen=1, append=False):
    graph_path = tmp_path / "experiment.grc"
    graph_path.touch()
    monkeypatch.setattr(fs_tools, "_project_dir_fn", lambda: tmp_path)
    monkeypatch.setattr(fs_tools, "_active_grc_path_fn", lambda: graph_path)

    graph = get_platform().make_flow_graph()
    graph.grc_file_path = str(graph_path.resolve())
    sink = graph.new_block("blocks_file_sink")
    for name, value in {
        "id": "capture",
        "file": "capture.bin",
        "type": dtype,
        "vlen": str(vlen),
        "append": "True" if append else "False",
    }.items():
        sink.params[name].set_value(value)
    graph.rewrite()
    return graph, graph_path, sink


def _successful_log(run_id=7, **overrides):
    return {
        "run_id": run_id,
        "run_in_progress": False,
        "process_exited": True,
        "ran_successfully": True,
        "return_code": 0,
        **overrides,
    }


def _stopped_log(run_id=7, **overrides):
    return {
        "run_id": run_id,
        "run_in_progress": False,
        "process_exited": True,
        "terminal_observed": True,
        "runtime_error": False,
        "return_code": -15,
        "ran_successfully": False,
        **overrides,
    }


def _seal(record, run_log=None):
    seal_observation(record, run_log or _successful_log())


def _prepared(tmp_path, monkeypatch, *, dtype="float", vlen=1):
    graph, graph_path, _ = _native_graph(tmp_path, monkeypatch, dtype=dtype, vlen=vlen)
    record = prepare_observation(graph, "capture", str(graph_path))
    record.run_id = 7
    return graph, graph_path, record


def test_prepare_uses_native_file_sink_and_resolves_relative_path(tmp_path, monkeypatch):
    graph, graph_path, sink = _native_graph(tmp_path, monkeypatch, dtype="complex", vlen=4)

    record = prepare_observation(graph, "capture", str(graph_path))

    assert record.run_id is None
    assert record.graph_path == graph_path.resolve()
    assert record.path == (tmp_path / "capture.bin").resolve()
    assert record.sink_name == sink.name == "capture"
    assert record.dtype == np.dtype("=c8")
    assert record.vlen == 4
    assert not record.path.exists()


def test_prepare_requires_enabled_exact_file_sink(tmp_path, monkeypatch):
    graph, graph_path, sink = _native_graph(tmp_path, monkeypatch)
    sink.states["state"] = "disabled"

    with pytest.raises(ValueError, match="Expected one enabled File Sink"):
        prepare_observation(graph, "capture", str(graph_path))


def test_prepare_rejects_append_and_preserves_existing_artifact(tmp_path, monkeypatch):
    graph, graph_path, _ = _native_graph(tmp_path, monkeypatch, append=True)
    with pytest.raises(ValueError, match="append disabled"):
        prepare_observation(graph, "capture", str(graph_path))

    artifact = tmp_path / "capture.bin"
    artifact.write_bytes(b"old run")
    graph, graph_path, _ = _native_graph(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="already exists"):
        prepare_observation(graph, "capture", str(graph_path))
    assert artifact.read_bytes() == b"old run"


def test_prepare_rejects_symlink_and_outside_sandbox(tmp_path, monkeypatch):
    graph, graph_path, sink = _native_graph(tmp_path, monkeypatch)
    (tmp_path / "capture.bin").symlink_to(tmp_path / "missing.bin")
    with pytest.raises(ValueError, match="already exists"):
        prepare_observation(graph, "capture", str(graph_path))

    (tmp_path / "capture.bin").unlink()
    sink.params["file"].set_value("../outside.txt")
    graph.rewrite()
    with pytest.raises(ValueError, match="sandbox"):
        prepare_observation(graph, "capture", str(graph_path))


def test_prepare_rejects_duplicate_enabled_sink_output(tmp_path, monkeypatch):
    graph, graph_path, _ = _native_graph(tmp_path, monkeypatch)
    other_sink = graph.new_block("blocks_file_sink")
    for name, value in {
        "id": "second_capture",
        "file": "capture.bin",
        "type": "float",
        "vlen": "1",
        "append": "False",
    }.items():
        other_sink.params[name].set_value(value)
    graph.rewrite()

    with pytest.raises(ValueError, match="target the same output path"):
        prepare_observation(graph, "capture", str(graph_path))


def test_prepare_does_not_expand_tilde_in_native_sink_path(tmp_path, monkeypatch):
    graph, graph_path, sink = _native_graph(tmp_path, monkeypatch)
    (tmp_path / "~").mkdir()
    sink.params["file"].set_value("~/capture.bin")
    graph.rewrite()

    record = prepare_observation(graph, "capture", str(graph_path))

    assert record.path == tmp_path / "~" / "capture.bin"


@pytest.mark.parametrize(
    "log_overrides, message",
    [
        ({"run_in_progress": True}, "still in progress"),
        ({"ran_successfully": False}, "successful flowgraph run"),
        ({"spawn_failed": True}, "forced, synthesized, or failed"),
        ({"terminated_by_kill": True}, "forced, synthesized, or failed"),
        ({"synthesized": True}, "forced, synthesized, or failed"),
        ({"forced": True}, "forced, synthesized, or failed"),
    ],
)
def test_read_requires_natural_successful_completion(tmp_path, monkeypatch, log_overrides, message):
    graph, _, record = _prepared(tmp_path, monkeypatch)
    np.array([1.0], dtype=np.float32).tofile(record.path)
    _seal(record)
    with pytest.raises(ValueError, match=message):
        read_observation(record, graph, _successful_log(**log_overrides), 7, 0, 2)


def test_read_rejects_stale_run_ids(tmp_path, monkeypatch):
    graph, _, record = _prepared(tmp_path, monkeypatch)
    np.array([1.0], dtype=np.float32).tofile(record.path)
    _seal(record)
    with pytest.raises(ValueError, match="run_id"):
        read_observation(record, graph, _successful_log(8), 7, 0, 2)
    with pytest.raises(ValueError, match="run_id"):
        read_observation(record, graph, _successful_log(), 8, 0, 2)


def test_read_rejects_graph_mutation_after_prepare(tmp_path, monkeypatch):
    graph, _, record = _prepared(tmp_path, monkeypatch)
    np.array([1.0], dtype=np.float32).tofile(record.path)
    _seal(record)
    graph.options_block.params["title"].set_value("changed after capture setup")
    graph.rewrite()
    with pytest.raises(ValueError, match="changed after this observation was prepared"):
        read_observation(record, graph, _successful_log(), 7, 0, 2)


def test_read_requires_completion_seal(tmp_path, monkeypatch):
    graph, _, record = _prepared(tmp_path, monkeypatch)
    np.array([1.0], dtype=np.float32).tofile(record.path)

    with pytest.raises(ValueError, match="call seal_observation"):
        read_observation(record, graph, _successful_log(), 7, 0, 1)


@pytest.mark.parametrize(
    "dtype,vlen,values,offset,count,expected",
    [
        ("float", 1, np.array([1.25, -2.5, 8.0], dtype=np.float32), 1, 9, [-2.5, 8.0]),
        (
            "complex",
            2,
            np.array([1 + 2j, 3 - 4j, -5 + 0.5j, 9j], dtype=np.complex64),
            1,
            2,
            [3 - 4j, -5 + 0.5j],
        ),
    ],
)
def test_reads_native_float_and_complex_vector_data(
    tmp_path, monkeypatch, dtype, vlen, values, offset, count, expected
):
    graph, _, record = _prepared(tmp_path, monkeypatch, dtype=dtype, vlen=vlen)
    values.tofile(record.path)
    _seal(record)

    actual, metadata = read_observation(record, graph, _successful_log(), 7, offset, count)

    np.testing.assert_array_equal(actual, np.asarray(expected, dtype=record.dtype))
    assert metadata["selected_count"] == len(expected)
    assert metadata["total_samples"] == values.size
    assert metadata["omitted_count"] == values.size - len(expected)
    assert metadata["offset"] == offset
    assert metadata["vlen"] == vlen
    assert "flat scalar samples" in metadata["interpretation"]
    assert metadata["sha256"] == hashlib.sha256(actual.tobytes()).hexdigest()


def test_natural_seal_records_natural_completion(tmp_path, monkeypatch):
    graph, _, record = _prepared(tmp_path, monkeypatch)
    np.array([1.0, 2.0], dtype=np.float32).tofile(record.path)
    run_log = _successful_log()

    seal_observation(record, run_log)
    _, metadata = read_observation(record, graph, run_log, 7, 0, 2)

    assert record.completion == metadata["completion"] == "natural"
    assert "capture_note" not in metadata


@pytest.mark.parametrize("return_code", [0, -15])
def test_stopped_seal_records_partial_capture_provenance(tmp_path, monkeypatch, return_code):
    graph, _, record = _prepared(tmp_path, monkeypatch)
    np.array([1.0, 2.0], dtype=np.float32).tofile(record.path)
    run_log = _stopped_log(return_code=return_code)

    seal_observation(record, run_log, stopped=True)
    _, metadata = read_observation(record, graph, run_log, 7, 0, 2)

    assert record.completion == metadata["completion"] == "stopped"
    assert "may be partial" in metadata["capture_note"]
    assert "whole-stream completion" in metadata["capture_note"]


@pytest.mark.parametrize(
    "missing,overrides",
    [
        ("terminal_observed", {}),
        (None, {"terminal_observed": False}),
        ("runtime_error", {}),
        (None, {"runtime_error": True}),
        (None, {"return_code": 1}),
        (None, {"spawn_failed": True}),
        (None, {"forced": True}),
        (None, {"synthesized": True}),
        (None, {"terminated_by_kill": True}),
        (None, {"killed": True}),
    ],
)
def test_stopped_seal_rejects_missing_or_abnormal_completion_evidence(
    tmp_path, monkeypatch, missing, overrides
):
    graph, _, record = _prepared(tmp_path, monkeypatch)
    np.array([1.0], dtype=np.float32).tofile(record.path)
    log = _stopped_log(**overrides)
    if missing is not None:
        log.pop(missing)

    with pytest.raises(ValueError):
        seal_observation(record, log, stopped=True)
    assert record.completion is None
    assert record.completed_file_identity is None


def test_stopped_seal_requires_matching_run_id(tmp_path, monkeypatch):
    _, _, record = _prepared(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="run_id.*do not match"):
        seal_observation(record, _stopped_log(8), stopped=True)


@pytest.mark.parametrize(
    "stopped,missing,process_exited",
    [
        (False, True, None),
        (False, False, False),
        (True, True, None),
        (True, False, False),
    ],
)
def test_seal_requires_explicit_process_exit_proof(
    tmp_path, monkeypatch, stopped, missing, process_exited
):
    _, _, record = _prepared(tmp_path, monkeypatch)
    np.array([1.0], dtype=np.float32).tofile(record.path)
    log = _stopped_log() if stopped else _successful_log()
    if missing:
        log.pop("process_exited")
    else:
        log["process_exited"] = process_exited

    with pytest.raises(ValueError, match="process_exited=True"):
        seal_observation(record, log, stopped=stopped)
    assert record.completion is None


def test_read_revalidates_process_exit_proof_with_success_flag_still_true(
    tmp_path, monkeypatch
):
    graph, _, record = _prepared(tmp_path, monkeypatch)
    np.array([1.0], dtype=np.float32).tofile(record.path)
    _seal(record)
    changed_log = _successful_log(process_exited=False)

    with pytest.raises(ValueError, match="process_exited=True"):
        read_observation(record, graph, changed_log, 7, 0, 1)


def test_read_revalidates_stopped_completion_evidence(tmp_path, monkeypatch):
    graph, _, record = _prepared(tmp_path, monkeypatch)
    np.array([1.0], dtype=np.float32).tofile(record.path)
    seal_observation(record, _stopped_log(), stopped=True)
    changed_log = _stopped_log(runtime_error=True)

    with pytest.raises(ValueError, match="runtime_error=False"):
        read_observation(record, graph, changed_log, 7, 0, 1)


def test_read_rejects_vector_record_misalignment(tmp_path, monkeypatch):
    graph, _, record = _prepared(tmp_path, monkeypatch, dtype="float", vlen=2)
    np.array([1.0, 2.0, 3.0], dtype=np.float32).tofile(record.path)
    _seal(record)

    with pytest.raises(ValueError, match="not aligned"):
        read_observation(record, graph, _successful_log(), 7, 0, 2)


def test_read_rejects_symlinked_output(tmp_path, monkeypatch):
    graph, _, record = _prepared(tmp_path, monkeypatch)
    real_file = tmp_path / "real.txt"
    np.array([1.0], dtype=np.float32).tofile(record.path)
    _seal(record)
    record.path.unlink(missing_ok=True)
    np.array([1.0], dtype=np.float32).tofile(real_file)
    record.path.symlink_to(real_file)

    with pytest.raises(ValueError, match="must not be a symlink"):
        read_observation(record, graph, _successful_log(), 7, 0, 1)


def test_read_rechecks_current_project_sandbox(tmp_path, monkeypatch):
    graph, _, record = _prepared(tmp_path, monkeypatch)
    np.array([1.0], dtype=np.float32).tofile(record.path)
    _seal(record)
    switched_project = tmp_path / "different-project"
    switched_project.mkdir()
    monkeypatch.setattr(fs_tools, "_project_dir_fn", lambda: switched_project)

    with pytest.raises(ValueError, match="current project sandbox"):
        read_observation(record, graph, _successful_log(), 7, 0, 1)


def test_read_rejects_file_replacement_after_native_completion_seal(tmp_path, monkeypatch):
    graph, _, record = _prepared(tmp_path, monkeypatch)
    np.array([1.0, 2.0], dtype=np.float32).tofile(record.path)
    _seal(record)
    assert record.completed_file_identity is not None

    np.array([3.0, 4.0], dtype=np.float32).tofile(record.path)
    with pytest.raises(ValueError, match="modified after native completion was sealed"):
        read_observation(record, graph, _successful_log(), 7, 0, 1)


@pytest.mark.parametrize("offset,count", [(-1, 1), (0, 0), (4, 1)])
def test_read_rejects_invalid_or_out_of_range_sample_bounds(
    tmp_path, monkeypatch, offset, count
):
    graph, _, record = _prepared(tmp_path, monkeypatch)
    np.array([1.0, 2.0, 3.0], dtype=np.float32).tofile(record.path)
    _seal(record)

    with pytest.raises(ValueError, match="offset|count|beyond"):
        read_observation(record, graph, _successful_log(), 7, offset, count)
