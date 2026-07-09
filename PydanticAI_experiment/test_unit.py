import os
import shutil
import tempfile
import pytest
from pathlib import Path

from grc_adapter import (
    load_flow_graph,
    inspect_graph,
    change_graph,
    query_catalog,
    query_docs,
    web_search,
    web_fetch,
)

FIXTURES_DIR = Path("tests/data")

@pytest.fixture
def temp_dial_tone():
    tmp_dir = tempfile.mkdtemp()
    src = FIXTURES_DIR / "dial_tone.grc"
    dst = Path(tmp_dir) / "dial_tone.grc"
    shutil.copy2(src, dst)
    yield dst
    shutil.rmtree(tmp_dir)

@pytest.fixture
def temp_empty():
    tmp_dir = tempfile.mkdtemp()
    src = FIXTURES_DIR / "empty.grc"
    dst = Path(tmp_dir) / "empty.grc"
    shutil.copy2(src, dst)
    yield dst
    shutil.rmtree(tmp_dir)

# ==========================================
# inspect_graph Unit Tests (2 tests)
# ==========================================

def test_inspect_graph_overview(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    res = inspect_graph(fg)
    assert res["ok"] is True
    graph = res["graph"]
    assert graph["validation"]["status"] == "valid"
    block_names = {b["instance_name"] for b in graph["blocks"]}
    assert "samp_rate" in block_names
    assert "analog_sig_source_x_0" in block_names
    assert len(graph["connections"]) > 0

def test_inspect_graph_scoped(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    res = inspect_graph(fg, targets=["samp_rate", "analog_sig_source_x_0"])
    assert res["ok"] is True
    graph = res["graph"]
    block_names = {b["instance_name"] for b in graph["blocks"]}
    assert "samp_rate" in block_names
    assert "analog_sig_source_x_0" in block_names
    assert len(block_names) == 2

# ==========================================
# change_graph Unit Tests (8 tests)
# ==========================================

def test_change_graph_add_block(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(fg, add_blocks=[{
        "block_id": "blocks_throttle2",
        "instance_name": "my_throttle",
        "params": {"type": "float"}
    }])
    assert res["ok"] is True
    snap = inspect_graph(fg)
    block_names = {b["instance_name"] for b in snap["graph"]["blocks"]}
    assert "my_throttle" in block_names

def test_change_graph_remove_block(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(fg, remove_blocks=["analog_noise_source_x_0"])
    assert res["ok"] is True
    snap = inspect_graph(fg)
    block_names = {b["instance_name"] for b in snap["graph"]["blocks"]}
    assert "analog_noise_source_x_0" not in block_names

def test_change_graph_update_params(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(fg, update_params=[{
        "instance_name": "samp_rate",
        "params": {"value": "96000"}
    }])
    assert res["ok"] is True
    snap = inspect_graph(fg)
    params = {b["instance_name"]: b["params"] for b in snap["graph"]["blocks"]}
    assert params["samp_rate"]["value"] == "96000"

def test_change_graph_update_states(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(fg, update_states=[{
        "instance_name": "blocks_add_xx",
        "state": "bypass"
    }])
    assert res["ok"] is True
    snap = inspect_graph(fg)
    states = {b["instance_name"]: b["state"] for b in snap["graph"]["blocks"]}
    assert states["blocks_add_xx"] == "bypass"

def test_change_graph_add_connection(temp_empty):
    fg = load_flow_graph(str(temp_empty))
    # Add two blocks first
    change_graph(fg, add_blocks=[
        {"block_id": "analog_sig_source_x", "instance_name": "sig", "params": {"type": "float"}},
        {"block_id": "blocks_null_sink", "instance_name": "sink", "params": {"type": "float"}}
    ])
    res = change_graph(fg, add_connections=["sig:0->sink:0"])
    assert res["ok"] is True
    snap = inspect_graph(fg)
    conns = snap["graph"]["connections"]
    assert "sig:0->sink:0" in conns

def test_change_graph_remove_connection(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(fg, remove_connections=["analog_sig_source_x_0:0->blocks_add_xx:0"])
    assert res["ok"] is True
    snap = inspect_graph(fg)
    conns = snap["graph"]["connections"]
    assert "analog_sig_source_x_0:0->blocks_add_xx:0" not in conns

def test_change_graph_complex_batch(temp_empty):
    fg = load_flow_graph(str(temp_empty))
    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "analog_sig_source_x", "instance_name": "sig", "params": {"type": "float"}},
            {"block_id": "blocks_throttle2", "instance_name": "thr", "params": {"type": "float"}},
            {"block_id": "blocks_null_sink", "instance_name": "sink", "params": {"type": "float"}}
        ],
        add_connections=[
            "sig:0->thr:0",
            "thr:0->sink:0"
        ]
    )
    assert res["ok"] is True
    snap = inspect_graph(fg)
    assert snap["graph"]["validation"]["status"] == "valid"

def test_change_graph_rollback_failure(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    # Make a change that is invalid (connecting to a non-existent port)
    res = change_graph(fg, add_connections=["analog_sig_source_x_0:0->blocks_add_xx:99"])
    assert res["ok"] is False
    assert len(res["errors"]) > 0
    # Confirm that the file is not corrupted and original state is valid
    snap = inspect_graph(fg)
    assert snap["graph"]["validation"]["status"] == "valid"

# ==========================================
# Vector Search and Knowledge Unit Tests (3 tests)
# ==========================================

def test_query_catalog_vector_search():
    res = query_catalog("sine wave source")
    assert res["ok"] is True
    assert "results" in res
    assert len(res["results"]) > 0
    match = res["results"][0]
    assert "block_id" in match
    assert "analog_sig_source_x" in match["block_id"]

def test_query_docs_rag():
    res = query_docs("what is a stream tag")
    assert res["ok"] is True
    assert "answer" in res
    assert len(res["answer"]) > 0

# ==========================================
# Web Tools Unit Tests (2 tests)
# ==========================================

def test_web_search_success():
    res = web_search("python")
    assert res["ok"] is True
    assert "results" in res
    assert isinstance(res["results"], list)
    if len(res["results"]) > 0:
        assert "title" in res["results"][0]

def test_web_fetch_success():
    res = web_fetch("https://example.com")
    assert res["ok"] is True
    assert "Example Domain" in res["title"]
    assert "content" in res
    assert len(res["content"]) > 0
