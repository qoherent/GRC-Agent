"""Shared pytest fixtures for the GRC Agent test suite.

These fixtures are auto-discovered by pytest but are also importable from
`unittest`-style tests when run under the pytest runner. The project
currently uses stdlib `unittest` for its CI gate; pytest is used for the
GUI tests and ad-hoc local runs.

Helpers
-------

- ``tmp_home`` — monkeypatches ``HOME`` and ``XDG_CONFIG_HOME`` to a
  per-test ``tmp_path``. Lets the ``init`` and ``paths`` tests, and any
  future config-writing test, run without touching the developer's
  ``~/.config/grc_agent/``.
- ``grc_agent_toml`` — writes a minimal valid ``grc_agent.toml`` to
  ``tmp_home/.config/grc_agent/config.toml`` with the supplied kwargs.
  Returns the resolved path.
- ``no_real_prefs_writes`` (autouse) — records the developer's real
  ``preferences.json`` mtime at test start and fails any test that
  modifies it. This is the second line of defense against future
  regressions: a leaky test that writes to the real path fails loudly
  instead of silently clobbering the user's model choice.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture()
def tmp_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect ``$HOME`` and ``$XDG_CONFIG_HOME`` to a per-test directory."""
    home = Path(tempfile.mkdtemp(prefix="grc_agent_test_home_", dir=tmp_path))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    yield home
    shutil.rmtree(home, ignore_errors=True)


@pytest.fixture(autouse=True)
def no_real_prefs_writes() -> Any:
    """Snapshot the real ``preferences.json`` mtime before each test and
    fail if any test modifies it.

    The GUI's ``_on_model_swap_finished`` calls
    ``update_last_model`` with no explicit path, which writes to
    ``~/.config/grc_agent/preferences.json`` by default. The
    per-class ``setUp`` in ``MainWindowModelMenuTests`` redirects
    ``XDG_CONFIG_HOME`` for the GUI tests. This fixture is a
    second line of defense: if a future test forgets to redirect
    and clobbers the real prefs file, the test fails immediately
    instead of silently breaking the user's next ``grc-agent-gui``
    launch.
    """
    real_path = Path.home() / ".config" / "grc_agent" / "preferences.json"
    mtime_before: float | None = None
    if real_path.exists():
        mtime_before = real_path.stat().st_mtime
    yield
    if mtime_before is not None and real_path.exists():
        mtime_after = real_path.stat().st_mtime
        if mtime_after != mtime_before:
            raise AssertionError(
                f"Test modified {real_path} (mtime changed from "
                f"{mtime_before} to {mtime_after}). Redirect "
                "XDG_CONFIG_HOME in your test's setUp or pass an "
                "explicit path= to update_last_model."
            )


@pytest.fixture()
def grc_agent_toml(tmp_home: Path) -> Any:
    """Factory that writes a minimal ``grc_agent.toml`` under ``tmp_home``.

    Usage::

        def test_x(grc_agent_toml):
            path = grc_agent_toml(model_path="/tmp/foo.gguf", device="CPU")
            assert path.is_file()
    """

    def _make(**overrides: Any) -> Path:
        target = tmp_home / ".config" / "grc_agent" / "config.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        body = "[llama]\n"
        body += 'server_url = "http://127.0.0.1:8080"\n'
        body += 'model = "test-model.gguf"\n'
        body += 'hf_model = "test/model:Q4"\n'
        body += 'device = "CPU"\n'
        body += "desired_context_tokens = 120000\n"
        body += "startup_timeout_seconds = 30.0\n"
        body += "max_tokens = 1024\n"
        body += "max_tool_rounds = 4\n"
        body += "temperature = 0.0\n"
        body += "enable_thinking = false\n"
        body += "request_timeout_seconds = 30.0\n"
        if "model_path" in overrides:
            body += f'model_path = "{overrides.pop("model_path")}"\n'
        else:
            body += 'model_path = ""\n'
        if "device" in overrides:
            body += f'device = "{overrides.pop("device")}"\n'
        for key, value in overrides.items():
            body += f"{key} = {value!r}\n"
        body += "\n[agent]\nhistory_compact_budget = 10000\n"
        target.write_text(body, encoding="utf-8")
        return target

    return _make
