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

---

## 5. `no_gui` flowgraph executions may run in an external terminal with an empty console log

**Where**: upstream GRC (`gnuradio/grc/gui/Executor.py:57-74`) + the host's terminal
setup — nothing in this codebase.

For `generate_options: no_gui` graphs, GRC wraps the run command in the configured
`xterm_executable` (here `/etc/gnuradio/conf.d/grc.conf` → `x-terminal-emulator`). The
subprocess PIPE is then the terminal *wrapper's* stdout — the flowgraph's own output goes
to the terminal window instead, so `exec_monitor` captures an empty log. Worse, the
wrapper check tests the `shutil.which`-returned symlink string
(`/usr/bin/x-terminal-emulator`, which does not contain `gnome-terminal`), so GRC takes
the `-e` branch, and a GNOME terminal wrapper typically exits immediately after handing
off to the daemon — emitting `>>> Done (return code 0)` and re-enabling Execute *while
the flowgraph is still running*. Verified from source by a grounding subagent (wrapper
exit behavior reasoned from `update-alternatives`, not spawned). `qt_gui` graphs (the
default case) are unaffected: they run directly and stream fully into the console.

Consequence for the agent: after a `run_flowgraph` of a `no_gui` graph, `get_run_log`
may return an empty log with a success-shaped `Done` while the graph lives on in an
external window. The system prompt teaches the model to recognize this and ask the user.

**Proposed fix**: none locally (upstream GRC behavior). Optionally detect the wrapper
case in `exec_monitor` (start marker contains the xterm path) and annotate
`get_run_log` with an external-terminal note.

## 6. ExecFlowGraphThread spawn failures are success-shaped

**Where**: upstream GRC (`gnuradio/grc/gui/Executor.py:44-46`).

If spawning the run subprocess raises (e.g. an unparseable `run_command` option), the
constructor's except path emits `send_verbose_exec(str(e))` followed by
`send_end_exec()` — the code defaults to 0, so the console shows `>>> Done` and the
monitor records a "successful" empty run with the exception text buried in the log. The
agent's `run_flowgraph` result therefore reports `completed` with `ran_successfully:
true`; only reading `get_run_log` reveals the spawn error. The tool docstring and prompt
teach the model to check the log when a run "completes" suspiciously fast with no output.

**Proposed fix**: none locally (upstream GRC behavior); documented in the tool contract.

## 7. A queued run-failure notification can be silently dropped in a busy race

**Where**: [`chat_sidebar.py`](../src/grc_agent/chat_sidebar.py) —
`_send_fix_when_free` ignores `send_message`'s `False` return.

`notify_run_failure` queues a follow-up turn via `_send_fix_when_free`, which awaits the
in-flight turn and then calls `self.send_message(text)` — but `send_message` returns
`False` without side effect when `self._busy` is already True. If the user starts a new
turn in the gap between the awaited turn ending and the fix dispatch, the failure
notification vanishes with no log line and no status message. Found by a grounding
subagent while wiring `run_flowgraph`; pre-existing.

**Proposed fix**: surface the drop in the status bar (and log it) when `send_message`
returns `False` in `_send_fix_when_free`.
