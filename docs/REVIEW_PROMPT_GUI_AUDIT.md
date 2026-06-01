# Adversarial GUI Audit — External Reviewer Prompt

This document is a self-contained prompt for handing the GRC Agent
sidekick GUI to a fresh, unbiased reviewer. The goal is to break through
the author's tunnel vision and surface latent bugs, race conditions,
memory leaks, and architectural anti-patterns that the in-team audit
cycles may have missed.

The prompt is intentionally adversarial. It assumes the code is broken
and asks the reviewer to prove it. Do not use this prompt for any other
purpose; do not execute the audit yourself in this repository unless
the user has explicitly asked you to act as the reviewer.

---

## Context to Give the Reviewer

- **Repository root:** `/home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent`
- **Scope of audit:** `src/grc_agent_gui/` and `tests/gui/` only.
  Runtime / core agent code under `src/grc_agent/` is out of scope for
  this pass; treat it as a black box that returns dict payloads.
- **Documentation to read first:**
  - `docs/BLUEPRINT.md` (high-level contract; section on
    "PySide6 GUI Release (Release 2.0.0)" and "Second-pass hardening (M7)")
  - `docs/PYSIDE6_GUI_BLUEPRINT.md` (full Milestones 1-7, including
    the 19-item M7 audit the in-team just completed)
- **Status the reviewer is told:**
  - 59/59 GUI tests pass under `xvfb-run uv run pytest tests/gui/`.
  - Lint clean: `uv run ruff check src/grc_agent_gui/ tests/gui/`.
  - M7 audit (19 items) is claimed complete and is the very thing
    this external review is meant to validate or refute.

The reviewer should be told explicitly that the M7 audit just landed
and that the whole point of the external review is to find what M7
missed.

---

## The Prompt (copy-paste verbatim)

> You are a Senior Desktop Systems Architect and Qt/PySide6 Expert. Your
> task is to perform an adversarial, zero-bias audit of a newly
> completed PySide6 desktop application (The "GRC Agent Sidekick").
>
> We have been iterating on this codebase heavily, and we need you to
> break through our tunnel vision. Assume the code has hidden flaws.
> Do NOT praise the implementation. Focus exclusively on identifying
> latent bugs, race conditions, memory leaks, or architectural
> anti-patterns.
>
> ### Project Context
> - **Purpose:** A local PySide6 GUI that runs alongside GNU Radio
>   Companion. It hosts an LLM (Qwen 3.5 9B) in a background thread
>   to mutate `.grc` YAML files on disk.
> - **Execution:** It compiles flowgraphs via `grcc` and runs them via
>   `QProcess`, using a two-phase termination (SIGTERM -> 200ms wait ->
>   SIGKILL) to prevent SDR hardware locks.
> - **UI Architecture:** It uses a "Sidekick" pattern (Chat via
>   `QTextBrowser`, and Graph Inspection via `QTreeWidget` /
>   `QTableWidget`). It enforces a strict Signal-Only boundary between
>   the LLM `QThread` and the GUI thread.
> - **Status:** 59/59 tests pass. It implements throttled UI streaming,
>   deferred window close events, and deterministic temp-directory
>   cleanup.
>
> ### Audit Mandate: Seek and Destroy
> Inspect the codebase (`src/grc_agent_gui/` and `tests/gui/`)
> focusing strictly on these 4 vectors:
>
> 1. **The C++ / Python GC Chasm:**
>    Look for `QObject` lifecycle mismatches. Are there any dynamically
>    created widgets, timers, or threads that could be prematurely
>    garbage collected by Python before the C++ Qt event loop cleans
>    them up? Look for dangling references or `deleteLater()`
>    omissions.
>
> 2. **Concurrency & Event Loop Starvation:**
>    We throttled the LLM stream to 16 chars / 50ms using a `QTimer`.
>    Look for any remaining synchronous I/O, heavy JSON parsing
>    (`inspect_graph`), or large file I/O operations occurring on the
>    main GUI thread. Are there any scenarios where `QProcess`
>    stdout/stderr buffers could overflow and block the event loop?
>
> 3. **Subprocess & Zombie Escapes:**
>    Audit the `QProcess` split-stage execution (`grcc` compile, then
>    `sys.executable` run). Are there any edge cases (e.g., rapid
>    consecutive clicking, unhandled OS signals, crashes inside the
>    compiled Python script) where the hardware-linked child process
>    escapes the two-phase `stop()` termination?
>
> 4. **State Desynchronization:**
>    The UI reads state from the LLM's `change_graph` commits. Are
>    there any race conditions where the user clicks "Run" exactly
>    while the LLM is committing a file write, resulting in a dirty or
>    corrupted read by `grcc`?
>
> ### Output Requirements
> Generate a ruthless, prioritized markdown list of your findings.
> Categorize them as CRITICAL (crashes/hardware locks), MODERATE
> (UI freezes/leaks), or MINOR (UX/style). If the architecture is
> genuinely flawless, state so objectively, but you must prove you
> looked for the above traps. Do not write remediation code; just
> diagnose the flaws.

---

## How to Run This Review

1. Open a fresh chat session (or new agent context) with no prior
   memory of the M7 audit.
2. Paste the prompt above verbatim.
3. Provide the reviewer the absolute paths to the four files listed
   in "Documentation to read first" plus the two source roots
   (`src/grc_agent_gui/` and `tests/gui/`).
4. Do **not** mention which specific M7 items the in-team addressed.
   The reviewer is meant to find what M7 missed, not confirm M7.
5. When the reviewer returns findings, triage them as a new M8
   backlog. Do not merge findings into M7 retroactively; M7 is the
   closed baseline.

## Reviewer Constraints the In-Team Cares About

- The reviewer should treat the 59/59 test result as a starting
  baseline, not as proof of correctness. Coverage gaps and
  tautological tests are themselves valid findings.
- The reviewer should look for **test gaps**, not just code bugs.
  A behavior without a regression test is a finding.
- The reviewer should specifically look at how the new throttled
  stream (`AgentWorker._start_throttled_stream`,
  `_emit_next_chunk`, `_flush_turn_finished`, `cancel()`) interacts
  with the existing `closeEvent` and `cleanup_thread` paths. M7 added
  this code; M7 also added the tests for it. The external review
  must verify the tests actually exercise the cancel + close race.
