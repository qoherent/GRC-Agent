# Known Issues

Defects found during review that are recorded rather than fixed, each with a
proposed fix. Unlike [`backlog.md`](backlog.md) — which tracks client feature
requests — everything here is a bug in code that already ships.

Every entry cites a verified observation, not a suspicion.

---

## 1. The provider badge misreports healthy connections

**Where**: [`ui/providers.py`](../src/grc_agent/ui/providers.py) —
`resolve_provider_from_base_url()`; consumed by
[`chat_sidebar.py`](../src/grc_agent/chat_sidebar.py) `set_agent()`.

The badge does not read the configured provider. It reverse-engineers it from
the model's base URL:

```python
def resolve_provider_from_base_url(base_url: str) -> str:
    if "11434" in base_url or "ollama.com" in base_url:
        return "ollama"
    if base_url:
        return "openai_compatible"
    return ""
```

`set_agent()` then compares that guess against the configured provider and
sets `is_default=True` when they differ, which renders as **"Fallback default
(configured provider unreachable)"**. So any provider whose base URL is
neither an Ollama port nor OpenAI-compatible is reported as a failed fallback
while it is working perfectly. It also mislabels a local Ollama reachable on a
non-11434 port.

**Proposed fix**: resolve the badge from the provider the agent was actually
built with, rather than inferring it from a URL. `build_agent_from_cfg` already
knows the answer; pass it through instead of re-deriving it downstream. That
removes the guess entirely rather than adding another branch to it.

---

## 2. Dead `openrouter` / `ollama_cloud` code paths

**Where**: [`adapter/rag.py`](../src/grc_agent/adapter/rag.py),
[`agent_factory.py`](../src/grc_agent/agent_factory.py),
[`chat_sidebar.py`](../src/grc_agent/chat_sidebar.py) `_confirm_unreachable`,
plus [`.env.example`](../.env.example).

`bda0f2f` consolidated the backends to two, and `load_settings()` now
normalizes the old names on read:

```python
if raw_provider in ("openrouter", "openai_compatible"):
    provider = "openai_compatible"
elif raw_provider in ("ollama", "ollama_cloud"):
    provider = "ollama"
```

`load_settings()` can therefore never return `"openrouter"` or
`"ollama_cloud"` — but branches testing for exactly those strings survive
throughout the codebase and are unreachable. `.env.example` still documents
four providers that no longer exist.

This is not merely cosmetic. One such branch is a live bug — see issue 3.

**Proposed fix**: delete the branches and correct `.env.example`. Per AGENTS.md
("No Backward Compatibility") this is a deletion, not a migration.

---

## 3. Serial embedding, and a silently partial vector index

**Where**: [`ingest.py`](../src/grc_agent/ingest.py) — `ingest_catalog` and
`ingest_docs`.

Two separate defects in the same loop.

**Serial calls.** `_embed` already accepts a list, and `/v1/embeddings` accepts
an array, but both ingest paths call `embed_document()` once per item:

```python
embedding = embed_document(embed_text, model)
vec_rows.append((block_id, embedding))
```

That is one HTTP round trip per block and per doc chunk. Batching is the single
largest ingest-latency lever, and it matters most against a local server.

**Silently partial index.** A single mid-run failure disables embedding for the
remainder of the build, with no retry:

```python
except Exception as exc:
    _log.warning("catalog embed failed for block_id=%s: %s", block_id, exc)
    can_embed = False
```

The `vec0` table is still created afterwards from whatever `vec_rows`
accumulated. The result is a vector index with **fewer rows than the FTS
index** — some blocks are simply unreachable by vector search. No staleness
check detects this: `_db_meta` records only `embedding_model` and
`corpus_version`, both of which still match, and `rag.py` treats a
vector-index-present DB as healthy. Queries then return `search_mode:
"vector"` with silently incomplete recall, which violates AGENTS.md's "No
silent transformation".

**Proposed fix**: batch the embed calls; and on partial failure either abort to
a lexical-only build or record the expected row count in `_db_meta` so the
shortfall is detected and triggers a rebuild.

---

## 4. `embed_query` always resolves the catalog model

**Where**: [`adapter/rag.py`](../src/grc_agent/adapter/rag.py) — `embed_query`.

```python
_, model = get_db_and_model("catalog")
```

This runs even when the caller is querying the `docs` domain. It is harmless
today only because `get_db_and_model` derives the model from the provider
rather than the domain, so both domains resolve to the same string. It becomes
a live bug the moment per-domain embedding models are possible — the docs index
would be queried with the catalog's model, silently returning garbage rankings
rather than an error.

**Proposed fix**: pass the caller's domain through instead of hardcoding
`"catalog"`.

---

## 5. `ruff format --check` fails on `main`

**Where**: `adapter/rag.py`, `agent.py`, `agent_factory.py`,
`ui/settings_dialog.py`, `tests/test_isolation.py`, `tests/test_unit.py`.

Six tracked files are not formatted to the pinned ruff's satisfaction, so CI's
`Run Ruff (format check)` step fails on `main`. The ruff version is unchanged
(`0.16.3` in `uv.lock` both before and after the 26.04 relock), so this is
pre-existing drift, not a version bump — the files were committed without
`ruff format` having been run.

**Proposed fix**: run `uv run ruff format` once and commit the result. Kept out
of the 26.04 branch deliberately, so a mechanical reformat of six files does
not obscure a functional diff.

---

## 6. Modal dialogs stall the event loop

**Where**: [`chat_sidebar.py`](../src/grc_agent/chat_sidebar.py) —
`_confirm_unreachable` uses `confirm.run()`.

`Gtk.Dialog.run()` iterates the GLib main context recursively. PyGObject's
`gi.events` documents this explicitly:

> Note that, unlike GLib, python does not support running the EventLoop
> recursively. [...] As such, do not use API such as `GLib.MainLoop.run` or
> `Gtk.Dialog.run`.

While such a dialog is open, asyncio callbacks do not dispatch — an in-flight
agent turn stalls until the user dismisses it. This is **pre-existing and
unchanged** by the `gi.events` migration (`gbulb` has the same constraint), and
it is bounded: the dialogs are modal and short-lived, so the stall is only ever
as long as the user takes to click. `desktop_app.py`'s `_fatal_dialog` is not
affected — it runs before the loop starts.

**Proposed fix**: replace `.run()` with the `response` signal plus an
`asyncio.Future`, so the dialog is awaited rather than pumped.
