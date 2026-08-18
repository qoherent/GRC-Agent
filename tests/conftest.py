"""Shared pytest fixtures/configuration for the grc-agent test suite.

Pin GTK 3.0 once at collection time. chat_sidebar.py calls
``gi.require_version("Gtk", "3.0")`` at import, but several tests do a bare
``from gi.repository import Gtk`` *before* importing chat_sidebar — and on this
system the default Gtk version is 4.0, which would then conflict with the 3.0
requirement. Pinning here (before any test module runs) makes the suite
deterministic regardless of test execution order.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from grc_agent.adapter import change_graph

"""Shared fixtures/helpers/constants split out of the former test_unit.py god
file. The epy sources and dial-tone block names feed adapter/graph, layout,
block_library, and chat tests; _seed_session/_count_sessions_for_path feed the
session-DB tests; _FakeResponse feeds probe_backend tests."""


FIXTURES_DIR = Path("tests/data")


_DIAL_TONE_FLOW_BLOCKS = {
    "analog_noise_source_x_0",
    "analog_sig_source_x_0",
    "analog_sig_source_x_1",
    "blocks_add_xx",
    "audio_sink",
    "freq_sink_0",
    "lpf_0",
    "time_sink_0",
    "waterfall_sink_0",
}


_EPY_COMPLEX_IO_SOURCE = (
    "import numpy as np\n"
    "from gnuradio import gr\n\n"
    "class blk(gr.sync_block):\n"
    "    def __init__(self):\n"
    "        gr.sync_block.__init__(self, name='epy', in_sig=[np.complex64], out_sig=[np.complex64])\n"
    "    def work(self, input_items, output_items):\n"
    "        output_items[0][:] = input_items[0]\n"
    "        return len(output_items[0])\n"
)


_EPY_FLOAT_INPUT_SOURCE = (
    "import numpy as np\n"
    "from gnuradio import gr\n\n"
    "class blk(gr.sync_block):\n"
    "    def __init__(self):\n"
    "        gr.sync_block.__init__(self, name='epy', in_sig=[np.float32], out_sig=[np.complex64])\n"
    "    def work(self, input_items, output_items):\n"
    "        return 0\n"
)


_EPY_FLOAT_IO_SOURCE = (
    "import numpy as np\n"
    "from gnuradio import gr\n\n"
    "class blk(gr.sync_block):\n"
    "    def __init__(self):\n"
    "        gr.sync_block.__init__(self, name='epy', in_sig=[np.float32], out_sig=[np.float32])\n"
    "    def work(self, input_items, output_items):\n"
    "        output_items[0][:] = input_items[0]\n"
    "        return len(output_items[0])\n"
)


_NOOP_EPY_SOURCE = (
    "from gnuradio import gr\n\n"
    "class blk(gr.basic_block):\n"
    "    def __init__(self):\n"
    "        gr.basic_block.__init__(self, name='noop', in_sig=None, out_sig=None)\n"
)


_SCALE_EPY_SOURCE = (
    "import numpy as np\n"
    "from gnuradio import gr\n\n"
    "class blk(gr.sync_block):\n"
    "    def __init__(self, scale=2.0):\n"
    "        gr.sync_block.__init__(self, name='Scale Block', "
    "in_sig=[np.float32], out_sig=[np.float32])\n"
    "        self.scale = scale\n\n"
    "    def work(self, input_items, output_items):\n"
    "        output_items[0][:] = input_items[0] * self.scale\n"
    "        return len(output_items[0])\n"
)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"data": []}

    def json(self):
        return self._payload


def _add_epy_blocks(fg, count):
    change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "epy_block",
                "instance_name": f"epy_{i}",
                "params": {"_source_code": _NOOP_EPY_SOURCE},
            }
            for i in range(count)
        ],
    )


def _add_scale_epy_block(fg, instance_name="my_epy"):
    return change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "epy_block",
                "instance_name": instance_name,
                "params": {"_source_code": _SCALE_EPY_SOURCE},
            }
        ],
        force=True,
    )


def _seed_session(grc_path: str) -> int:
    """Insert one session row for grc_path and return its id. Uses the real
    save_session so path resolution matches production exactly."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.db import save_session

    return save_session(None, grc_path, [ModelRequest(parts=[UserPromptPart(content="seed")])])


def _count_sessions_for_path(grc_path: str) -> int:
    import sqlite3
    from pathlib import Path

    from grc_agent.db import get_db_path

    abs_path = str(Path(grc_path).resolve())
    conn = sqlite3.connect(str(get_db_path()))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE grc_file_path = ?", (abs_path,)
        ).fetchone()[0]
    finally:
        conn.close()


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


@pytest.fixture
def temp_run_null_sink():
    tmp_dir = tempfile.mkdtemp()
    src = FIXTURES_DIR / "run_test_null_sink.grc"
    dst = Path(tmp_dir) / "run_test_null_sink.grc"
    shutil.copy2(src, dst)
    yield dst
    shutil.rmtree(tmp_dir)


@pytest.fixture
def temp_broken():
    tmp_dir = tempfile.mkdtemp()
    src = FIXTURES_DIR / "broken_unconnected_sink.grc"
    dst = Path(tmp_dir) / "broken_unconnected_sink.grc"
    shutil.copy2(src, dst)
    yield dst
    shutil.rmtree(tmp_dir)


@pytest.fixture
def temp_hier_block_lib_dir(tmp_path):
    """Redirects GNU Radio's Config.hier_block_lib_dir to a fresh tmp dir for
    the test, then restores it and rebuilds the real get_platform()
    singleton. Platform.block_classes is a single ChainMap shared by EVERY
    Platform instance (a Platform CLASS attribute) -- leaving the singleton
    pointed at a now-deleted tmp dir would silently corrupt every later
    test's view of get_platform().blocks."""
    from gnuradio.grc.core.Config import Config

    from grc_agent.adapter.graph import get_platform

    lib_dir = tmp_path / "grc_gnuradio"
    lib_dir.mkdir()
    original = Config.hier_block_lib_dir
    Config.hier_block_lib_dir = str(lib_dir)
    try:
        yield lib_dir
    finally:
        Config.hier_block_lib_dir = original
        get_platform().build_library()
