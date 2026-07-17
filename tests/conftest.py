"""Shared pytest fixtures/configuration for the grc-agent test suite.

Pin GTK 3.0 once at collection time. chat_sidebar.py calls
``gi.require_version("Gtk", "3.0")`` at import, but several tests do a bare
``from gi.repository import Gtk`` *before* importing chat_sidebar — and on this
system the default Gtk version is 4.0, which would then conflict with the 3.0
requirement. Pinning here (before any test module runs) makes the suite
deterministic regardless of test execution order.
"""

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
