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

**Status**: a `chatgpt.com` branch was added so the ChatGPT provider reads
correctly, but that is one more special case layered on the guess, not a fix.
The next provider hits the same wall, and a local Ollama served on a
non-11434 port is still mislabelled today.

---

## 2. Dead `openrouter` / `ollama_cloud` code paths

**Where**: [`adapter/rag.py`](../src/grc_agent/adapter/rag.py),
[`agent_factory.py`](../src/grc_agent/agent_factory.py),
[`chat_sidebar.py`](../src/grc_agent/chat_sidebar.py) `_confirm_unreachable`.

`bda0f2f` consolidated the backends to two, and `load_settings()` now
normalizes the old names on read:

```python
if raw_provider in ("openrouter", "openai_compatible"):
    provider = "openai_compatible"
elif raw_provider in ("ollama", "ollama_cloud"):
    provider = "ollama"
```

`load_settings()` can therefore never return `"openrouter"` or
`"ollama_cloud"` — but roughly 19 branches testing for exactly those strings
survive throughout the codebase and are unreachable. (`save_settings()` now
rejects them outright.)

This is not merely cosmetic: the same pattern already produced one live bug.
`rag.py` gated the EmbeddingGemma task prefix on `provider != "openrouter"`, a
condition that became permanently true, so the prefix was applied to every
backend including non-Gemma models. That instance is fixed; the remaining
branches are the same trap left armed.

A second instance was found and fixed since: `resolve_model_context_length`
keyed the context-window lookup on `"openrouter"` alone, so every
OpenAI-compatible endpoint fell through to `None` and the sidebar showed a
bare token count with no total to compare against.

`.env.example` has been corrected and no longer documents the dead values.
The env *keys* (`OPENROUTER_API_KEY`, `OLLAMA_CLOUD_MODEL`, …) are still read
as fallbacks so existing files keep working — those are a separate decision
from the dead provider branches.

**Proposed fix**: delete the branches. Per AGENTS.md ("No Backward
Compatibility") this is a deletion, not a migration.

---

## 3. Serial embedding calls during ingest

**Where**: [`ingest.py`](../src/grc_agent/ingest.py) — `ingest_catalog` and
`ingest_docs`.

`_embed` already accepts a list, and `/v1/embeddings` accepts
an array, but both ingest paths call `embed_document()` once per item:

```python
embedding = embed_document(embed_text, model)
vec_rows.append((block_id, embedding))
```

That is one HTTP round trip per block and per doc chunk. Batching is the single
largest ingest-latency lever, and it matters most against a local server.

**Proposed fix**: batch the embed calls.

**Silently partial index — FIXED.** Left here because it was found in the
wild and is worth not reintroducing. A single mid-run embed failure used to
disable embedding for the rest of the build while *keeping* what had already
been collected, so the `vec0` table was created over a fraction of the corpus.
No staleness check could detect it: `_db_meta` records only `embedding_model`
and `corpus_version`, both of which still matched, and `rag.py` treats a
vector-index-present DB as healthy — so queries returned `search_mode:
"vector"` with silently incomplete recall.

Observed live while enabling the llama.cpp backend: **4 vector rows against 718
docs chunks** (and 288 against 584 catalog blocks), which ranked an AGC page
top for "what is a stream tag" — worse than the lexical fallback it had
replaced. `ingest.py` now discards partial embeddings and builds lexical-only,
so the vector index either covers the whole corpus or does not exist.

---

## 4. Modal dialogs stall the event loop

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
