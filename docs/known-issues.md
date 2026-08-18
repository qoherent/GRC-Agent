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
`"ollama_cloud"` — branches testing for exactly those strings were unreachable.
(`save_settings()` rejects them outright.)

**RESOLVED.** All settings-provider-driven dead branches were deleted (the
scenario harness in `agent.py` and the normalization in `settings.py` keep
their explicit backend strings — those describe *test* backends and the
old-`.env` migration path, which are live). Deleting them surfaced one real
defect the dead code had been hiding: `_ollama_context_length`'s cloud
endpoint was keyed on the dead `provider == "ollama_cloud"` string, so since
the v0.1.5 consolidation a cloud user's context-length lookup silently went
to `localhost:11434`. It now derives the endpoint from the resolved
`ollama_base_url` (the same source of truth `_build_model` uses) and attaches
the API key when that URL is ollama.com. (The lookup still lives in
`chat_sidebar.resolve_model_context_length` for the context label; only the
compaction path stopped using it when it moved to `TieredCompaction`'s
genai-prices registry — see `AGENTS.md`.)

Two earlier instances of the same trap, both fixed on branch `26`: the
EmbeddingGemma task prefix keyed on `provider != "openrouter"` (permanently
true — prefix applied to every backend), and `resolve_model_context_length`
keyed on `"openrouter"` alone (every OpenAI-compatible endpoint fell through
to `None`). The env *keys* (`OPENROUTER_API_KEY`, `OLLAMA_CLOUD_MODEL`, …)
remain readable as fallbacks so existing `.env` files keep working — that is
a separate decision from the dead provider branches.

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

---

## 5. GTK3 nested submenus (File → New) can fail to open on Wayland

**Where**: upstream GTK3 + GRC's own `Gtk.MenuBar.new_from_model`
(`gnuradio/grc/gui/Bars.py`) — nothing in this codebase.

Reported on a progressively-upgraded Ubuntu 26.04 (Wayland session): opening
the File dropdown works, but the nested **New** submenu never appears, so the
flowgraph-type options look "missing". Not reproducible under X11, including
with the app's own `gi.events` backend (verified live by driving the real app
with `Gdk.test_simulate_button`/`simulate_motion` — the submenu model holds all
4 options and pops correctly on X11).

This is a documented GTK3-on-Wayland failure mode, not an app bug:
compositors that don't deliver enter/leave events for XDG popups leave GTK3
menu items unhighlighted and nested popups unmapped (see the Arch Linux
thread "GTK3 menus not working properly in native Wayland"; GTK issue #3662;
Xpra #4188, where GTK logs `Tried to map a popup with a non-top most
parent`). Stock `gnuradio-companion` shows the same behaviour on an affected
machine, since the menu is built entirely by GNU Radio's own GUI code.

**Workaround**: run under XWayland —

```bash
GDK_BACKEND=x11 uv run grc-agent
```

**Proposed fix**: none in this repo (upstream GTK/compositor issue). If it
recurs frequently we could detect a Wayland display at startup
(`Gdk.Display.get_default()` type) and surface the workaround in the status
bar.

---

## 6. ChatGPT (Codex) thinking traces are one-liners by design

**Where**: [`providers/openai_codex/model.py`](../src/grc_agent/providers/openai_codex/model.py)
— `openai_reasoning_summary: "auto"`.

OpenAI does not expose raw chain-of-thought for the GPT-5.x family at any
setting: *"While we don't expose the raw reasoning tokens emitted by the
model, you can view a summary of the model's reasoning using the `summary`
parameter"* (OpenAI reasoning docs). `auto` already selects the most detailed
summarizer available (it equals `detailed` for most reasoning models), and
GPT-5.2+ moved to deliberately *concise* summaries — so a short one-line
ThinkingPart is the entirety of what the API returned, not a rendering bug.
Verified the full pipeline: pydantic-ai maps every `reasoning_summary_text`
delta into a ThinkingPart and the sidebar accumulates all of them.

**Proposed fix**: optionally annotate the thinking expander with "(summary —
OpenAI does not expose raw reasoning)" for the Codex provider, so the brevity
isn't mistaken for a defect.
