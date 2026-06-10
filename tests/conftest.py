"""Shared pytest fixtures for the GRC Agent test suite.

These fixtures are auto-discovered by pytest but are also importable from
`unittest`-style tests when run under the pytest runner.
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
    """Fail if any test modifies the real ``preferences.json``."""
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
                "XDG_CONFIG_HOME in your test setUp."
            )


@pytest.fixture()
def grc_agent_toml(tmp_home: Path) -> Any:
    """Factory that writes a minimal ``grc_agent.toml`` under ``tmp_home``."""

    def _make(**overrides: Any) -> Path:
        target = tmp_home / ".config" / "grc_agent" / "config.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        body = "[llama]\n"
        body += 'server_url = "http://localhost:11434"\n'
        body += 'model = "test-model"\n'
        body += 'backend = "ollama"\n'
        body += "max_tokens = 1024\n"
        body += "max_tool_rounds = 4\n"
        body += "temperature = 0.0\n"
        body += "enable_thinking = false\n"
        body += "request_timeout_seconds = 30.0\n"
        for key, value in overrides.items():
            body += f"{key} = {value!r}\n"
        body += "\n[agent]\nhistory_compact_budget = 10000\n"
        target.write_text(body, encoding="utf-8")
        return target

    return _make
