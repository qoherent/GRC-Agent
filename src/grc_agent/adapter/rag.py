import contextlib
import hashlib
import logging
import os
import re
import sqlite3
import threading
from typing import Any

import httpx
import sqlite_vec
from openai import APIConnectionError, OpenAI

from grc_agent._paths import vectors_dir

_log = logging.getLogger(__name__)

# settings.py's load_settings()/get_env_value() each re-parse the whole .env
# file from disk on every call with no caching. rag.py's embedding path calls
# them repeatedly per query/chunk (get_db_and_model, _embed_endpoint,
# embed_query, embed_document all call in), which adds up to thousands of
# redundant .env parses over a single ingestion run. Cached here, gated on the
# file's mtime (a cheap stat(), not a full parse) so a live settings change —
# e.g. save_settings() called again within the same process, as the isolation
# tests do — still invalidates the cache instead of pinning it to whatever was
# first read.
_settings_cache: tuple[float, dict[str, Any]] | None = None
_env_value_cache: tuple[float, dict[str, str | None]] | None = None


def _env_mtime() -> float:
    from grc_agent.settings import env_path

    try:
        return env_path().stat().st_mtime
    except OSError:
        return 0.0


def _cached_load_settings() -> dict[str, Any]:
    global _settings_cache
    mtime = _env_mtime()
    if _settings_cache is None or _settings_cache[0] != mtime:
        from grc_agent.settings import load_settings

        _settings_cache = (mtime, load_settings())
    return _settings_cache[1]


def _cached_get_env_value(key: str) -> str | None:
    global _env_value_cache
    mtime = _env_mtime()
    if _env_value_cache is None or _env_value_cache[0] != mtime:
        _env_value_cache = (mtime, {})
    cache = _env_value_cache[1]
    if key not in cache:
        from grc_agent.settings import get_env_value

        cache[key] = get_env_value(key)
    return cache[key]


def get_db_and_model(domain: str) -> tuple[str, str | None]:
    cfg = _cached_load_settings()
    provider = cfg.get("provider", "ollama")

    if provider == "openrouter":
        model = _cached_get_env_value("OPENROUTER_EMBEDDING_MODEL") or os.getenv(
            "OPENROUTER_EMBEDDING_MODEL", "perplexity/pplx-embed-v1-0.6b"
        )
        db_name = f"{domain}_openrouter.db"
    else:
        # ollama and ollama_cloud both use local Ollama for embeddings
        # (Ollama Cloud's API doesn't expose /v1/embeddings)
        model = _cached_get_env_value("OLLAMA_EMBEDDING_MODEL") or os.getenv(
            "OLLAMA_EMBEDDING_MODEL", "embeddinggemma:latest"
        )
        db_name = f"{domain}_ollama.db"

    db_path = vectors_dir() / db_name
    return str(db_path), model


def _embed_endpoint() -> tuple[str, str | None]:
    """Shared base_url/api_key selection for both query- and document-side
    embedding calls."""
    cfg = _cached_load_settings()
    provider = cfg.get("provider", "ollama")

    if provider == "openrouter":
        key = _cached_get_env_value("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY", "")
        return "https://openrouter.ai/api/v1", key
    # ollama and ollama_cloud both use local Ollama for embeddings
    return "http://localhost:11434/v1", "not-needed"



# (base_url, api_key, client) as ONE tuple, replaced by a single atomic
# assignment below. embed_query/embed_document run on real OS threads (via
# asyncio.to_thread), so two threads racing here with DIFFERENT keys (e.g.
# a provider switch overlapping a catalog+docs cold query) must never observe
# a torn update — with the client and its key-tag as separate globals updated
# in two statements, a reader could see a new client paired with the old key
# (or vice versa), silently reusing the wrong endpoint/credentials for a
# request that "looks" cached. Bundling them means every read sees either the
# fully-old or the fully-new state, never a mix.
_embed_client_state: tuple[str, str, OpenAI] | None = None


def _get_embed_client() -> OpenAI:
    global _embed_client_state
    base_url, api_key = _embed_endpoint()
    state = _embed_client_state
    if state is not None and state[0] == base_url and state[1] == api_key:
        return state[2]
    # The SDK's own default timeout allows up to ~600s per attempt (a
    # backend that accepts the connection but then hangs, e.g. a local
    # Ollama server mid-model-load) — bounded here to the same order of
    # magnitude as the chat-model client's ~30s retry budget
    # (agent_factory.py's _retrying_http_client), so a hung embedding
    # backend fails fast instead of blocking a chat turn for up to ~30
    # minutes.
    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=30.0),
    )
    _embed_client_state = (base_url, api_key, client)
    return client


def _embed(model: str, input_text: str | list[str]) -> list[float] | list[list[float]]:
    client = _get_embed_client()
    try:
        response = client.embeddings.create(model=model, input=input_text, encoding_format="float")
    except APIConnectionError as exc:
        base_url, _ = _embed_endpoint()
        hint = (
            f"Is `ollama serve` running locally, with `ollama pull {model}` done?"
            if "localhost" in base_url
            else "Check OPENROUTER_API_KEY and network connectivity."
        )
        raise RuntimeError(f"Cannot reach the embeddings endpoint at {base_url}. {hint}") from exc
    if isinstance(input_text, list):
        return [d.embedding for d in response.data]
    return response.data[0].embedding


def embed_query(query: str) -> list[float]:
    cfg = _cached_load_settings()
    provider = cfg.get("provider", "ollama")
    use_prefix = provider != "openrouter"

    _, model = get_db_and_model("catalog")
    result = _embed(model, ("task: search result | query: " + query) if use_prefix else query)
    assert isinstance(result, list) and (not result or isinstance(result[0], float))
    return result  # type: ignore[return-value]


_DOCUMENT_PREFIX = "task: search result | document: "
EMBED_MAX_WORDS = 900


def _cap_words(text: str, max_words: int, *, label: str = "") -> str:
    """Cap document text at a maximum word count.
    Used strictly to satisfy hard input token constraints of embedding model APIs
    during database ingestion (ingest_catalog, ingest_docs) to prevent API failures.
    """
    words = text.split()
    if len(words) <= max_words:
        return text
    _log.warning(
        "_cap_words: truncating %s from %d to %d words (%.0f%% discarded)",
        label or "a document chunk",
        len(words),
        max_words,
        100 * (1 - max_words / len(words)),
    )
    return " ".join(words[:max_words])


def embed_document(text: str, model: str) -> list[float]:
    cfg = _cached_load_settings()
    provider = cfg.get("provider", "ollama")
    use_prefix = provider != "openrouter"

    body = text if not use_prefix else _DOCUMENT_PREFIX + text
    result = _embed(model, body)
    assert isinstance(result, list) and (not result or isinstance(result[0], float))
    return result  # type: ignore[return-value]


_EMBEDDING_DIM_CACHE: dict[str, int] = {}
_CORPUS_VERSION_CACHE: dict[str, str] = {}


# Read by chat_sidebar.py's _poll_indexing (polled every 500ms via
# GLib.timeout_add) to drive the ChatSidebar's status bar with a "Building
# knowledge database..." message instead of an indefinite hang during the
# first query_knowledge call (or after a provider switch that changes the
# embedding model). Set by _ensure_db_built.
_rag_building: dict[str, dict[str, Any]] = {}
"""Per-domain build status, keyed by domain ("catalog" | "docs"). Each value:
{"status": None|"building"|"ready"|"failed", "current": int, "total": int,
"indexed": int}. Keyed by domain (not a single flat dict) so that concurrent
catalog+docs cold builds — pydantic-ai runs function tools in parallel — don't
clobber each other's progress. The GUI poller reads the entry for whichever
domain is currently "building". Entries persist after completion so the final
"ready"/"failed" transition is observable; a domain key is (re)created on each
build. Mutated from the worker thread ingest runs on; read from the main loop
(CPython per-key dict ops are atomic under the GIL)."""


def _get_embedding_dim(model: str) -> int:
    """Cache the embedding dimension for a model so we don't pay for a real
    embedding API call on every single vector query just to verify the cached
    DB still matches the current model."""
    if model not in _EMBEDDING_DIM_CACHE:
        _EMBEDDING_DIM_CACHE[model] = len(embed_document("test", model))
    return _EMBEDDING_DIM_CACHE[model]


def _corpus_version(domain: str) -> str:
    """A cheap identity for the domain's underlying source data, independent
    of the embedding model — a hash of the live block-id set for the catalog,
    a content hash of the docs corpus for docs (its files change across
    grc-agent releases). Without this, a cached DB that still matches on
    embedding_model alone would silently keep serving stale results forever
    after a change to the source data, with no error or indication anything's
    wrong.

    For the catalog, hashing the actual block set (not GNU Radio's version
    string) is what makes newly installed OOT modules discoverable: installing
    an OOT block changes the block set but not gr.version(), so a fingerprint
    of platform.blocks is the only identity that catches it. Cached per-process:
    the block set doesn't change during a single run, and re-hashing ~100
    markdown files (docs) on every query would be wasteful."""
    if domain in _CORPUS_VERSION_CACHE:
        return _CORPUS_VERSION_CACHE[domain]

    if domain == "catalog":
        # Same non-underscore filter ingest_catalog indexes, so the fingerprint
        # matches exactly what's embedded. get_platform() is cached after first
        # load and is needed for querying anyway, so this adds no net cost.
        from grc_agent.adapter.graph import get_platform

        platform = get_platform()
        block_ids = sorted(b for b in platform.blocks if not b.startswith("_"))
        version = hashlib.sha256("\n".join(block_ids).encode()).hexdigest()[:16]
    else:
        from grc_agent._paths import docs_dir

        h = hashlib.sha256()
        for p in sorted(docs_dir().glob("*.md")):
            h.update(p.name.encode())
            h.update(p.read_bytes())
        version = h.hexdigest()[:16]

    _CORPUS_VERSION_CACHE[domain] = version
    return version


_BUILD_LOCKS: dict[str, threading.Lock] = {}

# Per-domain "last verified fresh" (db_path, model), so _build_db can skip its
# metadata re-check entirely on a warm cache instead of re-opening a
# connection and re-querying sqlite_master/_db_meta on every single
# query_catalog/query_docs call. See _build_db for cache population/use.
_FRESHNESS_CACHE: dict[str, tuple[str, str]] = {}


def _build_lock_for(domain: str) -> threading.Lock:
    """Per-domain build lock. RAG builds run on real OS threads (dispatched via
    asyncio.to_thread from query_catalog/query_docs), so two concurrent
    cold-cache builds of the SAME domain would otherwise race on the unlocked
    os.remove + rebuild and on the module-level embed/dimension caches.
    Different domains (catalog vs docs) target different files and use
    different locks, so they still build concurrently.

    dict.setdefault is a single atomic C-level dict operation in CPython (not
    interruptible by another thread mid-check) — required here, not just a
    style preference: a plain "get, then if-None set" (as this used to be)
    lets two threads racing to build the SAME domain for the first time each
    construct their own Lock() before either publishes it, so they'd return
    two DIFFERENT lock objects and take zero mutual exclusion from each
    other — exactly the race this lock exists to prevent. The throwaway
    Lock() built on every call when one already exists is cheap and
    discarded immediately by setdefault; that's a fine tradeoff for
    correctness that doesn't depend on GIL-scheduling luck.
    """
    return _BUILD_LOCKS.setdefault(domain, threading.Lock())


def _ensure_db_built(domain: str, db_path: str, model: str) -> None:
    with _build_lock_for(domain):
        # The implementation re-checks os.path.exists + validity under the
        # lock, so a concurrent builder that finished first makes a waiting
        # caller a no-op instead of rebuilding (and racing) a second time.
        _build_db(domain, db_path, model)


def _build_db(domain: str, db_path: str, model: str) -> None:  # noqa: C901
    global _rag_building

    # Once a (domain, db_path, model) combo has been verified fresh in this
    # process, later calls skip re-opening a connection and re-running the
    # metadata checks below entirely — query_catalog/query_docs call this on
    # every single query, and the checks are otherwise redundant work on a
    # warm cache. Invalidated implicitly: any rebuild below re-populates it
    # with the new state; a mismatch never populates it at all.
    if _FRESHNESS_CACHE.get(domain) == (db_path, model) and os.path.exists(db_path):
        return

    if os.path.exists(db_path):
        # Check the lexical (FTS5) fallback index, vector dimension, embedding
        # model name, and corpus version (all stored in _db_meta /
        # sqlite_master). A changed corpus/block-library or model would
        # otherwise go stale silently forever.
        try:
            conn = sqlite3.connect(db_path)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            fts_exists = (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name = ?", (f"{domain}_fts",)
                ).fetchone()
                is not None
            )
            sql_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = ?", (f"{domain}_idx",)
            ).fetchone()
            meta: dict[str, str] = {}
            try:
                for key, value in conn.execute("SELECT key, value FROM _db_meta"):
                    meta[key] = value
            except sqlite3.OperationalError:
                pass
            conn.close()

            reason = None
            if not fts_exists:
                # Pre-lexical-fallback DB, built before FTS5 support existed.
                reason = "missing lexical (FTS5) fallback index"
            elif sql_row and sql_row[0]:
                match = re.search(r"float\[(\d+)\]", sql_row[0])
                if match:
                    db_dim = int(match.group(1))
                    model_dim = _get_embedding_dim(model)
                    if model_dim != db_dim:
                        reason = f"dimension mismatch (DB has {db_dim}, model has {model_dim})"
                    elif not meta:
                        reason = "no metadata recorded"
                    elif meta.get("embedding_model") != model:
                        reason = (
                            f"embedding model changed (was '{meta.get('embedding_model')}', "
                            f"now '{model}')"
                        )
                    elif meta.get("corpus_version") != _corpus_version(domain):
                        reason = "source data changed since this DB was built"
                else:
                    reason = "corrupt vector index"
            else:
                # No vector index at all — a valid steady state, not
                # necessarily staleness: the embedding backend was
                # unreachable when this DB was last (re)built, so it's
                # lexical-only by design. Do NOT rebuild merely because the
                # vector index is absent — that would re-attempt (and
                # re-fail) embedding on every single query while the backend
                # stays down. Only a genuine corpus change should give
                # embedding a fresh chance.
                if not meta or meta.get("corpus_version") != _corpus_version(domain):
                    reason = "lexical-only DB is stale or missing metadata"

            if reason:
                print(f"[grc-agent] {domain} vector DB stale: {reason}. Rebuilding...")
                os.remove(db_path)
            else:
                _FRESHNESS_CACHE[domain] = (db_path, model)
                return
        except (sqlite3.DatabaseError, sqlite3.OperationalError):
            with contextlib.suppress(OSError):
                os.remove(db_path)

    _rag_building[domain] = {"status": "building", "current": 0, "total": 0, "indexed": 0}

    def _on_progress(current: int, total: int) -> None:
        # Called from the worker thread ingest runs on; mutates the per-domain
        # entry in place (CPython atomic per-key). The GUI polls from the main
        # loop instead of receiving cross-thread widget calls.
        entry = _rag_building.get(domain)
        if entry is not None:
            entry["current"] = current
            entry["total"] = total

    try:
        print(
            f"[grc-agent] {domain} vector DB not found or stale — building it now "
            f"(first run only, may take a few minutes)..."
        )
        from grc_agent import ingest

        if domain == "catalog":
            count = ingest.ingest_catalog(db_path, model, on_progress=_on_progress)
        else:
            count = ingest.ingest_docs(db_path, model, on_progress=_on_progress)
        print(f"[grc-agent] {domain} vector DB build complete: {db_path}")
        # `indexed` is the count actually indexed for lexical search (len(rows)),
        # which can be < `total` if some items failed to render — so the GUI's
        # "entries ready" message doesn't overclaim the processed count. It may
        # exceed the count that embedded successfully if the embedding backend
        # failed for some/all items (see ingest.py) — those still get a
        # lexical-only entry.
        entry = _rag_building.get(domain)
        if entry is not None:
            entry["status"] = "ready"
            entry["indexed"] = count
        _FRESHNESS_CACHE[domain] = (db_path, model)
    except Exception:
        entry = _rag_building.get(domain)
        if entry is not None:
            entry["status"] = "failed"
        raise


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone()
        is not None
    )


_FTS_TOKEN_RE = re.compile(r"\w+")

# Caps the MATCH expression's own size. Without this, an adversarially long or
# highly repetitive query (e.g. tens of thousands of words) builds an
# OR-joined MATCH expression whose evaluation cost scales with the expression
# itself, not the corpus — measured to stall a single query for 8-46 seconds
# on a ~100k-character input, synchronously blocking the calling thread.
# Realistic natural-language queries are far under this cap.
_FTS_MAX_TOKENS = 32


def _fts_query_string(q: str) -> str | None:
    """Build a permissive FTS5 MATCH expression from free-text input.

    Quotes each token so punctuation in the query (e.g. 'samp_rate?') can't
    produce an invalid MATCH expression, and ORs tokens together — this is a
    recall-oriented fallback for when vector search is unavailable, not a
    primary ranking mechanism, so broad matching beats precision here.
    Deduplicates (case-insensitive, order-preserving) and caps at
    _FTS_MAX_TOKENS before building the expression. Returns None if the query
    has no word tokens (nothing to search on).
    """
    tokens = _FTS_TOKEN_RE.findall(q)
    if not tokens:
        return None
    seen: set[str] = set()
    deduped = []
    for t in tokens:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(t)
    return " OR ".join(f'"{t}"' for t in deduped[:_FTS_MAX_TOKENS])


def _lexical_fallback_message(embed_error: str | None) -> str:
    """Explain why a result came back lexical instead of vector — covers both
    "the embedding call just failed" (embed_error set) and "this corpus has no
    vector index at all yet" (embed_error is None: the DB was built
    lexical-only during a past outage and hasn't been rebuilt since, even
    though the embedding backend may be reachable again right now). Per
    AGENTS.md's "no silent transformation" rule, a lexical result must always
    say so — including this second case, which previously fell through with
    no message at all whenever the current embed call happened to succeed."""
    if embed_error:
        return f"Vector search unavailable ({embed_error}); used lexical (keyword) fallback."
    return (
        "No vector index exists for this corpus yet (built lexical-only during "
        "a prior embedding-backend outage); used lexical (keyword) fallback. "
        "Vector search resumes automatically once a corpus or model change "
        "triggers a rebuild with the embedding backend reachable."
    )


def _query_index(
    domain: str,
    q: str,
    limit: int,
    *,
    idx_table: str,
    fts_table: str,
    chunks_table: str,
    id_column: str,
    extra_limit: int = 0,
) -> dict[str, Any]:
    """Shared vector-then-lexical retrieval behind query_catalog/query_docs:
    embed the query, ensure/open the domain DB, rank rowids via sqlite-vec
    (primary) or FTS5 BM25 (fallback — embedding call failed, or no vector
    index exists yet for this DB, e.g. built during a past embedding-backend
    outage), then batch-resolve `id_column` for the ranked rowids in one
    query instead of one SELECT per hit.

    idx_table/fts_table/chunks_table are internal per-domain constants
    (never user input), so interpolating them into the SQL text below is
    safe. Returns {"ok": False, "message": ...} on a DB build/missing
    failure (same shape both callers returned before this was factored out),
    else the ranked-rowid bundle for the caller to finish rendering into its
    own response shape (catalog's block list vs docs' joined answer
    string).
    """
    embed_error: str | None = None
    query_vec: list[float] | None = None
    try:
        query_vec = embed_query(q)
    except Exception as exc:
        embed_error = str(exc)

    db_path, model = get_db_and_model(domain)
    try:
        _ensure_db_built(domain, db_path, model)
    except Exception as exc:
        return {"ok": False, "message": f"{domain.capitalize()} DB build failed: {exc}"}
    if not os.path.exists(db_path):
        return {"ok": False, "message": f"{domain.capitalize()} DB not found at: {db_path}"}

    conn = sqlite3.connect(db_path)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.row_factory = sqlite3.Row

        fetch_limit = limit + extra_limit
        vec_available = query_vec is not None and _table_exists(conn, idx_table)

        if vec_available:
            vec_rows = conn.execute(
                f"SELECT rowid, distance FROM {idx_table} WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (sqlite_vec.serialize_float32(query_vec), fetch_limit),
            ).fetchall()
            ranked_rowids = [row["rowid"] for row in vec_rows]
            distance_by_rowid = {row["rowid"]: row["distance"] for row in vec_rows}
            output_truncated = len(vec_rows) > limit
            search_mode = "vector"
        else:
            fts_query = _fts_query_string(q)
            fts_rows = (
                conn.execute(
                    f"SELECT rowid FROM {fts_table} WHERE {fts_table} MATCH ? "
                    f"ORDER BY bm25({fts_table}) LIMIT ?",
                    (fts_query, fetch_limit),
                ).fetchall()
                if fts_query and _table_exists(conn, fts_table)
                else []
            )
            ranked_rowids = [row["rowid"] for row in fts_rows]
            distance_by_rowid = {}
            output_truncated = len(fts_rows) > limit
            search_mode = "lexical"

        id_by_rowid: dict[int, Any] = {}
        if ranked_rowids:
            placeholders = ",".join("?" for _ in ranked_rowids)
            for row in conn.execute(
                f"SELECT rowid, {id_column} FROM {chunks_table} WHERE rowid IN ({placeholders})",
                ranked_rowids,
            ):
                id_by_rowid[row["rowid"]] = row[id_column]

        return {
            "ok": True,
            "search_mode": search_mode,
            "ranked_rowids": ranked_rowids,
            "id_by_rowid": id_by_rowid,
            "distance_by_rowid": distance_by_rowid,
            "output_truncated": output_truncated,
            "embed_error": embed_error,
        }
    finally:
        conn.close()


def query_catalog(query: str, limit: int = 5) -> dict[str, Any]:
    q = " ".join(str(query).split())
    if not q:
        return {"ok": False, "results": [], "message": "query must be non-empty"}

    result = _query_index(
        "catalog",
        q,
        limit,
        idx_table="catalog_idx",
        fts_table="catalog_fts",
        chunks_table="catalog_chunks",
        id_column="block_id",
        extra_limit=1,
    )
    if not result["ok"]:
        return {"ok": False, "results": [], "message": result["message"]}

    results = []
    for rowid in result["ranked_rowids"]:
        block_id = result["id_by_rowid"].get(rowid)
        if not block_id:
            continue
        rendered = render_catalog_block(block_id, result["distance_by_rowid"].get(rowid, 0.0))
        if rendered:
            results.append(rendered)
        if len(results) >= limit:
            break

    response: dict[str, Any] = {
        "ok": True,
        "query": q,
        "results": results,
        "output_truncated": result["output_truncated"],
        "search_mode": result["search_mode"],
    }
    if result["search_mode"] == "lexical":
        response["message"] = _lexical_fallback_message(result["embed_error"])
    return response


def render_catalog_block(block_id: str, distance: float) -> dict[str, Any] | None:
    from grc_agent.adapter.graph import get_platform, keep_param, type_controlling_params

    platform = get_platform()
    fg = platform.make_flow_graph()
    try:
        b = fg.new_block(block_id)
    except KeyError:
        return None
    fg.rewrite()

    params = {}
    type_controlling = type_controlling_params(block_id)

    for k, p in b.params.items():
        if keep_param(k, p, b, mode="details"):
            dtype = getattr(p, "dtype", "") or "raw"
            default = getattr(p, "default", "") or ""

            cleaned_default = default
            if cleaned_default.startswith("${") and cleaned_default.endswith("}"):
                cleaned_default = cleaned_default[2:-1].strip()
            if not cleaned_default and k in type_controlling:
                cleaned_default = "auto"

            opts = getattr(p, "options", None)
            if dtype == "enum" and opts:
                opt_keys = [str(o) for o in opts]
                if set(opt_keys) == {"True", "False"}:
                    params[k] = f"[bool]={cleaned_default}"
                else:
                    params[k] = f"enum=[{','.join(opt_keys)}]={cleaned_default}"
            else:
                params[k] = f"[{dtype}]={cleaned_default}"

    inputs = []
    for p in b.active_sinks:
        inputs.append(
            {
                "port_id": str(p.key),
                "dtype": str(getattr(p, "dtype", "")),
                "domain": str(getattr(p, "domain", "") or "stream"),
            }
        )
    outputs = []
    for p in b.active_sources:
        outputs.append(
            {
                "port_id": str(p.key),
                "dtype": str(getattr(p, "dtype", "")),
                "domain": str(getattr(p, "domain", "") or "stream"),
            }
        )

    return {
        "block_id": block_id,
        "label": getattr(b, "label", block_id),
        "category": " > ".join(b.category) if isinstance(b.category, list) else str(b.category),
        "params": params,
        "inputs": inputs,
        "outputs": outputs,
        "distance": round(distance, 3),
    }


def query_docs(query: str, limit: int = 5) -> dict[str, Any]:
    q = " ".join(str(query).split())
    if not q:
        return {"ok": False, "answer": "", "message": "query must be non-empty"}

    result = _query_index(
        "docs",
        q,
        limit,
        idx_table="docs_idx",
        fts_table="docs_fts",
        chunks_table="docs_chunks",
        id_column="payload",
    )
    if not result["ok"]:
        return {"ok": False, "answer": "", "message": result["message"]}

    id_by_rowid = result["id_by_rowid"]
    chunks = [id_by_rowid[r] for r in result["ranked_rowids"] if r in id_by_rowid]
    answer = "\n\n---\n\n".join(chunks)

    response: dict[str, Any] = {
        "ok": True,
        "query": q,
        "answer": answer,
        "search_mode": result["search_mode"],
    }
    if result["search_mode"] == "lexical":
        response["message"] = _lexical_fallback_message(result["embed_error"])
    return response
