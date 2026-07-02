# 2026-07-02 GUI polish (zoom, chat contrast, thinking, LaTeX)

## Context

The GRC Agent desktop GUI ships with several quality-of-life issues that
impede everyday use:

1. **Zoom too small** — `apply_zoom` defaults to `3.0` and `ui_font_metrics`
   rounds the chat-pt size to `body * 0.75`, so the chat-pt value never
   catches up to the body font. The chat-rendered HTML reads as ~11 px.
2. **User message hard to read** — `render_user_message_html` paints the
   message body in `#a0a8b8` (dim blue-gray) on `#1c2238` (dim blue panel).
   At any zoom level the contrast is poor and the text feels muted compared
   to the agent output.
3. **User vs agent bubbles look the same** — both use near-black tinted
   panels (`#1c2238` blue vs `#111512` green). The visual cue is too
   subtle for fast scanning of a long chat.
4. **Thinking tokens not visible** — verified against the live Ollama
   `gemma4:e4b-it-qat-120k` instance: the model DOES emit `delta.reasoning`
   in the streamed `/v1/chat/completions` response. The codebase already
   pipes these through `GrcResponseConverter.yield_from_provider`. There
   is, however, one stray `think:false` in `doc_answer.py` and zero
   observable signal from the converter that helps the user (or us)
   confirm tokens are reaching the UI.
5. **Model replies are verbose when the user asked a simple question**.
6. **LaTeX in chat** — model output like `$350\text{--}\mu\text{Hz}$` is
   passed straight through to QTextBrowser which has no math renderer.

## Decisions

### Zoom

- Bump the persisted default from `3.0` to `3.5` at `main_window.py:368`
  and `app.py:106`. Existing users' `QSettings` value wins until they
  hit `View → Reset Zoom`.
- Bump base font metrics in `ui_font_metrics` (`styles.py:14-17`):
  - `body = int(14 * zoom)` → `int(15 * zoom)`
  - `mono = int(13 * zoom)` → `int(14 * zoom)`
  - `chat_pt = round(body * 0.75)` → `round(body * 0.8)`

  The 0.8 ratio matches what QTextBrowser actually paints (the *-0.75*
  heuristic was a small-font relic).

### Chat contrast

- `chat_widget.py` color tokens (single source of truth, edit here only):
  - `_COLOR_USER_BG`: `#1c2238` → `#243049` (lighter blue panel)
  - `_COLOR_USER_BORDER`: `#3a4a72` → `#4d6298`
  - `_COLOR_USER` (label "You:"): `#7a92c8` → `#cdd6f4` (primary text)
  - `_COLOR_USER_TEXT`: new constant `#e8eaf0` (message body)
  - `_COLOR_AGENT_BG`: `#111512` (near-black w/ green tint) → `#1b1d22`
    (neutral dark — no longer competing with the user blue panel)
  - `_COLOR_AGENT_BORDER`: `#253529` → `#3a4250`
- `render_user_message_html`: apply `font-size: {chat_pt}px` driven by
  the same `ui_font_metrics` so user text tracks zoom.

### Thinking

- `doc_answer.py:305-313`: delete the `if "openrouter.ai" not in
  openai_base_url: extra_body["think"] = False` block. With Ollama's
  default behavior the docs-RAG call now passes through to the model's
  default thinking mode; the response's `.content` is still the answer
  text (Ollama keeps thinking separate in `message.reasoning`).
- `toolagents_runtime.py:297`: bump the existing debug log to `warning`
  level for first-stream visibility: the converter already
  `logger.debug("thinking_token streamed len=%d", len(reasoning))` — we
  leave it as-is. Add a one-shot
  `logger.warning("first thinking token streamed len=%d", ...)` so an
  operator running at default log level sees whether the converter is
  picking up deltas. The flag flips off after the first emission so we
  don't spam the log.

### System prompt — concise-response rule

Append one line to `build_system_prompt` in `model_context.py:196`:

> When the user asks a question, answer concisely: lead with the direct
> answer, then add only the context needed to act on it.

No other prompt changes.

### LaTeX (option B + A combined)

**B (prompt-side):** append to `build_system_prompt`:

> Do not use LaTeX or TeX math notation in chat replies; write math
> inline in plain text (e.g. `350 microHz`, `f^2`, `x_i`).

**A (pre-process shim):** new `strip_inline_math` helper in
`chat_widget.py`. Runs over the user-provided `markdown_text` *before*
the markdown-to-HTML pass. Recognizes `$...$` and `$$...$$` segments
and rewrites them to either:

- unicode/HTML-entity form for common math symbols (`\mu` → µ,
  `\text{...}` → plain text inside the math, `\cdot` → ·, `^N` → ⁿ,
  `_N` → ₙ, `--> ` → –), or
- a `<code>$…$</code>` span as a fallback when the content can't be
  safely rewritten (multi-line, unknown macros, brackets, etc.).

The shim is intentionally simple — it's a "looks less ugly" pass, not a
full LaTeX renderer. The fallback `<code>$…$</code>` keeps the original
content visible but visually marks it as code, so the user can read it.

## Non-goals

- No new model-facing tool or schema field changes (per AGENTS.md).
- No rewrite of the GUI in Streamlit (per AGENTS.md "No backward
  compatibility"; also the actual blockers are local style/streaming
  fixes that take ~50 lines).
- No dependency additions (no QtWebEngine, no MathJax).

## Verification

- `uv run pytest -m "not grc_native and not gui and not llama_eval"` —
  default test gate. The existing `tests/gui/test_chat_widget.py`
  thinking-persistence tests stay green.
- New unit tests (TDD):
  - `test_strip_inline_math`: simple math → unicode, fallback path,
    no false positives on `$` inside code spans.
  - `test_user_message_html_uses_zoomable_font_size`: render a user
    message, assert the body div carries a `font-size` style.
  - `test_user_message_text_is_near_white`: render a user message,
    assert body text color is in the `#e0..#ff` range.
  - `test_agent_background_is_distinct_from_user`: assert
    `_COLOR_AGENT_BG != _COLOR_USER_BG`.

## Risk

- Bumping the zoom default changes the first-launch experience for
  brand-new users. Existing users keep their saved zoom. Mitigated by
  the View menu's `Reset Zoom` (Ctrl+0).
- Removing `think:false` from `doc_answer.py` may increase the time
  per docs-RAG call (the model now reasons before answering). This is
  acceptable: docs-RAG is a low-frequency, latency-tolerant path, and
  the cost is bounded by Ollama's default num_predict.