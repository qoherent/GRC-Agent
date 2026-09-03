"""Shared pytest fixtures/configuration for the grc-agent test suite.

Pin GTK 3.0 once at collection time. chat_sidebar.py calls
``gi.require_version("Gtk", "3.0")`` at import, but several tests do a bare
``from gi.repository import Gtk`` *before* importing chat_sidebar — and on this
system the default Gtk version is 4.0, which would then conflict with the 3.0
requirement. Pinning here (before any test module runs) makes the suite
deterministic regardless of test execution order.
"""

import contextlib
import shutil
import sys
import tempfile
from pathlib import Path

import gi
import pytest

from grc_agent.adapter import change_graph

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")


"""Shared fixtures/helpers/constants split out of the former test_unit.py god
file. The epy sources and dial-tone block names feed adapter/graph, layout,
block_library, and chat tests; _seed_session/_count_sessions_for_path feed the
session-DB tests; _FakeResponse feeds probe_backend tests."""


# Absolute so the suite does not depend on being invoked from the repo root.
FIXTURES_DIR = Path(__file__).resolve().parent / "data"


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


# ---------------------------------------------------------------------------
# Shared fixtures
#
# These replace per-file copies that had drifted: the environment-isolation
# setenv appeared 108 times across 14 files with the fixture itself redeclared
# verbatim four times, ChatSidebar() was constructed 95 times against 21
# teardowns, the canvas manager was built by __new__ at 20 sites with 6-8
# hand-set attributes each, and the fake-deps harness was byte-identical
# across two files (which one of them documented in its own docstring).
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Point .env, the session DB and the vector store at a temp directory.

    Every test that touches settings or persistence needs this; without it a
    run writes to the developer's real .env and chat DB.
    """
    env_path = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env_path))
    vectors = tmp_path / "vectors"
    vectors.mkdir(exist_ok=True)
    monkeypatch.setenv("GRC_AGENT_VECTORS_DIR", str(vectors))
    return env_path


@pytest.fixture
def sidebar(isolated_env):  # noqa: ARG001
    """A ChatSidebar that actually gets torn down.

    ChatSidebar.__init__ arms a 60s and a 500ms repeating GLib source, and
    until they were removable nothing ever disarmed them — every sidebar a
    test constructed went on polling the shared default GMainContext for the
    rest of the session. With enough armed, a `while Gtk.events_pending()`
    drain never runs dry, which is what made the GTK suite order-dependent.
    """
    from grc_agent.chat_sidebar import ChatSidebar

    widget = ChatSidebar()
    try:
        yield widget
    finally:
        widget.destroy()


@pytest.fixture
def canvas_manager():
    """A NativeCanvasManager built through its real constructor.

    Tests used to build it with __new__ and hand-set 6-8 attributes, so the
    object under test was a hand-maintained shadow of the real shape — and
    because the polled method swallows exceptions, a renamed __init__
    attribute vanished instead of failing a test.

    __init__ itself arms nothing: the 1.5s safety-net poll
    (_check_for_unsynced_edit) is armed by setup_signal_handlers(), which
    also wires real notebook signals and so is deliberately NOT called here
    -- a test that needs the poll armed calls it explicitly (and is then
    responsible for that source, same as any other GLib.timeout_add call
    a test triggers directly).
    """
    from unittest.mock import MagicMock

    from grc_agent.adapter import graph as adapter_graph
    from grc_agent.native_canvas import NativeCanvasManager

    # GRC's own GUI package (gnuradio.grc.gui) must be bootstrapped top-down
    # once per process before anything under it is constructed directly, or
    # a lazy internal import (Bars.py importing Actions mid-init) hits its
    # own circular-import failure -- the same gotcha
    # test_untitled_save_dialog_seeded_to_project_dir works around the same
    # way. get_gui_platform() is the app's own bootstrap entry point and is
    # idempotent.
    adapter_graph.get_gui_platform()

    window = MagicMock()
    platform = MagicMock()
    return NativeCanvasManager(window, platform)


def walk_widgets(root):
    """Every descendant of `root`, depth-first.

    Eleven copies of this walker lived in test_chat_sidebar.py alone.
    """
    yield root
    children = getattr(root, "get_children", None)
    if not callable(children):
        return
    for child in children():
        yield from walk_widgets(child)


@pytest.fixture(autouse=True)
def _disarm_leaked_sidebar_timers():
    """Disarm repeating GLib sources left behind by any sidebar a test built.

    The `sidebar` fixture below is the right way to get one, but ~95 tests
    construct ChatSidebar directly. Each arms a 60s and a 500ms repeating
    source; left armed they accumulate on the shared default GMainContext
    until a `while Gtk.events_pending()` drain never runs dry and the suite
    hangs — reproducible today by running the GTK file in reverse order.

    This sweeps test-side rather than adding a registry to ChatSidebar:
    production code must not carry scaffolding that exists only for tests.
    """
    yield
    import gc

    sidebar_cls = sys.modules.get("grc_agent.chat_sidebar")
    if sidebar_cls is None:
        return
    cls = getattr(sidebar_cls, "ChatSidebar", None)
    if cls is None:
        return
    for obj in gc.get_objects():
        if isinstance(obj, cls):
            with contextlib.suppress(Exception):
                obj._remove_timers()
