"""Tests for the GrcShell capability/toolset — the narrowed harness Shell
adoption (dynamic project cwd, approval-gated exec tools, catalog-derived
env scrubbing, harness denylist defaults).

Hermetic: no display, no LLM, no GTK. Real local subprocesses only (echo,
printenv, pwd, sleep) spawned inside a tmp project directory.
"""

import asyncio
import fnmatch

import pytest
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai_harness.shell import LLM_API_KEY_ENV_PATTERNS
from pydantic_ai_harness.shell._capability import _DEFAULT_DENIED_COMMANDS

import grc_agent.fs_tools as fs_tools
from grc_agent.shell_tools import (
    GrcShell,
    GrcShellToolset,
    derive_env_deny_patterns,
)


@pytest.fixture(autouse=True)
def _default_loop_policy():
    """Force the plain asyncio policy for this module.

    These tests call ``asyncio.run`` directly, and an earlier module in the
    full-suite order (test_desktop_app) installs the app's gbulb/GLib policy
    process-wide. Under that inherited policy ``asyncio.run`` runs the shared
    default GLib loop via run_until_complete — with leftover Gtk state from
    build_app that path never dispatches the subprocess pipe I/O and hangs
    (verified by faulthandler; the REAL app path — a coroutine already
    running on the live unified loop — was verified working separately).
    A fresh default policy gives every test a clean selector loop.
    """
    saved = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    yield
    asyncio.set_event_loop_policy(saved)


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    """Point the fs_tools providers at a tmp project dir (the shell cwd
    source), with no active flowgraph — same precedence as the fs sandbox."""
    monkeypatch.setattr(fs_tools, "_project_dir_fn", lambda: tmp_path)
    monkeypatch.setattr(fs_tools, "_active_grc_path_fn", lambda: None)
    monkeypatch.setattr(fs_tools, "_active_flow_graph_fn", lambda: None)
    return tmp_path


@pytest.fixture
def no_project(monkeypatch):
    monkeypatch.setattr(fs_tools, "_project_dir_fn", lambda: None)
    monkeypatch.setattr(fs_tools, "_active_grc_path_fn", lambda: None)


def _toolset(**kwargs):
    return GrcShellToolset(**kwargs) if kwargs else GrcShell().get_toolset()


class _Ctx:
    pass


# --- identity: for_run must keep the subclass (the base impl drops it) ---


def test_for_run_preserves_subclass_and_resolves_cwd_per_run(project_root, tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    other.mkdir()
    ts = _toolset()
    assert ts._cwd == project_root
    # Switch the project between construction and run start: for_run (and
    # every spawn) must follow the new provider value.
    monkeypatch.setattr(fs_tools, "_project_dir_fn", lambda: other)
    fr = asyncio.run(ts.for_run(_Ctx()))
    assert isinstance(fr, GrcShellToolset)
    assert fr._cwd == other


def test_exec_tools_carry_requires_approval_on_init_and_for_run():
    ts = _toolset()
    assert ts.tools["run_command"].requires_approval is True
    assert ts.tools["start_command"].requires_approval is True
    assert ts.tools["check_command"].requires_approval is False
    assert ts.tools["stop_command"].requires_approval is False
    fr = asyncio.run(ts.for_run(_Ctx()))
    assert fr.tools["run_command"].requires_approval is True
    assert fr.tools["start_command"].requires_approval is True


# --- gating ---


@pytest.mark.usefixtures("no_project")
def test_no_project_dir_gates_exec_tools_with_modelretry():
    ts = _toolset()
    with pytest.raises(ModelRetry, match="No project directory is set"):
        asyncio.run(ts.run_command("echo hi"))
    with pytest.raises(ModelRetry, match="No project directory is set"):
        asyncio.run(ts.start_command("echo hi"))


@pytest.mark.usefixtures("project_root")
def test_denied_command_raises_modelretry():
    ts = _toolset()
    assert "rm" in ts._denied_commands
    with pytest.raises(ModelRetry, match="denied"):
        asyncio.run(ts.run_command("rm -rf /tmp/nothing-here"))


@pytest.mark.usefixtures("project_root")
def test_interactive_commands_blocked():
    ts = _toolset()
    with pytest.raises(ModelRetry, match="Interactive"):
        asyncio.run(ts.run_command("vim /tmp/x"))


@pytest.mark.usefixtures("project_root")
def test_mutual_exclusion_inherited():
    with pytest.raises(ValueError, match="not both"):
        GrcShellToolset(allowed_commands=["echo"], denied_commands=["rm"])


@pytest.mark.usefixtures("project_root")
def test_default_denylist_matches_harness_defaults():
    ts = _toolset()
    assert ts._denied_commands == list(_DEFAULT_DENIED_COMMANDS)


# --- env scrubbing ---


@pytest.mark.usefixtures("project_root")
def test_env_scrubbed_at_spawn(monkeypatch):
    monkeypatch.setenv("OLLAMA_CLOUD_API_KEY", "sekrit-ollama-cloud")
    monkeypatch.setenv("XAI_API_KEY", "sekrit-xai")
    monkeypatch.setenv("GRCSHELL_PROBE", "visible-control")
    ts = _toolset()
    out = asyncio.run(ts.run_command("printenv"))
    assert "sekrit-ollama-cloud" not in out
    assert "sekrit-xai" not in out
    assert "visible-control" in out  # non-secret env still flows through


def test_derived_patterns_cover_the_provider_catalog():
    from grc_agent.ui.providers import PROVIDER_API_KEY

    patterns = derive_env_deny_patterns()
    for name in {v for v in PROVIDER_API_KEY.values() if v} | {"OLLAMA_CLOUD_API_KEY"}:
        assert any(fnmatch.fnmatchcase(name, p) for p in patterns), name


def test_harness_llm_pattern_gap_is_documented():
    """The harness list alone misses this app's default provider family —
    the reason the derived list exists. Fails loudly if a harness upgrade
    closes the gap (then simplify back to the harness list)."""
    missing = {
        "OLLAMA_API_KEY",
        "OLLAMA_CLOUD_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "COHERE_API_KEY",
        "XAI_API_KEY",
    }
    for name in missing:
        assert not any(fnmatch.fnmatchcase(name, p) for p in LLM_API_KEY_ENV_PATTERNS), name


# --- cwd behavior ---


def test_cwd_is_project_dir_and_not_persisted(project_root):
    ts = _toolset()
    first = asyncio.run(ts.run_command("pwd"))
    assert str(project_root) in first
    # persist_cwd=False (default): a cd inside one call never sticks.
    asyncio.run(ts.run_command("cd / && pwd"))
    second = asyncio.run(ts.run_command("pwd"))
    assert str(project_root) in second
    assert second.strip() != "/"


def test_cwd_follows_provider_change_between_calls(tmp_path, monkeypatch):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setattr(fs_tools, "_project_dir_fn", lambda: a)
    monkeypatch.setattr(fs_tools, "_active_grc_path_fn", lambda: None)
    ts = _toolset()
    assert str(a) in asyncio.run(ts.run_command("pwd"))
    monkeypatch.setattr(fs_tools, "_project_dir_fn", lambda: b)
    assert str(b) in asyncio.run(ts.run_command("pwd"))


# --- execution basics ---


@pytest.mark.usefixtures("project_root")
def test_run_command_captures_output_and_exit_code():
    ts = _toolset()
    out = asyncio.run(ts.run_command("echo hello-grc"))
    assert "hello-grc" in out and "[stdout]" in out
    out = asyncio.run(ts.run_command("false"))
    assert "[exit code: 1]" in out


@pytest.mark.usefixtures("project_root")
def test_background_lifecycle():
    async def main():
        ts = _toolset()
        started = await ts.start_command("sleep 30")
        assert "ID:" in started
        cid = started.split("ID:")[1].strip()
        checked = await ts.check_command(cid)
        stopped = await ts.stop_command(cid)
        return checked, stopped

    checked, stopped = asyncio.run(main())
    assert "[status: running]" in checked
    assert "[stopped]" in stopped


@pytest.mark.usefixtures("project_root")
def test_aexit_cleans_up_leaked_background_processes():
    async def main():
        ts = _toolset()
        async with ts:
            await ts.start_command("sleep 30")
            await ts.start_command("sleep 30")
            assert len(ts._background) == 2
        assert ts._background == {}

    asyncio.run(main())


# --- user-tunable knobs via .env ---


def test_denied_commands_env_override(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("GRC_SHELL_DENIED_COMMANDS=rm,my-custom-danger\n")
    monkeypatch.setenv("GRC_AGENT_ENV", str(env_file))
    cap = GrcShell()
    assert cap.denied_commands == ["rm", "my-custom-danger"]


def test_denied_commands_env_empty_disables(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("GRC_SHELL_DENIED_COMMANDS=\n")
    monkeypatch.setenv("GRC_AGENT_ENV", str(env_file))
    assert GrcShell().denied_commands == []


def test_timeout_env_override(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("GRC_SHELL_TIMEOUT=12.5\n")
    monkeypatch.setenv("GRC_AGENT_ENV", str(env_file))
    assert GrcShell().default_timeout == 12.5
