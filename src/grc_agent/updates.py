"""Check-for-updates backend and terminal apply helper (copy-command flow).

The GUI never updates itself: ``check_for_updates`` only inspects the Git
checkout and builds a safely quoted shell command. The user closes the app,
pastes the command into a new terminal, and ``apply_update`` revalidates,
backs up settings/sessions, fast-forwards to the reviewed commit,
synchronises ``uv.lock`` with the existing interpreter, and verifies the
native imports — printing a restart invitation only on success.

Resolution is always from this file's own location, never from cwd (GRC
retargets cwd to project directories). Both entry points raise
:exc:`UpdateError` with user-facing messages; remote URLs and tracebacks are
scrubbed so embedded credentials never surface.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .settings import env_path

_UPDATE_BRANCH = "main"
_REMOTE = "origin"
_FETCH_TIMEOUT = 60.0
_GIT_TIMEOUT = 30.0
_SYNC_TIMEOUT = 600.0

_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")
_CRED_RE = re.compile(r"(https?://)[^/\s@]+@")


class UpdateError(Exception):
    """User-facing update failure. The message is safe to display verbatim."""


@dataclass(frozen=True)
class UpdateInfo:
    """Result of a check: identity of the install and the command to copy."""

    root: Path
    current_commit: str
    target_commit: str
    source: str
    command: str

    @property
    def available(self) -> bool:
        """True when the checkout is behind the reviewed remote commit."""
        return self.current_commit != self.target_commit


def installation_root() -> Path:
    """Nearest ancestor of this file containing ``.git``.

    One uniform rule, no cwd involved: a dev checkout resolves to its own
    repo root; an installed package without ``.git`` falls back to the
    source-layout root so the subsequent Git probe fails with a clear
    "not a Git installation" error instead of guessing.
    """
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / ".git").exists():
            return parent
    return here.parent.parent.parent


def install_lock_path() -> Path:
    """Data-dir lock guarding backups against a concurrently running app.

    Lives beside the session DB (gitignored when that is the repo root), so
    it never pollutes ``git status``. The app holds it shared; the updater
    takes it exclusive.
    """
    return env_path().parent / ".grc_agent" / "install.lock"


def _scrub(text: str) -> str:
    """Redact ``https://user:secret@host`` credential prefixes from output."""
    return _CRED_RE.sub(r"\1<redacted>@", text)


def _interpreter_path(root: Path) -> Path:
    return root / ".venv" / "bin" / "python"


def build_update_command(root: Path, target_commit: str) -> str:
    """Safely quoted one-liner that revalidates and applies ``target_commit``.

    The SHA is full hex (``!r`` quoting is exact for that alphabet) and the
    install path goes through :func:`shlex.quote`, so spaces and quotes in
    the path cannot break or inject into the shell line.
    """
    return (
        f"cd {shlex.quote(str(root))} && "
        f".venv/bin/python -c "
        f'"from grc_agent.updates import apply_update; apply_update({target_commit!r})"'
    )


async def _run_git(root: Path, *args: str, timeout: float = _GIT_TIMEOUT) -> str:
    """Run ``git -C root ...``; return stripped stdout or raise UpdateError.

    The subprocess is always reaped: timeouts and cancellations kill it
    before propagating, so the GTK main loop never leaks a zombie that
    holds the repo lock.
    """
    git = shutil.which("git")
    if git is None:
        raise UpdateError(
            "Git is not installed or not on PATH. "
            "Install Git to receive application updates."
        )
    try:
        proc = await asyncio.create_subprocess_exec(
            git,
            "-C",
            str(root),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise UpdateError(f"Could not start Git ({exc}).") from None
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        with contextlib.suppress(Exception):
            proc.kill()
        await proc.wait()
        raise UpdateError(f"Git {' '.join(args)} timed out.") from None
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        err = _scrub(stderr.decode("utf-8", "replace").strip())
        raise UpdateError(f"Git {' '.join(args)} failed: {err or 'unknown error'}")
    return stdout.decode("utf-8", "replace").strip()


def _require_sha(value: str, what: str) -> str:
    value = value.strip()
    if not _SHA_RE.match(value):
        raise UpdateError(f"Refusing to update: {what} is not a commit SHA ({value!r}).")
    return value


async def check_for_updates(root: Path | None = None) -> UpdateInfo:
    """Fetch ``origin`` and compare ``HEAD`` against ``origin/main``.

    Validates the Git install, the ``main`` branch, the configured origin,
    and the existing system-bridged interpreter before any network access.
    Refuses dirty tracked work, ahead histories, and divergences — the
    updater never stashes, resets, cleans, or force-checkouts.
    Untracked files (projects, backups) never block the check.
    """
    repo = Path(root) if root is not None else installation_root()
    if not (repo / ".git").exists():
        raise UpdateError(
            "This installation is not a Git checkout "
            f"({repo} has no .git). Reinstall from the repository to update."
        )
    branch = await _run_git(repo, "symbolic-ref", "--short", "HEAD")
    if branch != _UPDATE_BRANCH:
        raise UpdateError(
            f"Updates are supported from the '{_UPDATE_BRANCH}' branch "
            f"(currently on '{branch}'). Switch to '{_UPDATE_BRANCH}' to update."
        )
    origin_url = await _run_git(repo, "config", "--get", "remote.origin.url")
    if not origin_url:
        raise UpdateError("No 'origin' remote is configured. Cannot check for updates.")
    interpreter = _interpreter_path(repo)
    if not (interpreter.is_file() and os.access(interpreter, os.X_OK)):
        raise UpdateError(
            f"The project interpreter is missing ({interpreter}). "
            "Recreate the virtualenv per the README before updating."
        )
    if shutil.which("uv") is None:
        raise UpdateError(
            "The 'uv' tool is not on PATH. Install uv to apply dependency updates."
        )
    try:
        await _run_git(repo, "fetch", _REMOTE, timeout=_FETCH_TIMEOUT)
    except UpdateError as exc:
        raise UpdateError(f"Could not reach {_REMOTE}: {exc}") from None
    current = _require_sha(await _run_git(repo, "rev-parse", "HEAD"), "HEAD")
    target = _require_sha(
        await _run_git(repo, "rev-parse", f"{_REMOTE}/{_UPDATE_BRANCH}"), "origin/main"
    )
    porcelain = await _run_git(repo, "status", "--porcelain=v1")
    dirty = [
        line
        for line in porcelain.splitlines()
        if line and not line.startswith("??") and not line.startswith("!!")
    ]
    if dirty:
        preview = ", ".join(dirty[:5])
        if len(dirty) > 5:
            preview += f" (+{len(dirty) - 5} more)"
        raise UpdateError(
            "Uncommitted changes present (" + preview + "). "
            "Save, commit, or discard them before updating — "
            "the updater never stashes or discards work."
        )
    counts = await _run_git(
        repo, "rev-list", "--left-right", "--count", f"HEAD...{_REMOTE}/{_UPDATE_BRANCH}"
    )
    try:
        ahead_s, behind_s = counts.split()
        ahead, behind = int(ahead_s), int(behind_s)
    except ValueError:
        raise UpdateError(
            "Could not compare local history with origin/main."
        ) from None
    if ahead > 0 and behind > 0:
        raise UpdateError(
            "Local and origin/main histories have diverged. "
            "Reconcile manually before updating — the updater never rebases."
        )
    if ahead > 0:
        raise UpdateError(
            "Local branch has commits not on origin/main. "
            "Push or discard them before updating."
        )
    return UpdateInfo(
        root=repo,
        current_commit=current,
        target_commit=target,
        source=f"{_REMOTE}/{_UPDATE_BRANCH}",
        command=build_update_command(repo, target),
    )


def _run_blocking(
    cmd: list[str], cwd: Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    """Synchronous subprocess for the terminal helper (no event loop there)."""
    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise UpdateError(f"Required tool is missing: {cmd[0]}.") from None
    except subprocess.TimeoutExpired:
        raise UpdateError(f"'{' '.join(cmd)}' timed out.") from None


def _atomic_write_text(path: Path, data: str) -> None:
    """Write ``data`` atomically: temp file, fsync, ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _back_up_state(previous: str, target: str) -> Path:
    """Copy ``.env`` bytes and the session DB into a private timestamped dir.

    The DB travels through ``sqlite3.Connection.backup`` (the standard
    online-backup API), so a concurrent reader cannot leave a torn copy;
    the running-app lock makes a concurrent writer impossible.
    Returns the backup directory.
    """
    env_file = env_path()
    base = env_file.parent
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = base / ".grc_agent" / "backups" / stamp
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(backup_dir, 0o700)
    if env_file.is_file():
        _atomic_write_text(
            backup_dir / ".env",
            env_file.read_text(encoding="utf-8", errors="replace"),
        )
    db_path = base / ".grc_agent" / "chat_sessions.db"
    if db_path.is_file():
        src = sqlite3.connect(str(db_path))
        try:
            dst = sqlite3.connect(str(backup_dir / "chat_sessions.db"))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    _atomic_write_text(
        backup_dir / "update.json",
        json.dumps(
            {"previous_commit": previous, "target_commit": target, "taken_at": stamp},
            indent=2,
        ),
    )
    return backup_dir


def apply_update(target_commit: str, root: Path | None = None) -> None:
    """Apply a previously reviewed ``target_commit`` from a terminal.

    Callable as ``.venv/bin/python -c "from grc_agent.updates import
    apply_update; apply_update('<sha>')"`` — no new ``grc-agent`` CLI
    surface. Revalidates everything :func:`check_for_updates` checked,
    refuses stale commands (``origin/main`` moved since the copy), backs up
    settings/sessions, fast-forwards to exactly ``target_commit``, syncs
    ``uv.lock`` with the existing interpreter, and verifies the native
    imports. Prints a restart invitation only on full success.
    """
    target = _require_sha(target_commit, "target")
    repo = Path(root) if root is not None else installation_root()
    if not (repo / ".git").exists():
        raise UpdateError(f"{repo} is not a Git checkout (no .git).")
    if shutil.which("git") is None:
        raise UpdateError("Git is not installed or not on PATH.")
    interpreter = _interpreter_path(repo)
    if not (interpreter.is_file() and os.access(interpreter, os.X_OK)):
        raise UpdateError(f"The project interpreter is missing ({interpreter}).")
    if shutil.which("uv") is None:
        raise UpdateError("The 'uv' tool is not on PATH.")

    lock_file = install_lock_path()
    lock_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # The fd must stay open across _apply_locked so the exclusive lock is
    # held for the whole backup+merge+sync sequence.
    fh = open(lock_file, "a", encoding="utf-8")  # noqa: SIM115
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UpdateError(
                "Close grc-agent before updating (the app is still running)."
            ) from None
        try:
            _apply_locked(target, repo, interpreter)
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def _apply_locked(target: str, repo: Path, interpreter: Path) -> None:
    branch = _git_blocking(repo, "symbolic-ref", "--short", "HEAD")
    if branch != _UPDATE_BRANCH:
        raise UpdateError(
            f"Refusing to update from branch '{branch}' "
            f"(expected '{_UPDATE_BRANCH}')."
        )
    if not _git_blocking(repo, "config", "--get", "remote.origin.url"):
        raise UpdateError("No 'origin' remote is configured.")
    fetch = _run_blocking(["git", "-C", str(repo), "fetch", _REMOTE], repo, _FETCH_TIMEOUT)
    if fetch.returncode != 0:
        raise UpdateError(f"Could not reach {_REMOTE}: {_scrub(fetch.stdout.strip())}")
    current = _require_sha(_git_blocking(repo, "rev-parse", "HEAD"), "HEAD")
    fresh = _require_sha(_git_blocking(repo, "rev-parse", f"{_REMOTE}/{_UPDATE_BRANCH}"), "origin/main")
    if fresh != target:
        raise UpdateError(
            "This update command is stale: origin/main is now "
            f"{fresh[:12]} but the command targets {target[:12]}. "
            "Check for updates again and copy the fresh command."
        )
    if current == target:
        print(f"Already up to date ({target[:12]}). Nothing to do.")
        return
    porcelain = _git_blocking(repo, "status", "--porcelain=v1")
    dirty = [
        line
        for line in porcelain.splitlines()
        if line and not line.startswith("??") and not line.startswith("!!")
    ]
    if dirty:
        raise UpdateError(
            "Uncommitted changes present. Save, commit, or discard them "
            "before updating — the updater never stashes or discards work."
        )
    counts = _git_blocking(
        repo, "rev-list", "--left-right", "--count", f"HEAD...{_REMOTE}/{_UPDATE_BRANCH}"
    )
    try:
        ahead_s, behind_s = counts.split()
    except ValueError:
        raise UpdateError("Could not compare local history with origin/main.") from None
    if int(ahead_s) > 0:
        raise UpdateError(
            "Local branch has commits not on origin/main (or histories "
            "diverged). Reconcile manually before updating."
        )
    backup_dir = _back_up_state(current, target)
    print(f"Backed up settings and sessions to {backup_dir}.")
    merge = _run_blocking(
        ["git", "-C", str(repo), "merge", "--ff-only", target], repo, _GIT_TIMEOUT
    )
    if merge.returncode != 0:
        raise UpdateError(
            "Fast-forward failed (your files were left untouched): "
            f"{_scrub(merge.stdout.strip()) or 'unknown error'} "
            f"Backups are at {backup_dir}."
        )
    sync = _run_blocking(
        ["uv", "sync", "--extra", "dev", "--locked", "--python", ".venv/bin/python"],
        repo,
        _SYNC_TIMEOUT,
    )
    if sync.returncode != 0:
        tail = _scrub(sync.stdout.strip())[-2000:]
        raise UpdateError(
            "Dependency sync failed after the code update. "
            f"Backups are at {backup_dir}. Details: {tail or 'unknown error'}"
        )
    verify = _run_blocking(
        [str(interpreter), "-c", "import gi; import gnuradio.gr"],
        repo,
        _GIT_TIMEOUT,
    )
    if verify.returncode != 0:
        raise UpdateError(
            "Post-update verification failed (gi/gnuradio imports). "
            f"Backups are at {backup_dir}. Details: {_scrub(verify.stdout.strip())}"
        )
    print(
        f"Update complete: {current[:12]} -> {target[:12]}. "
        f"Backups are at {backup_dir}. You can now restart grc-agent."
    )


def _git_blocking(repo: Path, *args: str) -> str:
    """Blocking ``git -C repo`` used by the terminal helper."""
    completed = _run_blocking(["git", "-C", str(repo), *args], repo, _GIT_TIMEOUT)
    if completed.returncode != 0:
        raise UpdateError(
            f"Git {' '.join(args)} failed: "
            f"{_scrub(completed.stdout.strip()) or 'unknown error'}"
        )
    return completed.stdout.strip()
