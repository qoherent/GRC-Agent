import contextlib
import hashlib
import os
import re
import sqlite3
import threading
from typing import Any

import sqlite_vec
from openai import APIConnectionError, OpenAI

from grc_agent._paths import vectors_dir


def get_db_and_model(domain: str) -> tuple[str, str | None]:
    from grc_agent.settings import get_env_value, load_settings

    cfg = load_settings()
    provider = cfg.get("provider", "ollama")

    if provider == "openrouter":
        model = get_env_value("OPENROUTER_EMBEDDING_MODEL") or os.getenv(
            "OPENROUTER_EMBEDDING_MODEL", "perplexity/pplx-embed-v1-0.6b"
        )
        db_name = f"{domain}_openrouter.db"
    else:
        # ollama and ollama_cloud both use local Ollama for embeddings
        # (Ollama Cloud's API doesn't expose /v1/embeddings)
        model = get_env_value("OLLAMA_EMBEDDING_MODEL") or os.getenv(
            "OLLAMA_EMBEDDING_MODEL", "embeddinggemma:latest"
        )
        db_name = f"{domain}_ollama.db"

    db_path = vectors_dir() / db_name
    return str(db_path), model


def _embed_endpoint() -> tuple[str, str | None]:
    """Shared base_url/api_key selection for both query- and document-side
    embedding calls."""
    from grc_agent.settings import get_env_value, load_settings

    cfg = load_settings()
    provider = cfg.get("provider", "ollama")

    if provider == "openrouter":
        key = get_env_value("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY", "")
        return "https://openrouter.ai/api/v1", key
    # ollama and ollama_cloud both use local Ollama for embeddings
    return "http://localhost:11434/v1", "not-needed"


_embed_client: OpenAI | None = None
_embed_client_key: tuple[str, str] | None = None


def _get_embed_client() -> OpenAI:
    global _embed_client, _embed_client_key
    base_url, api_key = _embed_endpoint()
    key = (base_url, api_key)
    if _embed_client is None or _embed_client_key != key:
        _embed_client = OpenAI(base_url=base_url, api_key=api_key)
        _embed_client_key = key
    return _embed_client


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
    from grc_agent.settings import load_settings

    cfg = load_settings()
    provider = cfg.get("provider", "ollama")
    use_prefix = provider != "openrouter"

    _, model = get_db_and_model("catalog")
    result = _embed(model, ("task: search result | query: " + query) if use_prefix else query)
    assert isinstance(result, list) and (not result or isinstance(result[0], float))
    return result  # type: ignore[return-value]


_DOCUMENT_PREFIX = "task: search result | document: "
EMBED_MAX_WORDS = 900


def _cap_words(text: str, max_words: int) -> str:
    """Cap document text at a maximum word count.
    Used strictly to satisfy hard input token constraints of embedding model APIs
    during database ingestion (ingest_catalog, ingest_docs) to prevent API failures.
    """
    words = text.split()
    return text if len(words) <= max_words else " ".join(words[:max_words])


def embed_document(text: str, model: str) -> list[float]:
    from grc_agent.settings import load_settings

    cfg = load_settings()
    provider = cfg.get("provider", "ollama")
    use_prefix = provider != "openrouter"

    body = text if not use_prefix else _DOCUMENT_PREFIX + text
    result = _embed(model, body)
    assert isinstance(result, list) and (not result or isinstance(result[0], float))
    return result  # type: ignore[return-value]


_EMBEDDING_DIM_CACHE: dict[str, int] = {}
_CORPUS_VERSION_CACHE: dict[str, str] = {}


# Exposed to the dashboard via /grc/status so the UI can show a "Building
# knowledge database..." banner instead of an indefinite hang during the
# first query_knowledge call (or after a provider switch that changes the
# embedding model). Set by _ensure_db_built, no longer read by any endpoint.
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


def _build_lock_for(domain: str) -> threading.Lock:
    """Per-domain build lock. RAG builds run on real OS threads (dispatched via
    asyncio.to_thread from query_catalog/query_docs), so two concurrent
    cold-cache builds of the SAME domain would otherwise race on the unlocked
    os.remove + rebuild and on the module-level embed/dimension caches.
    Different domains (catalog vs docs) target different files and use
    different locks, so they still build concurrently."""
    lock = _BUILD_LOCKS.get(domain)
    if lock is None:
        lock = threading.Lock()
        _BUILD_LOCKS[domain] = lock
    return lock


def _ensure_db_built(domain: str, db_path: str, model: str) -> None:
    with _build_lock_for(domain):
        # The implementation re-checks os.path.exists + validity under the
        # lock, so a concurrent builder that finished first makes a waiting
        # caller a no-op instead of rebuilding (and racing) a second time.
        _build_db(domain, db_path, model)


def _build_db(domain: str, db_path: str, model: str) -> None:  # noqa: C901
    global _rag_building
    if os.path.exists(db_path):
        # Check vector dimension, embedding model name, and corpus version
        # (all stored in _db_meta). Any mismatch triggers a rebuild —
        # different models produce different embedding spaces, and a changed
        # corpus/block-library would otherwise go stale silently forever.
        try:
            conn = sqlite3.connect(db_path)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            sql_row = conn.execute(
                f"SELECT sql FROM sqlite_master WHERE name = '{domain}_idx'"
            ).fetchone()
            meta: dict[str, str] = {}
            try:
                for key, value in conn.execute("SELECT key, value FROM _db_meta"):
                    meta[key] = value
            except sqlite3.OperationalError:
                pass
            conn.close()

            if sql_row and sql_row[0]:
                match = re.search(r"float\[(\d+)\]", sql_row[0])
                if match:
                    db_dim = int(match.group(1))
                    model_dim = _get_embedding_dim(model)
                    reason = None
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

                    if reason:
                        print(f"[grc-agent] {domain} vector DB stale: {reason}. Rebuilding...")
                        os.remove(db_path)
                    else:
                        return
                else:
                    os.remove(db_path)
            else:
                os.remove(db_path)
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
        # `indexed` is the count actually embedded (len(rows)), which can be <
        # `total` if some items failed to render/embed — so the GUI's "entries
        # ready" message doesn't overclaim the processed count.
        entry = _rag_building.get(domain)
        if entry is not None:
            entry["status"] = "ready"
            entry["indexed"] = count
    except Exception:
        entry = _rag_building.get(domain)
        if entry is not None:
            entry["status"] = "failed"
        raise


def query_catalog(query: str, limit: int = 5) -> dict[str, Any]:
    q = " ".join(str(query).split())
    if not q:
        return {"ok": False, "results": [], "message": "query must be non-empty"}

    try:
        query_vec = embed_query(q)
    except Exception as exc:
        return {"ok": False, "results": [], "message": f"Embedding failed: {exc}"}

    db_path, model = get_db_and_model("catalog")
    try:
        _ensure_db_built("catalog", db_path, model)
    except Exception as exc:
        return {"ok": False, "results": [], "message": f"Catalog DB build failed: {exc}"}
    if not os.path.exists(db_path):
        return {"ok": False, "results": [], "message": f"Catalog DB not found at: {db_path}"}

    conn = sqlite3.connect(db_path)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.row_factory = sqlite3.Row

        vec_rows = conn.execute(
            "SELECT rowid, distance FROM catalog_idx WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(query_vec), limit + 1),
        ).fetchall()

        results = []
        for row in vec_rows:
            rowid = row["rowid"]
            distance = row["distance"]
            chunk = conn.execute(
                "SELECT block_id FROM catalog_chunks WHERE rowid = ?",
                (rowid,),
            ).fetchone()
            if not chunk:
                continue

            block_id = chunk["block_id"]
            rendered = render_catalog_block(block_id, distance)
            if rendered:
                results.append(rendered)

            if len(results) >= limit:
                break

        return {
            "ok": True,
            "query": q,
            "results": results,
            "output_truncated": len(vec_rows) > limit,
        }
    finally:
        conn.close()


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

    try:
        query_vec = embed_query(q)
    except Exception as exc:
        return {"ok": False, "answer": "", "message": f"Embedding failed: {exc}"}

    db_path, model = get_db_and_model("docs")
    try:
        _ensure_db_built("docs", db_path, model)
    except Exception as exc:
        return {"ok": False, "answer": "", "message": f"Docs DB build failed: {exc}"}
    if not os.path.exists(db_path):
        return {"ok": False, "answer": "", "message": f"Docs DB not found at: {db_path}"}

    conn = sqlite3.connect(db_path)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.row_factory = sqlite3.Row

        vec_rows = conn.execute(
            "SELECT rowid, distance FROM docs_idx WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(query_vec), limit),
        ).fetchall()

        chunks = []
        for row in vec_rows:
            rowid = row["rowid"]
            chunk = conn.execute(
                "SELECT payload FROM docs_chunks WHERE rowid = ?",
                (rowid,),
            ).fetchone()
            if chunk:
                chunks.append(chunk["payload"])

        answer = "\n\n---\n\n".join(chunks)
        return {"ok": True, "query": q, "answer": answer}
    finally:
        conn.close()
