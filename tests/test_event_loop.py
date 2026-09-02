"""The asyncio+GLib unification seam.

AGENTS.md section 4 requires one unified loop on GTK's default GMainContext,
installed exactly once before any UI initialisation. Two backends satisfy
that: PyGObject >= 3.50 ships `gi.events` in-tree, and older PyGObject (the
3.48 that Ubuntu 24.04 ships, and the floor this project still supports via
`requires-python = ">=3.12"`) needs gbulb.

Neither path had a test. The gbulb branch in particular monkeypatches a
third-party transport method, so a gbulb release that renames or reshapes
`_loop_reading` would break the app silently on every 3.12 install.
"""

import asyncio
import importlib.util

import pytest


def test_install_is_idempotent_and_picks_an_available_backend():
    """install() must be safe to call twice and must leave a usable policy."""
    from grc_agent import event_loop

    saved = asyncio.get_event_loop_policy()
    try:
        event_loop.install()
        first = asyncio.get_event_loop_policy()
        event_loop.install()  # second call is a no-op, not a re-install
        assert asyncio.get_event_loop_policy() is first
    finally:
        asyncio.set_event_loop_policy(saved)


def test_exactly_one_unification_backend_is_available():
    """One of the two backends must exist, or the app cannot run at all."""
    has_gi_events = importlib.util.find_spec("gi.events") is not None
    has_gbulb = importlib.util.find_spec("gbulb") is not None
    assert has_gi_events or has_gbulb, (
        "neither gi.events (PyGObject >= 3.50) nor gbulb is importable; "
        "the unified asyncio+GLib loop has no backend"
    )


@pytest.mark.skipif(
    importlib.util.find_spec("gi.events") is not None,
    reason="PyGObject >= 3.50 provides gi.events in-tree; the gbulb path is unused here",
)
def test_gbulb_patch_target_still_exists():
    """_install_gbulb patches gbulb's ReadTransport._loop_reading.

    Pin the attribute it reaches for: gbulb is a third-party package pinned
    only by a lower bound, and a rename upstream would otherwise turn the
    patch into a silent no-op on every Python 3.12 install.
    """
    import gbulb.transports

    assert hasattr(gbulb.transports, "ReadTransport")
    assert callable(gbulb.transports.ReadTransport._loop_reading)
