"""Hermetic tests for the copy-command update flow (grc_agent.updates).

Every behavior runs against throwaway local Git repositories — no network,
no mocks of the Git layer itself. ``uv`` and the project interpreter are
stand-in executables on a prepended PATH dir, which keeps the subprocess
seams real (exit codes, timeouts) without touching the network.
"""

import os
import shlex
import shutil
import sqlite3
import stat
import subprocess
from pathlib import Path

import pytest

from grc_agent import updates
from grc_agent.updates import (
    UpdateError,
    apply_update,
    build_update_command,
    check_for_updates,
    install_lock_path,
)


def _git(*args: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, f"git {args}: {completed.stdout}"
    return completed.stdout.strip()


def _git_commit(repo: Path, name: str, content: str, message: str) -> None:
    (repo / name).write_text(content, encoding="utf-8")
    _git("add", name, cwd=repo)
    _git(
        "-c",
        "user.email=t@t.invalid",
        "-c",
        "user.name=t",
        "commit",
        "-m",
        message,
        cwd=repo,
    )


def _make_origin(base: Path) -> Path:
    """A normal (non-bare) repo acting as the fetchable remote."""
    origin = base / "origin"
    origin.mkdir(parents=True)
    try:
        _git("init", "-b", "main", cwd=origin)
    except AssertionError:
        _git("init", cwd=origin)
        _git("checkout", "-b", "main", cwd=origin)
    _git_commit(origin, "tracked.txt", "v1\n", "v1")
    return origin


def _clone(origin: Path, dest: Path) -> Path:
    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)
    _git("clone", str(origin), dest.name, cwd=parent)
    return dest


def _head(repo: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=repo)


def _fake_bin(bin_dir: Path, uv_exit: int = 0) -> None:
    """Stand-in ``uv`` (+ project interpreter elsewhere) for apply tests."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        f"echo 'fake uv sync ok'\n"
        f"exit {uv_exit}\n",
        encoding="utf-8",
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_interpreter(clone: Path) -> Path:
    interp = clone / ".venv" / "bin" / "python"
    interp.parent.mkdir(parents=True, exist_ok=True)
    interp.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    interp.chmod(interp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return interp


@pytest.fixture
def updated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated origin+clone pair with PATH uv and interpreter in place."""
    base = tmp_path / "repos"
    origin = _make_origin(base)
    clone = _clone(origin, base / "clone")
    _fake_interpreter(clone)
    bin_dir = tmp_path / "bin"
    _fake_bin(bin_dir)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GRC_AGENT_ENV", str(clone / ".env"))
    return clone


def _origin_of(clone: Path) -> Path:
    url = _git("config", "--get", "remote.origin.url", cwd=clone)
    return Path(url)


@pytest.mark.asyncio
async def test_check_up_to_date(updated_env: Path) -> None:
    info = await check_for_updates(updated_env)
    assert not info.available
    assert info.current_commit == info.target_commit == _head(updated_env)
    assert info.source == "origin/main"
    assert info.root == updated_env


@pytest.mark.asyncio
async def test_check_available_builds_quoted_command(updated_env: Path) -> None:
    origin = _origin_of(updated_env)
    _git_commit(origin, "tracked.txt", "v2\n", "v2")
    info = await check_for_updates(updated_env)
    assert info.available
    assert info.target_commit == _head(origin)
    assert info.current_commit != info.target_commit
    assert info.target_commit in info.command
    assert shlex.quote(str(updated_env)) in info.command
    assert "apply_update" in info.command


@pytest.mark.asyncio
async def test_check_untracked_files_do_not_block(updated_env: Path) -> None:
    (updated_env / "notes.txt").write_text("scratch", encoding="utf-8")
    info = await check_for_updates(updated_env)
    assert not info.available


@pytest.mark.asyncio
async def test_check_dirty_tracked_refused(updated_env: Path) -> None:
    (updated_env / "tracked.txt").write_text("local edit\n", encoding="utf-8")
    with pytest.raises(UpdateError, match="Uncommitted changes"):
        await check_for_updates(updated_env)
    _git("checkout", "--", "tracked.txt", cwd=updated_env)
    (updated_env / "tracked.txt").write_text("staged edit\n", encoding="utf-8")
    _git("add", "tracked.txt", cwd=updated_env)
    with pytest.raises(UpdateError, match="Uncommitted changes"):
        await check_for_updates(updated_env)


@pytest.mark.asyncio
async def test_check_ahead_refused(updated_env: Path) -> None:
    _git_commit(updated_env, "tracked.txt", "local only\n", "local")
    with pytest.raises(UpdateError, match="not on origin/main"):
        await check_for_updates(updated_env)


@pytest.mark.asyncio
async def test_check_diverged_refused(updated_env: Path) -> None:
    _git_commit(updated_env, "tracked.txt", "local only\n", "local")
    _git_commit(_origin_of(updated_env), "tracked.txt", "remote v2\n", "v2")
    with pytest.raises(UpdateError, match="[Dd]iverged"):
        await check_for_updates(updated_env)


@pytest.mark.asyncio
async def test_check_wrong_branch_refused(updated_env: Path) -> None:
    _git("checkout", "-b", "feature", cwd=updated_env)
    with pytest.raises(UpdateError, match="main"):
        await check_for_updates(updated_env)


@pytest.mark.asyncio
async def test_check_missing_interpreter_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "repos"
    clone = _clone(_make_origin(base), base / "clone")
    monkeypatch.setenv("GRC_AGENT_ENV", str(clone / ".env"))
    with pytest.raises(UpdateError, match="interpreter is missing"):
        await check_for_updates(clone)


@pytest.mark.asyncio
async def test_check_missing_uv_refused(
    updated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_which = shutil.which

    def _which(cmd: str, *args: object, **kwargs: object) -> str | None:
        if cmd == "uv":
            return None
        return real_which(cmd, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", _which)
    with pytest.raises(UpdateError, match="'uv'"):
        await check_for_updates(updated_env)


@pytest.mark.asyncio
async def test_check_missing_git_refused(
    updated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_which = shutil.which

    def _which(cmd: str, *args: object, **kwargs: object) -> str | None:
        if cmd == "git":
            return None
        return real_which(cmd, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", _which)
    with pytest.raises(UpdateError, match="Git is not installed"):
        await check_for_updates(updated_env)


@pytest.mark.asyncio
async def test_path_with_space_is_quoted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = tmp_path / "with space"
    origin = _make_origin(base)
    clone = _clone(origin, base / "clone dir")
    _fake_interpreter(clone)
    bin_dir = tmp_path / "bin"
    _fake_bin(bin_dir)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GRC_AGENT_ENV", str(clone / ".env"))
    info = await check_for_updates(clone)
    assert shlex.quote(str(clone)) in info.command


def test_build_update_command_quotes_root(tmp_path: Path) -> None:
    root = tmp_path / "a'b dir"
    cmd = build_update_command(root, "a" * 40)
    assert cmd.startswith(f"cd {shlex.quote(str(root))} && ")
    assert "a" * 40 in cmd


@pytest.mark.asyncio
async def test_error_scrubs_remote_credentials(updated_env: Path) -> None:
    secret = "s3cret-token-xyz"
    _git(
        "remote",
        "set-url",
        "origin",
        f"http://user:{secret}@127.0.0.1:9/repo.git",
        cwd=updated_env,
    )
    with pytest.raises(UpdateError) as excinfo:
        await check_for_updates(updated_env)
    assert secret not in str(excinfo.value)


def test_apply_fast_forward_preserves_data(
    updated_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = updated_env
    origin = _origin_of(clone)
    # User state: settings file, session DB with a row, untracked project.
    env_file = Path(os.environ["GRC_AGENT_ENV"])
    env_file.write_text("GRC_PROVIDER=ollama_local\n", encoding="utf-8")
    db_path = env_file.parent / ".grc_agent" / "chat_sessions.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO sessions (name) VALUES ('keep-me')")
    conn.commit()
    conn.close()
    project = clone / "work" / "my.grc"
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_bytes(b"<grc project bytes>")
    before_head = _head(clone)

    _git_commit(origin, "tracked.txt", "v2\n", "v2")
    target = _head(origin)

    apply_update(target, clone)

    assert _head(clone) == target != before_head
    assert (clone / "tracked.txt").read_text(encoding="utf-8") == "v2\n"
    # Settings, sessions, and project bytes are byte-identical.
    assert env_file.read_text(encoding="utf-8") == "GRC_PROVIDER=ollama_local\n"
    check = sqlite3.connect(str(db_path))
    assert check.execute("SELECT name FROM sessions").fetchall() == [("keep-me",)]
    check.close()
    assert project.read_bytes() == b"<grc project bytes>"
    # Backups hold the same contents plus provenance metadata.
    backups = sorted((env_file.parent / ".grc_agent" / "backups").iterdir())
    assert len(backups) == 1
    assert (backups[0] / ".env").read_text(encoding="utf-8") == "GRC_PROVIDER=ollama_local\n"
    bconn = sqlite3.connect(str(backups[0] / "chat_sessions.db"))
    assert bconn.execute("SELECT name FROM sessions").fetchall() == [("keep-me",)]
    bconn.close()
    out = capsys.readouterr().out
    assert "Backed up settings and sessions" in out
    assert "restart grc-agent" in out


@pytest.mark.asyncio
async def test_apply_stale_command_refused(updated_env: Path) -> None:
    clone = updated_env
    origin = _origin_of(clone)
    _git_commit(origin, "tracked.txt", "v2\n", "v2")
    stale = _head(origin)
    _git_commit(origin, "tracked.txt", "v3\n", "v3")
    before = _head(clone)
    info = await check_for_updates(clone)
    assert info.target_commit != stale
    with pytest.raises(UpdateError, match="[Ss]tale"):
        apply_update(stale, clone)
    assert _head(clone) == before


def test_apply_dirty_refused(updated_env: Path) -> None:
    origin = _origin_of(updated_env)
    _git_commit(origin, "tracked.txt", "v2\n", "v2")
    (updated_env / "tracked.txt").write_text("local edit\n", encoding="utf-8")
    before = _head(updated_env)
    with pytest.raises(UpdateError, match="Uncommitted changes"):
        apply_update(_head(origin), updated_env)
    assert _head(updated_env) == before


def test_apply_sync_failure_reports_backups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "repos"
    origin = _make_origin(base)
    clone = _clone(origin, base / "clone")
    _fake_interpreter(clone)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text("#!/bin/sh\necho 'boom'\nexit 1\n", encoding="utf-8")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GRC_AGENT_ENV", str(clone / ".env"))
    _git_commit(origin, "tracked.txt", "v2\n", "v2")
    with pytest.raises(UpdateError, match="[Dd]ependency sync failed"):
        apply_update(_head(origin), clone)
    # The code moved (merge succeeded) but settings backups were still taken.
    assert _head(clone) == _head(origin)
    assert list((clone / ".grc_agent" / "backups").iterdir())


def test_apply_refuses_while_app_holds_lock(updated_env: Path) -> None:
    import fcntl

    origin = _origin_of(updated_env)
    _git_commit(origin, "tracked.txt", "v2\n", "v2")
    lock = install_lock_path()
    lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    before = _head(updated_env)
    with open(lock, "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
        with pytest.raises(UpdateError, match="still running"):
            apply_update(_head(origin), updated_env)
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    assert _head(updated_env) == before


def test_apply_rejects_malformed_sha(updated_env: Path) -> None:
    with pytest.raises(UpdateError, match="not a commit SHA"):
        apply_update("not-a-sha", updated_env)


def test_installation_root_resolves_from_module() -> None:
    root = updates.installation_root()
    assert (root / ".git").exists()
    assert (root / "pyproject.toml").is_file()
