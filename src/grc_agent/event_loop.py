"""asyncio + GLib event-loop unification.

One capability check, made once at startup. PyGObject >= 3.50 ships
``gi.events``, which is upstream, in-tree, and follows Python releases —
Python 3.14 requires it, since ``gbulb`` is unmaintained and its pinned
PyGObject cannot build there. Ubuntu 24.04's PyGObject 3.48 has no
``gi.events``, so ``gbulb`` is used there instead.

Both provide the same thing: one thread running asyncio and the GLib main
loop together, so agent streaming, canvas syncs, and tool calls need no
cross-thread marshaling. Never reimplement that unification.

``install()`` must be called after ``gi.require_version("Gtk", "3.0")`` and
before any asyncio or GTK use.
"""

from __future__ import annotations

import asyncio

# "" until install() runs; then "gi.events" or "gbulb". Read by callers that
# want to report which backend is live (see desktop_app's startup logging).
backend: str = ""

_policy = None


def install() -> None:
    """Install the unified asyncio+GLib loop policy for this process."""
    global _policy, backend
    if backend:
        return
    try:
        from gi.events import GLibEventLoopPolicy
    except ImportError:
        _install_gbulb()
        backend = "gbulb"
        return
    _policy = GLibEventLoopPolicy()
    backend = "gi.events"


def main_event_loop() -> asyncio.AbstractEventLoop:
    """Return the loop attached to GTK's main context, and make it current."""
    if not backend:
        raise RuntimeError("event_loop.install() must be called before main_event_loop()")
    # gi.events: get_event_loop() binds to the thread-default GMainContext — on
    # the main thread, GLib's *default* context, which is the one GTK dispatches
    # on. new_event_loop() would instead construct a fresh GLib.MainContext()
    # that GTK never iterates, so GTK signals would never reach asyncio. The two
    # are not interchangeable here.
    # gbulb: install(gtk=True) already made the policy return a Gtk-attached
    # loop, so the plain constructor is correct on that path.
    loop = _policy.get_event_loop() if _policy is not None else asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def _install_gbulb() -> None:
    import gbulb

    gbulb.install(gtk=True)

    # Avoid an AssertionError in ReadTransport._loop_reading when transports
    # close or change. gbulb-specific: gi.events has no equivalent defect.
    try:
        import gbulb.transports

        _original_loop_reading = gbulb.transports.ReadTransport._loop_reading

        def _patched_loop_reading(self, fut=None):
            if (
                fut is not None
                and self._read_fut is not fut
                and not (self._read_fut is None and self._closing)
            ):
                return
            return _original_loop_reading(self, fut)

        gbulb.transports.ReadTransport._loop_reading = _patched_loop_reading
    except Exception as e:  # pragma: no cover - defensive, gbulb-only path
        print(f"Warning: Failed to patch gbulb transports: {e}")
