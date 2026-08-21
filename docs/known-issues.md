# Known Issues

Defects found during review that are recorded rather than fixed, each with a
proposed fix. Unlike [`backlog.md`](backlog.md) — which tracks client feature
requests — everything here is a bug in code that already ships.

Every entry cites a verified observation, not a suspicion.

---

## 1. Serial embedding calls during ingest

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

---

## 2. Modal dialogs stall the event loop

**Where**: [`chat_sidebar.py`](../src/grc_agent/chat_sidebar.py) —
`_confirm_unreachable` uses `confirm.run()`.

`Gtk.Dialog.run()` iterates the GLib main context recursively. PyGObject's
`gi.events` documents this explicitly:

> Note that, unlike GLib, python does not support running the EventLoop
> recursively. [...] As such, do not use API such as `GLib.MainLoop.run` or
> `Gtk.Dialog.run`.

While such a dialog is open, asyncio callbacks do not dispatch — an in-flight agent turn stalls until the user dismisses it. This is **pre-existing and
unchanged** by the `gi.events` migration (`gbulb` has the same constraint), and
it is bounded: the dialogs are modal and short-lived, so the stall is only ever
as long as the user takes to click. `desktop_app.py`'s `_fatal_dialog` is not
affected — it runs before the loop starts.

The modal surface has been shrinking deliberately: the "Model not in backend's
list" Save-path popup (introduced with the `probe_backend` hung-chat guard) was
removed again in `0df48ec` and replaced with a status-bar warning, because the
mismatch is a diagnostic, not a decision. As of v0.3.0, `_confirm_unreachable`
(the "Save anyway?" confirm on a genuinely unreachable backend) is the only
remaining `.run()` dialog in the sidebar.

**Proposed fix**: replace `.run()` with the `response` signal plus an
`asyncio.Future`, so the dialog is awaited rather than pumped.

---

## 3. GTK3 nested submenus (File → New) can fail to open on Wayland

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

## 4. ChatGPT (Codex) thinking traces are one-liners by design

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
