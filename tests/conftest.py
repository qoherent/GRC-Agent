"""Test-suite isolation.

Sets env-overridable ports to non-default values BEFORE any test module
imports `grc_agent.web` (which reads them at module load). This lets the
suite run alongside a live dev session on the default ports without the two
stomping each other's broadwayd / canvas control server / web server.

Imported by pytest before test modules are collected, so the env is in place
in time for `from grc_agent import web as web_app` in test_web_app.py.
"""

import os

# High ports unlikely to collide with a dev session on the defaults
# (8085 / 7933 / 7932) or with ephemeral OS ports.
os.environ.setdefault("GRC_BROADWAY_PORT", "18085")
os.environ.setdefault("GRC_CANVAS_CONTROL_PORT", "17933")
os.environ.setdefault("GRC_AGENT_PORT", "17932")
