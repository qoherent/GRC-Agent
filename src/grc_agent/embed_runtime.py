"""Optional local llama.cpp runtime serving EmbeddingGemma for RAG.

Without this, vector search requires a user-installed Ollama with
`embeddinggemma` pulled; when that is absent the knowledge base silently
degrades to lexical (FTS5/BM25) search. This module provisions a pinned
llama.cpp build and the EmbeddingGemma GGUF into an XDG data directory and
runs `llama-server` against them, so embeddings work with nothing installed
system-wide.

Ported from the `whatisit-nl2sh` project, keeping the parts that are
load-bearing rather than incidental:

* **Decide before downloading.** `runtime_plan()` resolves the platform, libc
  flavour, and glibc version *first*. The failure it exists to prevent is a
  successful download of a binary that cannot start.
* **Verify before rename.** `download()` streams to `<dest>.part`, hashes
  while writing, and only `replace()`s into place once the digest matches, so
  an interrupted or corrupted transfer never leaves a file that later fails
  as a confusing error from llama.cpp.
* **Keep the extracted directory whole.** The binaries resolve their sibling
  `.so` files through `RUNPATH=$ORIGIN`, so `extract_runtime()` returns the
  directory containing `llama-server` rather than copying the binary out.
* **UNIX socket, not a TCP port.** On a multi-user box loopback is shared
  across UIDs, and the bind(0)-probe-then-start pattern leaves a window for a
  co-tenant to claim the port and answer in our place. A socket inside a 0700
  directory removes that class of problem entirely.

Nothing here is a Python dependency: the runtime and the model are fetched at
first use with stdlib `urllib`, not `huggingface_hub`.

Provisioning is driven from the Settings dialog — this project is GUI-only, so
there is no `setup` subcommand. The functions here are blocking and are called
from worker threads (`asyncio.to_thread`), never on the GTK main loop.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import httpx

_log = logging.getLogger(__name__)

# Pinned rather than tracked as "latest": a new upstream build can change
# behaviour without warning, and this is the one that has been tested.
LLAMA_BUILD = "b10333"
LLAMA_REPO = "ggml-org/llama.cpp"

# The prebuilt Linux binaries are built against glibc 2.34. Below that they
# download fine and then die with "GLIBC_2.34 not found" on first use, which
# tells the user nothing. Detect it up front instead.
MIN_GLIBC = (2, 34)

# Platform -> upstream asset suffix. Anything absent has no prebuilt archive.
ASSET_SUFFIX = {
    ("Linux", "x86_64"): "ubuntu-x64",
    ("Linux", "aarch64"): "ubuntu-arm64",
    ("Darwin", "arm64"): "macos-arm64",
    ("Darwin", "x86_64"): "macos-x64",
}

# Download size of each archive, for the confirmation prompt. These are the
# compressed tarballs, which is what the progress callback counts, so the
# figure offered and the figure shown agree. Tied to LLAMA_BUILD — update
# together.
ASSET_BYTES = {
    ("Linux", "x86_64"): 16_507_165,
    ("Linux", "aarch64"): 13_377_770,
    ("Darwin", "arm64"): 11_015_270,
    ("Darwin", "x86_64"): 11_290_712,
}

# Rough extracted size of the runtime, for the disk-space check.
RUNTIME_BYTES = 120_000_000

# The QAT GGUF published by ggml-org. Some community quants of
# embeddinggemma-300m drift from the reference `transformers` output; this one
# is the reference-quality build. sha256/size come from Hugging Face's
# x-linked-etag / x-linked-size, which are the LFS object's own digest, so they
# are pinned here without needing to download the file to learn them.
MODEL = {
    "repo": "ggml-org/embeddinggemma-300m-qat-q8_0-GGUF",
    "file": "embeddinggemma-300m-qat-Q8_0.gguf",
    "sha256": "6fa0c02a9c302be6f977521d399b4de3a46310a4f2621ee0063747881b673f67",
    "size": 328_577_056,
}

# Identity recorded in the vector DB's `_db_meta.embedding_model`. Deliberately
# distinct from Ollama's "embeddinggemma:latest" for the same weights, so
# switching backends is detected as stale and triggers a rebuild rather than
# querying one model's index with another model's vectors.
EMBED_MODEL_ID = "llamacpp/embeddinggemma-300m-qat-Q8_0"

# EmbeddingGemma's trained context length. The batch sizes below must match it:
# an embedding request is a single sequence that has to fit in one physical
# batch, and llama-server's default ubatch is 512, so leaving it alone rejects
# any input over 512 tokens with "input (N tokens) is too large to process"
# — which during ingestion silently costs the whole vector index (see
# ingest.py's all-or-nothing rule).
CONTEXT_TOKENS = 2048

USER_AGENT = "grc-agent-setup"

_READY_TIMEOUT = 180.0
_HEALTH_TIMEOUT = 1.0


class FetchError(RuntimeError):
    """Provisioning or lifecycle failure that should surface as a message."""


# ---------------------------------------------------------------------------
# storage layout
# ---------------------------------------------------------------------------


def data_dir() -> Path:
    """Root for the runtime and model. Deliberately XDG rather than the
    package directory — this holds a few hundred MB of binaries and weights,
    which have no business inside an installed wheel."""
    override = os.environ.get("GRC_AGENT_RUNTIME_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "grc-agent"


def bin_dir() -> Path:
    return data_dir() / "bin"


def models_dir() -> Path:
    return data_dir() / "models"


def state_dir() -> Path:
    """Owner-only directory for the socket, pid, and API token."""
    d = data_dir() / "run"
    d.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(d, 0o700)
    return d


def server_binary() -> Path:
    override = os.environ.get("GRC_AGENT_LLAMA_SERVER")
    return Path(override) if override else bin_dir() / "llama-server"


def model_path() -> Path:
    return models_dir() / MODEL["file"]


def is_provisioned() -> bool:
    """True when both the runtime and the model are present on disk."""
    return server_binary().is_file() and model_path().is_file()


# ---------------------------------------------------------------------------
# platform gate
# ---------------------------------------------------------------------------


def platform_key(system: str | None = None, machine: str | None = None) -> tuple:
    system = system or platform.system()
    m = (machine or platform.machine()).lower()
    if m in ("amd64", "x86_64", "x64"):
        m = "x86_64"
    elif m in ("arm64", "aarch64"):
        m = "arm64" if system == "Darwin" else "aarch64"
    return (system, m)


def asset_name(build: str = LLAMA_BUILD, key: tuple | None = None) -> str | None:
    suffix = ASSET_SUFFIX.get(key or platform_key())
    return f"llama-{build}-bin-{suffix}.tar.gz" if suffix else None


def asset_url(build: str = LLAMA_BUILD, key: tuple | None = None) -> str | None:
    name = asset_name(build, key)
    return f"https://github.com/{LLAMA_REPO}/releases/download/{build}/{name}" if name else None


def glibc_version() -> tuple | None:
    """(major, minor), or None when it cannot be determined."""
    try:
        v = os.confstr("CS_GNU_LIBC_VERSION")  # e.g. "glibc 2.34"
        if v:
            m = re.search(r"(\d+)\.(\d+)", v)
            if m:
                return (int(m.group(1)), int(m.group(2)))
    except (ValueError, OSError, AttributeError):
        pass
    try:
        _, ver = platform.libc_ver()
        m = re.match(r"(\d+)\.(\d+)", ver or "")
        if m:
            return (int(m.group(1)), int(m.group(2)))
    except OSError:
        pass
    return None


def is_musl() -> bool:
    """True on Alpine and other musl systems, where these builds will not run."""
    try:
        if platform.libc_ver()[0] == "musl":
            return True
    except OSError:
        pass
    # libc_ver() commonly reports ("", "") on musl, so also look for the loader.
    return any(Path(p).exists() for p in ("/lib/ld-musl-x86_64.so.1", "/lib/ld-musl-aarch64.so.1"))


def existing_llama_server() -> Path | None:
    """An already-installed llama-server, which skips the runtime download."""
    w = shutil.which("llama-server")
    return Path(w) if w else None


def free_bytes(path: Path) -> int:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(str(p)).free


def fmt_size(n: float) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def runtime_plan(key: tuple | None = None) -> dict:
    """Decide which runtime, if any, this machine can actually run.

    Returns {"kind": "upstream"|"none", "url", "size", "reason", "warn"}.
    Deciding this before downloading is the whole point: the failure it avoids
    is a successful download of a binary that cannot start.

    Unlike the reference implementation there is no self-hosted compatibility
    build for glibc < 2.34 — such machines are told to build llama.cpp and
    point `GRC_AGENT_LLAMA_SERVER` at it, rather than being handed a binary
    this project would have to keep publishing.
    """
    key = key or platform_key()
    out: dict = {"kind": "none", "url": None, "size": None, "reason": "", "warn": ""}

    if key not in ASSET_SUFFIX:
        out["reason"] = f"no prebuilt llama.cpp archive for {key[0]}/{key[1]}"
        return out

    def upstream() -> dict:
        out.update(kind="upstream", url=asset_url(key=key), size=ASSET_BYTES.get(key))
        return out

    if key[0] != "Linux":
        return upstream()
    if is_musl():
        out["reason"] = "musl libc (Alpine): these binaries are glibc-only"
        return out

    v = glibc_version()
    if v is None:
        out["warn"] = "could not determine the glibc version; assuming it is recent enough"
        return upstream()
    if v >= MIN_GLIBC:
        return upstream()

    out["reason"] = (
        f"glibc {v[0]}.{v[1]} is older than the {MIN_GLIBC[0]}.{MIN_GLIBC[1]} "
        "the prebuilt binaries need"
    )
    return out


def manual_instructions() -> str:
    """What to do when no runtime can be fetched for this machine."""
    return (
        "Build llama.cpp yourself and point grc-agent at it:\n"
        f"  git clone https://github.com/{LLAMA_REPO}\n"
        "  cmake -B build -DGGML_NATIVE=ON && cmake --build build -j --target llama-server\n"
        "Then set GRC_AGENT_LLAMA_SERVER to the resulting llama-server binary."
    )


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------


def _open(url: str, timeout: float = 60.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310


def release_digests(build: str = LLAMA_BUILD, timeout: float = 30.0) -> dict:
    """Asset name -> sha256, from the GitHub release API.

    GitHub publishes a digest per asset, so the runtime is verified against
    upstream's own hash rather than one pinned here that would go stale on
    every build bump.
    """
    url = f"https://api.github.com/repos/{LLAMA_REPO}/releases/tags/{build}"
    try:
        with _open(url, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise FetchError(f"could not reach the GitHub release API: {e}") from e
    out = {}
    for a in data.get("assets", []):
        d = a.get("digest") or ""
        if d.startswith("sha256:"):
            out[a["name"]] = d.split(":", 1)[1]
    return out


def model_url() -> str:
    return f"https://huggingface.co/{MODEL['repo']}/resolve/main/{MODEL['file']}"


def download(
    url: str,
    dest: Path,
    sha256: str | None = None,
    expected_size: int | None = None,
    progress=None,
    should_cancel=None,
) -> Path:
    """Download to `dest`, verifying before it appears at that path.

    Writes to `<dest>.part` and renames only after the hash matches, so an
    interrupted or corrupted run can never leave something at `dest` that
    later fails with a confusing error from llama.cpp.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    got = 0
    try:
        with _open(url) as r:
            total = expected_size or int(r.headers.get("content-length") or 0)
            with open(part, "wb") as f:
                while True:
                    if should_cancel is not None and should_cancel():
                        raise FetchError("cancelled")
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    if progress:
                        progress(got, total)
    except (urllib.error.URLError, OSError) as e:
        part.unlink(missing_ok=True)
        raise FetchError(f"download failed: {e}") from e
    except BaseException:
        # Cancellation and KeyboardInterrupt included: never leave a partial
        # file behind that a later run would mistake for a complete one.
        part.unlink(missing_ok=True)
        raise

    if sha256 and h.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise FetchError(
            f"checksum mismatch for {dest.name}\n"
            f"  expected {sha256}\n  got      {h.hexdigest()}\n"
            "  the file was deleted; nothing was installed"
        )
    part.replace(dest)
    return dest


def _safe_members(tf: tarfile.TarFile, root: Path):
    """Reject anything that would write outside `root`.

    Symlinks are kept, not skipped: the shared libraries ship as a chain of
    them (libllama-common.so -> .so.0 -> .so.0.17.0) and the loader resolves
    the SONAME through that chain. Dropping them extracts files that look
    complete and then fail with "cannot open shared object file".
    """
    resolved = root.resolve()
    prefix = str(resolved) + os.sep
    for m in tf.getmembers():
        where = os.path.normpath(os.path.join(str(resolved), m.name))
        if not (where + os.sep).startswith(prefix):
            raise FetchError(f"refusing to extract {m.name!r}: escapes the target directory")
        if m.issym() or m.islnk():
            ln = m.linkname
            if os.path.isabs(ln) or ln.startswith(("/", "\\")):
                raise FetchError(f"refusing to extract {m.name!r}: absolute link target")
            base = os.path.dirname(where) if m.issym() else str(resolved)
            target = os.path.normpath(os.path.join(base, ln))
            if not (target + os.sep).startswith(prefix):
                raise FetchError(f"refusing to extract {m.name!r}: link escapes the target")
        yield m


def extract_runtime(archive: Path, dest: Path) -> Path:
    """Unpack the archive and return the directory holding llama-server.

    The upstream tarball puts everything in one versioned directory, and the
    binaries are dynamically linked against the .so files sitting beside them
    via RUNPATH=$ORIGIN. The whole directory has to stay together, so this
    returns that directory rather than copying binaries out of it.
    """
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        tf.extractall(dest, members=_safe_members(tf, dest), filter="data")
    for cand in sorted(dest.rglob("llama-server")):
        if cand.is_file():
            return cand.parent
    raise FetchError(f"llama-server not found in {archive.name}")


# ---------------------------------------------------------------------------
# provisioning
# ---------------------------------------------------------------------------


def download_plan() -> dict:
    """What provisioning would do, without doing any of it.

    Lets the GUI state the exact byte cost before asking for consent, and
    refuse up front when the machine cannot run the result at all.
    """
    plan = runtime_plan()
    have_server = server_binary().is_file()
    reusable = None if have_server else existing_llama_server()
    need_runtime = not have_server and reusable is None
    need_model = not model_path().is_file()

    total = 0
    if need_runtime:
        total += plan["size"] or RUNTIME_BYTES
    if need_model:
        total += MODEL["size"]

    return {
        "need_runtime": need_runtime,
        "need_model": need_model,
        "reusable_server": reusable,
        "runtime_available": plan["kind"] != "none",
        "reason": plan["reason"],
        "warn": plan["warn"],
        "download_bytes": total,
        "disk_bytes": total + (RUNTIME_BYTES if need_runtime else 0),
    }


def provision(progress=None, should_cancel=None) -> None:
    """Fetch whatever is missing so that `is_provisioned()` becomes true.

    `progress(stage, done, total)` is called with byte counts during each
    download; `should_cancel()` is polled and aborts cleanly. Blocking — call
    it from a worker thread, never on the GTK main loop.
    """
    plan = download_plan()

    if plan["need_runtime"] and not plan["runtime_available"]:
        raise FetchError(f"{plan['reason']}\n\n{manual_instructions()}")
    if plan["warn"]:
        _log.warning("embed runtime: %s", plan["warn"])

    needed = plan["disk_bytes"]
    free = free_bytes(data_dir())
    if needed and free < needed * 1.1:
        raise FetchError(
            f"not enough disk space: need about {fmt_size(needed * 1.1)}, "
            f"{fmt_size(free)} free at {data_dir()}"
        )

    bin_dir().mkdir(parents=True, exist_ok=True)
    models_dir().mkdir(parents=True, exist_ok=True)

    if plan["reusable_server"] is not None:
        # An already-installed llama-server skips a download entirely.
        link = bin_dir() / "llama-server"
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(plan["reusable_server"])
        _log.info("embed runtime: reusing %s", plan["reusable_server"])
    elif plan["need_runtime"]:
        _fetch_runtime(progress=progress, should_cancel=should_cancel)

    if plan["need_model"]:
        download(
            model_url(),
            model_path(),
            sha256=MODEL["sha256"],
            expected_size=MODEL["size"],
            progress=(lambda d, t: progress("model", d, t)) if progress else None,
            should_cancel=should_cancel,
        )


def _fetch_runtime(progress=None, should_cancel=None) -> None:
    plan = runtime_plan()
    name = asset_name()
    digest = release_digests().get(name or "")
    if not digest:
        raise FetchError(f"no published checksum for {name}")

    staging = data_dir() / "staging"
    archive = staging / name
    download(
        plan["url"],
        archive,
        sha256=digest,
        expected_size=plan["size"],
        progress=(lambda d, t: progress("runtime", d, t)) if progress else None,
        should_cancel=should_cancel,
    )
    try:
        src = extract_runtime(archive, staging / "unpacked")
        # Move the directory's contents wholesale: the binaries find their
        # sibling .so files through RUNPATH=$ORIGIN, so they must stay
        # together.
        for item in src.iterdir():
            target = bin_dir() / item.name
            if target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(item), str(target))
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------------------
# server lifecycle
# ---------------------------------------------------------------------------


def socket_path() -> Path:
    return state_dir() / "server.sock"


def _pid_path() -> Path:
    return state_dir() / "server.pid"


def _token_path() -> Path:
    return state_dir() / "server.token"


def _log_path() -> Path:
    return state_dir() / "server.log"


def _write_private(path: Path, text: str) -> None:
    """Write owner-only, creating with 0600 rather than chmod-ing afterwards.

    O_NOFOLLOW: GRC_AGENT_RUNTIME_DIR is user-controlled, and if it is ever
    pointed at a shared or attacker-writable location a pre-planted symlink
    here would silently redirect this write (with O_TRUNC) onto whatever it
    points at. This turns that into a hard failure instead.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    # The 0600 above applies only when open() creates the file, so a stale
    # group-readable token would otherwise stay that way across restarts.
    with contextlib.suppress(OSError):
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def read_token() -> str | None:
    try:
        return _token_path().read_text().strip() or None
    except OSError:
        return None


def resolve_threads() -> int:
    """Half the cores, capped at 4 — decode is memory-bandwidth-bound, so more
    threads stop helping well before they stop costing."""
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def _client(timeout: float) -> httpx.Client:
    """HTTP over the server's UNIX socket. The host name is a placeholder;
    the transport ignores it and connects to the socket path."""
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=str(socket_path())),
        base_url="http://llamacpp",
        timeout=timeout,
    )


def is_alive(timeout: float = _HEALTH_TIMEOUT) -> bool:
    if not socket_path().exists():
        return False
    try:
        with _client(timeout) as c:
            return c.get("/health").status_code == 200
    except Exception:
        return False


def fit_to_context(text: str, limit: int = CONTEXT_TOKENS - 8) -> str:
    """Truncate `text` to the model's context, measured with the server's own
    tokenizer rather than estimated.

    A word-count cap cannot bound tokens: across the shipped docs corpus the
    900-word cap still produced up to 2993 tokens, because tables, code, and
    URLs tokenize at roughly 3.3 tokens per word instead of the ~1.3 that
    prose does. Guessing a smaller word cap would truncate ordinary prose
    chunks to protect against the dense minority; asking the tokenizer is
    exact and only shortens what actually overflows.

    Costs one local round trip, and a second only when truncation is needed.
    Returns the text unchanged when it already fits.
    """
    token = read_token()
    with _client(30.0) as c:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        tokens = c.post("/tokenize", json={"content": text}, headers=headers).json()["tokens"]
        if len(tokens) <= limit:
            return text
        _log.warning(
            "fit_to_context: truncating a document from %d to %d tokens (%.0f%% discarded)",
            len(tokens),
            limit,
            100 * (1 - limit / len(tokens)),
        )
        r = c.post("/detokenize", json={"tokens": tokens[:limit]}, headers=headers)
        return r.json()["content"]


def _is_our_server(pid: int) -> bool:
    """Confirm a pid really is our llama-server before signalling it, so a
    recycled pid never gets an unrelated process killed."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return "llama-server" in raw.replace(b"\x00", b" ").decode(errors="replace")


def stop_server() -> bool:
    pid_file = _pid_path()
    stopped = False
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if _is_our_server(pid):
                os.kill(pid, signal.SIGTERM)
                stopped = True
        except (ProcessLookupError, ValueError, PermissionError, OSError):
            pass
        pid_file.unlink(missing_ok=True)
    socket_path().unlink(missing_ok=True)
    _token_path().unlink(missing_ok=True)
    return stopped


def ensure_server(wait: float = _READY_TIMEOUT) -> str:
    """Start llama-server if it is not already answering, and return the API
    token once it is ready. Blocking; safe to call from a worker thread."""
    if is_alive():
        token = read_token()
        if token:
            return token
        # Socket answers but our token is gone — not our server, or state was
        # cleared underneath us. Restart rather than talk to it unauthenticated.
        stop_server()

    if not is_provisioned():
        raise FetchError(
            "the local embedding runtime is not installed — "
            "install it from Settings to enable vector search"
        )

    sock = socket_path()
    sock.unlink(missing_ok=True)
    token = secrets.token_urlsafe(24)
    _write_private(_token_path(), token)

    cmd = [
        str(server_binary()),
        "-m",
        str(model_path()),
        "--host",
        str(sock),
        "-t",
        str(resolve_threads()),
        "-c",
        str(CONTEXT_TOKENS),
        # Logical and physical batch must both admit a full-context sequence.
        "-b",
        str(CONTEXT_TOKENS),
        "-ub",
        str(CONTEXT_TOKENS),
        "--no-webui",
        "--api-key",
        token,
        # EmbeddingGemma is a mean-pooling model. Never pass --reranking here:
        # it forces rank pooling and every embedding comes back all zeros.
        "--embeddings",
        "--pooling",
        "mean",
    ]

    log_file = _log_path()
    if not log_file.exists():
        os.close(os.open(str(log_file), os.O_WRONLY | os.O_CREAT, 0o600))
    with open(log_file, "ab") as lf:
        lf.write(f"\n=== start {time.strftime('%F %T')}: {' '.join(cmd)}\n".encode())
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            stdout=lf,
            stderr=lf,
            stdin=subprocess.DEVNULL,
            env=_runtime_env(),
            start_new_session=True,
        )
    _write_private(_pid_path(), str(proc.pid))

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise FetchError(f"llama-server exited with code {proc.returncode}; see {log_file}")
        if is_alive():
            return token
        time.sleep(0.3)
    raise FetchError(f"llama-server did not become ready in {wait:.0f}s; see {log_file}")


def _runtime_env() -> dict:
    """Prepend the bundled lib directory so the binaries find their own .so
    files even when the system ones are older."""
    env = dict(os.environ)
    lib = bin_dir()
    if lib.is_dir():
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = f"{lib}:{existing}" if existing else str(lib)
    return env
