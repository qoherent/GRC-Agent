"""Shared pytest fixtures for the GRC Agent test suite.

These fixtures are auto-discovered by pytest but are also importable from
`unittest`-style tests when run under the pytest runner.
"""

from __future__ import annotations

import os

os.environ["GRC_AGENT_TESTING"] = "true"

# Redirect the vector DB to a per-session tmp dir BEFORE any test module
# imports ``grc_agent.runtime.doc_answer`` (which captures DB_PATH at
# module load). This keeps production code paths — including the explicit
# ``GrcAgent.warmup_vector_index()`` call wired into the CLI/GUI —
# exercisable from tests without ever touching the real
# ``.grc_agent/vectors/docs_v1.db``. The ``_guard_real_vector_db``
# fixture below is the mechanical backstop that verifies the real path
# is never reached.
_SESSION_VECTORS_DIR = os.environ.get("GRC_AGENT_TEST_VECTORS_DIR")
if not _SESSION_VECTORS_DIR:
    import tempfile as _tf

    _SESSION_VECTORS_DIR = _tf.mkdtemp(prefix="grc_agent_test_vectors_")
os.environ.setdefault("GRC_AGENT_VECTORS_DIR", _SESSION_VECTORS_DIR)

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
    """Fail if any test modifies or creates the real ``preferences.json``."""
    real_path = Path.home() / ".config" / "grc_agent" / "preferences.json"
    existed_before = real_path.exists()
    mtime_before: float | None = None
    if existed_before:
        mtime_before = real_path.stat().st_mtime
    yield
    if real_path.exists():
        if not existed_before:
            raise AssertionError(
                f"Test created {real_path}. Redirect XDG_CONFIG_HOME in your test setUp."
            )
        if real_path.stat().st_mtime != mtime_before:
            raise AssertionError(
                f"Test modified {real_path} (mtime changed from "
                f"{mtime_before} to {real_path.stat().st_mtime}). Redirect "
                "XDG_CONFIG_HOME in your test setUp."
            )


@pytest.fixture(autouse=True)
def _guard_real_vector_db(request: Any) -> Any:
    """Fail if any test touches the real production vector DB.

    Audit finding S1: ``GrcAgent.__init__`` used to spawn an ingestion
    thread unconditionally, which wrote mock-polluted vectors into the
    real ``.grc_agent/vectors/docs_v1.db`` whenever a test instantiated
    an agent with ``get_embedding`` patched. Ingestion has since moved
    to an explicit ``GrcAgent.warmup_vector_index()`` that tests never
    call. This fixture is the mechanical backstop: it asserts the
    production DB is not created or modified by any non-live test.

    Tests that need a real ingest must redirect via the
    ``GRC_AGENT_VECTORS_DIR`` env var or mark themselves ``live_rag``
    (and only run under ``GRC_AGENT_LIVE_RAG=1``).
    """
    if request.node.get_closest_marker("live_rag"):
        yield
        return

    # Always check the REAL production path — not whatever
    # GRC_AGENT_VECTORS_DIR points at (this conftest redirects that env
    # var to a per-session tmp dir above, so the redirected path is the
    # one tests are allowed to touch).
    db_path = Path(".grc_agent") / "vectors" / "docs_v1.db"

    def _snapshot() -> tuple[bool, float | None]:
        if not db_path.exists():
            return (False, None)
        return (True, db_path.stat().st_mtime_ns)

    before = _snapshot()
    yield
    after = _snapshot()
    if after != before:
        raise AssertionError(
            f"Test touched the production vector DB at {db_path} "
            f"(before={before}, after={after}). Tests must not call "
            f"GrcAgent.warmup_vector_index() or otherwise reach the real "
            f"DB_PATH. Redirect via GRC_AGENT_VECTORS_DIR or import the "
            f"live-integration tests under tests/retrieval_eval/."
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
