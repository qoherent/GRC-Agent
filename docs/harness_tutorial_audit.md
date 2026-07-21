# GRC Agent Harness Audit vs. GNU Radio Wiki Tutorials

**Capability audit, not a code-quality audit.** For every one of the 97
scraped GNU Radio Companion (GRC) wiki pages under `docs/wiki_gnuradio_org/`,
this asks one question: *can the current GRC Agent harness actually carry
out this tutorial's steps, end to end?* Six subagents each audited a cluster
of tutorials against the harness's real tool surface (not a summary of it —
the actual pydantic model shapes in `agent.py`, the actual system prompt in
`prompts.py`, the actual ingest glob in `ingest.py`). This document
consolidates their findings, deduplicated, plus two findings verified
directly during synthesis. The per-tutorial tables below are the original,
as-audited record and are left unchanged; resolution status for each
actionable finding is tracked in the section immediately below instead.

## Resolution status

| # | Finding | Resolution |
|---|---|---|
| 1 | Synthetic test-fixture content shipped unfiltered in the `docs` corpus | **Left as-is** (explicit decision) — no `source_type` tagging or relocation. Revisit if this becomes a real citation-trust problem in practice. |
| 2 | No-execution/no-hardware boundary undocumented in the system prompt | **Fixed** — `prompts.py` now states it explicitly. |
| 3 | OOT/build-tool boundary undocumented in the system prompt | **Fixed** — `prompts.py` now states it explicitly, with the Embedded Python Block alternative named. |
| 4 | Deprecation/staleness has no resolution rule | **Fixed** (rule) + **Fixed** (physical removal for the two confirmed-self-declared-deprecated pages) — `prompts.py` now tells the model to prefer a named replacement over a deprecated snippet's own instructions; `Async_CRC32.md` and `index.php_OutOfTreeModules.md` (both self-declare deprecated with a live replacement already in the corpus — `CRC_Append.md`/`CRC_Check.md` and `Creating_C++_OOT_with_gr-modtool.md`/`Creating_Python_OOT_with_gr-modtool.md` respectively) were deleted from `docs/wiki_gnuradio_org/`. `BlocksCodingGuide.md` (SWIG-era, no self-declared-deprecated banner), `UsingEclipse.md`, and `E310_FM_Receiver.md` were deliberately left — they don't self-declare deprecated the way the two removed pages did, and still carry legitimate historical/reference value. |
| 5 | `tutorial_manifest.txt` was dead code | **Fixed** — deleted. |
| 6 | No tool to load a fetched/pasted `.grc` into the canvas | **Rejected, by design** — confirmed intentional: the harness is deliberately scoped to one already-open flowgraph; not adding this. |
| 7 | No filesystem browse/list tool | **Rejected, by design** — no new tool. |
| 8 | `inspect_graph` doesn't syntax-check Embedded Python Block source | **Deferred** — see explanation below; no action taken pending your call. |
| 9 | No bulk pattern-match update | **No action needed** (per the original audit itself — the two-step enumerate-then-batch workflow already covers this). |
| 10 | Missing prerequisite content (`QPSK_Mod_and_Demod.md`) | **Not actioned** — would require re-scraping from the live wiki, out of scope for this pass. |
| 11 | No canvas-position field on `add_blocks` | **Not actioned** — cosmetic/low-priority, no decision requested. |
| 12 | No read-only "preview generated Python" tool | **Fixed** — added the `generate_python` tool (`adapter.preview_flowgraph_py()`), backed by GNU Radio's own `Generator._build_python_code_from_template()` (in-memory only, `write()` never called — verified with a dedicated test that no file appears on disk after a call, and re-verified through a genuine live-LLM run in `tests/test_integration.py`). Deliberately does not apply `generate_flowgraph_py`'s `run_options` override, so it shows the flowgraph's actual configured output. Follow-up hardening after a self-review: added a `k`-capped (default 5, clamped 1-20, matching `query_knowledge`'s convention) limit on returned Embedded Python Block/Module files — the main script is always kept, drops are counted in `omitted_files`, never silent; added 3 more unit tests building a real flowgraph with multiple `epy_block` instances via `change_graph` to cover the previously-untested multi-file path and the cap/truncation logic; wrapped the private-API call in a try/except so an unsupported future GNU Radio version fails with a clear `ModelRetry` instead of a raw `AttributeError`; clarified in the docstring that each entry's `path` is informational only, after observing a live model (GPT-4.1) format it as a fake downloadable link. Covered by 8 unit tests in `tests/test_unit.py`, a tool-wiring check in `tests/test_isolation.py`, and a dedicated live-LLM integration test (`test_scenario_generate_python_writes_nothing_to_disk`, verified against OpenRouter with the project's real configured model, `deepseek/deepseek-v4-flash`); documented in `AGENTS.md`'s Tool Surface table and Key Conventions. |
| 13 | Corpus's own outdated code samples (Python 2, `distutils`, SWIG) | **Covered by the finding #4 prompt rule** where a page self-declares deprecated; `BlocksCodingGuide.md`/`UsingEclipse.md`/`E310_FM_Receiver.md` left in place per the finding #4 decision above. |
| 14 | Ad-hoc filename-metadata reinvention vs. SigMF/File Meta Sink | **No code fix applicable** — this is the tutorial content's own pattern, not harness code; already noted as guidance for future agent behavior. |
| 15 | `index.php_Guided_Tutorial_GRC.md` misclassified in original clustering | **Corrected in this document** — see the addendum under Cluster 1. |
| 16 | `Main_Page.md` duplicate of `index.md` | **Fixed** — `Main_Page.md` deleted, `index.md` kept. |
| 17 | Can the agent actually author Python code into blocks that take it (Embedded Python Block/Module — `Embedded_Python_Block.md`, `Creating_Your_First_Block.md`, `Python_Block_Tags.md`, `Python_Block_with_Vectors.md`, `Python_Block_Message_Passing.md`, `Bandlimited_threshold_detector.md`, `Stream_Tags.md`)? Raised as a follow-up question, investigated by a dedicated subagent with live empirical testing (not just reading docs), then hardened through a full multi-persona code review (correctness/testing/maintainability/project-standards/agent-native/learnings/api-contract/reliability/adversarial) of the resulting fix. | **Yes, conditionally — and three real bugs found and fixed across two rounds, not one.** `epy_block`'s/`epy_module`'s Python source lives in a plain string param (`_source_code`/`source_code`); `change_graph`'s `update_params` sets it like any other param, no special-casing needed, and it was verified to actually *execute* correctly (a generated module was imported and its `work()` called directly on real arrays, producing numerically correct output). But GNU Radio's own `EPyBlock.rewrite()` only regenerates that block's ports/params from new source when *that block's* `rewrite()` runs — and `change_graph`'s conditional Phase-5 rewrite only fires `if add_blocks`, not on `update_params`. **Round 1** (live empirical testing): changing an existing epy_block's port *count* and connecting to the new port in the same call (no `add_blocks`) fails outright; changing a port's *dtype* (same count) and connecting in the same call is worse — the connection is made against the pre-rewrite port object, then silently dropped when the final rewrite replaces it, returning `ok: true` under `force=True`. Fixed by re-verifying every connection made in the batch's own `add_connections` still existed after the final rewrite. **Round 2** (an adversarial reviewer, dispatched as part of reviewing round 1's own fix, empirically reproduced two further bugs in that fix, not just theorized them): (a) a **P0** — the round-1 check only tracked connections made by the *same call's* `add_connections`, so a call with *only* `update_params` (no `add_connections` at all) still silently dropped an *already-existing* connection with zero errors reported; (b) a **P1** — the round-1 check compared `(block_name, port_key)` string tuples, but GNU Radio rekeys a port **in place** (same object, `Port.rewrite()` sets `self.key = self.name`) whenever a port's dtype becomes `"message"` (e.g. a `pad_sink` reconfigured to `type='message'`) — a live repro showed this false-positively rolled back a perfectly valid connection. **Fixed properly this time**: the check now snapshots actual `Connection` objects (not strings) once before any phase-3-onward mutation, and compares against the post-final-rewrite state — `Connection.__eq__`/`__hash__` are keyed on the underlying `Port` objects (confirmed by reading GNU Radio's own `Connection` class), which are identity-based with no override, so an in-place key mutation doesn't break the match while an actual port replacement correctly does. This single unified check subsumes the round-1 version entirely and catches both the P0 and P1 shapes. Also fixed in the same pass: a triply-corroborated `k` off-by-one in `generate_python` (implementation capped `k-1` block-source files against a docstring/`AGENTS.md` contract promising `k`), an overly-broad `except AttributeError` around the entire codegen call (now narrowed to an explicit `hasattr` existence check, so an unrelated internal GNU Radio bug can't be mislabeled as "unsupported version"), and `change_graph_func`'s generic `ModelRetry` hint wrongly suggesting `force=True` for force-independent errors like `connection_silently_dropped`. Covered by 4 regression tests in `tests/test_unit.py` (the original silent-drop + two-call-workaround pair, plus a new update_params-only pre-existing-connection-drop test and a new port-rekey-is-not-a-false-positive test) and 2 new `k`-boundary tests. The system prompt (`prompts.py`) and `AGENTS.md` document the two-call workaround and the corrected mechanism. No new tool needed. |

Regression check after the code/corpus changes above: `uv run pytest
tests/test_unit.py tests/test_isolation.py tests/test_exec_monitor.py` (101 +
24 + 7 tests; the 1 unrelated `test_isolation.py` failure across every run
in this session is a pre-existing Ollama Cloud weekly-quota rate limit, not
a regression) and `uv run ruff check` across the whole repo both pass. Also
verified against a live OpenRouter run (the project's actual configured
model, `deepseek/deepseek-v4-flash`) after every round of fixes.

## Harness boundary (what was actually confirmed, not assumed)

- **As audited, the harness had exactly 5 model-facing tools** (this
  section describes that original state, which the tables below still
  reflect): `inspect_graph` (read), `query_knowledge`
  (`catalog` domain = installed block index; `docs` domain = every `.md`
  under `docs/wiki_gnuradio_org/`), `web_search`/`web_fetch`, and
  `change_graph` (the only mutation tool: `add_blocks`, `remove_blocks`,
  `update_params`, `update_states`, `add_connections`/`remove_connections`,
  `force`). A 6th tool, `generate_python`, was added afterward as part of
  resolving finding #12 below — see the Resolution status table.
  Confirmed directly from `agent.py`'s `BlockAdd`/`ParamUpdate`/
  `StateUpdate` models: every item is addressed by one explicit
  `instance_name` — there is no wildcard/pattern-match update, and
  `BlockAdd` has no canvas-position field.
- Message ports work through the exact same `add_connections` string syntax
  as stream ports (`'src:port->dst:port'`), just with a string port ID
  instead of a numeric one — confirmed in the system prompt and exercised
  correctly by every message-passing tutorial in Cluster 4.
- **No filesystem tool, no shell/subprocess tool, no code-execution tool.**
  GRC's own Run/Stop toolbar button executes a flowgraph, but it's
  human-driven, not agent-callable. This one boundary is the root cause of
  the large majority of "not fully achievable" verdicts below.
- `query_knowledge`'s `docs` domain embeds **every** `.md` file with zero
  filtering (`ingest.py:184`, `corpus_dir.glob("*.md")`) — confirmed by
  direct read, not inference.
- The system prompt (`prompts.py`) documents tool syntax but says nothing
  about the no-filesystem/no-execution/no-hardware boundary, and nothing
  about how to treat a retrieved doc snippet that says "Deprecated."

## Top-priority findings

### 1. The `docs` RAG corpus contains synthetic test-fixture content, indistinguishable from real wiki citations, and it is already being served live

**Confirmed directly, not just by a subagent.** At least 9 files under
`docs/wiki_gnuradio_org/` are not genuine wiki scrapes:

- 7 carry an explicit `Provenance: ... Why relevant: this snippet grounds
  docs QA row Q11 ...` / `"GRC Agent evidence reports"` template with no
  MediaWiki navigation/footer (every genuine scrape has one): `Flowgraph.md`,
  `Sample_Rate.md`, `Variables_Block_Parameters.md`, `Tagged_Stream_Blocks.md`,
  `Embedded_Python_Block.md`, `grcc.md`, `Hier_Blocks.md`.
- 2 more have the same authored-not-scraped character with no provenance
  tag at all: `pmt_architecture.md` (a hand-written PMT summary) and
  `constellation_modulation_upgrade.md` (phrased as direct tool-call
  instructions to an agent — *"Use `update_params` to change its `type`
  parameter to `16qam`"*).

This is not a hypothetical risk: `tests/output/09_docs_stream_tags_concept_ollama.md`
(a checked-in real integration-test transcript) shows `query_knowledge`
actually returning the synthetic `Flowgraph.md` and `Tagged_Stream_Blocks.md`
snippets in the same result list as genuine `Stream_Tags.md`/
`BlocksCodingGuide.md` wiki text, with no distinguishing marker the model
or a user could act on.

**Root cause:** `ingest.py`'s unfiltered `*.md` glob (see boundary section
above) has no concept of source provenance/trust tier.

**Concrete fix:** either (a) move these 9 files out of the shipped
`docs/wiki_gnuradio_org/` corpus into a separate test-fixtures directory not
covered by `pyproject.toml`'s package-data include, if they exist purely to
ground `tests/test_integration.py` scenarios, or (b) if they're meant to
ship, add a `source_type: synthetic|wiki` field to `docs_chunks` in
`ingest.py` and surface it in `query_knowledge`'s JSON output so a citation
is never silently presented as verbatim wiki text when it isn't. Do not
leave this un-tagged — a user or the model has no way today to tell the
difference.

### 2. The no-execution boundary is real, correct, and completely undocumented to the model

Every one of the 6 clusters — all 97 files — surfaced the same pattern
dozens of times: build/configure a flowgraph (fully achievable via
`change_graph`) vs. run it, watch a live plot, drag a live slider, listen to
audio, or read console output (correctly out of scope, no execution tool
exists). The boundary itself is the right design choice given `AGENTS.md`'s
GUI-only, no-CLI rule — the gap is that `prompts.py` never states it, so the
model has no way to know it must hand off rather than attempt or claim a
workaround.

**Concrete fix** — add one paragraph to `build_system_prompt()`:

> You cannot launch GRC, save/open/rename `.grc` files, run/stop a
> flowgraph, or interact with a running flowgraph's live widgets (sliders,
> choosers, plots, buttons) or real hardware/audio. When a task needs this,
> tell the user exactly what to click or run and what to check — never
> claim to have done it yourself.

### 3. OOT module creation (`gr-modtool`, C++/Python OOT, custom buffers) is categorically out of scope — correctly, but also undocumented

Cluster 5's dominant finding: 14 of 18 files in the custom-block/OOT
cluster fail at their very first step (`gr_modtool newmod`/`add`, hand-edit
`.h`/`.cc`/`.yml`, `cmake && make && sudo make install`) because there is no
filesystem, shell, or build tool — by design, per `AGENTS.md`'s GUI-only
rule. The one thing that **is** fully achievable today: adding an Embedded
Python Block (`epy_block`) and setting its inline `source_code` param via
`update_params` — this covers "write custom block logic," just not
"package it as an installable, catalog-registered OOT module." Building a
scoped file-write tool was considered and rejected by the subagent's own
analysis: the unavoidable `sudo make install` step means a partial tool
wouldn't close the gap anyway, so the complexity/risk isn't worth it.

**Concrete fix:** add to the same system-prompt paragraph as #2: *"You
cannot create, compile, or install out-of-tree (C++/Python) modules — no
`gr-modtool`, filesystem, or build access. If asked, say so and offer an
Embedded Python Block instead for logic that can live inside one
flowgraph."* No new tool recommended.

### 4. Corpus staleness contradicts itself across files, with no rule to resolve it

Two concrete, confirmed cross-file contradictions/dead-ends:

- `Default_Header_Format_Obj..md` tells the user to "use the CRC32 Async
  block," while `Async_CRC32.md` — in the same corpus — documents that
  exact block as **`Deprecated in 3.10`**, superseded by CRC Append/CRC
  Check (`Added in 3.10.2.0`).
- `index.php_OutOfTreeModules.md` opens with its own **Deprecation
  Warning** (superseded, XML block defs, SWIG glue) yet is ingested
  unfiltered alongside the current `Creating_C++_OOT_with_gr-modtool.md`
  (pybind11) — a query could surface either as authoritative.
- `Coding_guide_impl.md` and `index.php_Guided_Tutorial_Programming_Topics.md`
  are both genuine wiki pages that are pure redirect stubs (*"replaced by
  GREP1"*, *"merged with Stream Tags usage manual page"*) — retrievable but
  contentless.

**Concrete fix:** one general system-prompt rule instead of hand-editing
every stale page: *"If a retrieved doc snippet says 'Deprecated' or
'replaced by'/'merged with', prefer whatever replacement it names instead of
the snippet's own instructions."* Separately, consider excluding
self-declared-deprecated pages from ingestion entirely.

### 5. `tutorial_manifest.txt` is dead code

Its own comment claims *"Only exact filenames listed here are indexed as
tutorial_chunk"* — grepped directly, zero references to
`tutorial_manifest`/`tutorial_chunk` anywhere in `src/`. It does nothing.
**Fix:** either wire it up for real (tag `docs_chunks` with a `chunk_type`
so tutorial vs. reference pages are distinguishable in results) or delete
the file — it currently documents a behavior that doesn't exist.

## Smaller, concrete gaps (lower priority, still actionable)

| # | Gap | Where it surfaced | Proposed fix |
|---|---|---|---|
| 6 | No tool loads an externally-fetched/pasted `.grc` YAML into the live canvas | `Sample_Rate_Tutorial.md`, `IQ_Complex_Tutorial.md` reference external example `.grc` files by URL | Add a `load_flowgraph(grc_yaml: str)` tool that parses fetched/pasted YAML and replaces the in-memory canvas |
| 7 | No filesystem browse/list capability | `Reading_and_Writing_Binary_Files.md` needs to discover which recorded files already exist by extension | A narrowly-scoped, read-only `list_files(dir, pattern)` restricted to a File Source/Sink's own path directory — not a general filesystem tool |
| 8 | `inspect_graph`'s validation never catches Embedded Python Block runtime/logic errors, only structural/syntax issues at load time | `Bandlimited_threshold_detector.md`; `Python_Block_with_Vectors.md` also surfaces a genuine GRC quirk (vector-size validation checks only the `__init__` default arg, not the runtime param — can show "valid" and still crash on Run) | Document the quirk in the system prompt; optionally surface an `ast.parse()`-only syntax check on `epy_block` source in `inspect_graph`'s output (stays read-only) |
| 9 | No bulk pattern-match update (only per-`instance_name`) | `Porting_Existing_Flowgraphs_to_a_Newer_Version.md`'s "replace every WX GUI block with QT GUI" | No fix needed — `inspect_graph` first to enumerate matching instances, then one batched `change_graph` call already covers this in two round trips |
| 10 | Missing prerequisite content in the corpus | `Simulation_example__BPSK_Demodulation.md` explicitly defers to a `QPSK_Mod_and_Demod.md` that doesn't exist anywhere in the corpus; `GNU_Radio_3.10_OOT_Module_Porting_Guide.md` has no sequel for a hypothetical future GR version | Corpus-completeness gap, not a tool gap — re-scrape the missing prerequisite page(s) |
| 11 | No canvas-position field on `add_blocks` | `Streams_and_Vectors.md`'s "move and reconnect" step | Cosmetic only (doesn't affect generated code) — low priority; optional `position: [x, y]` field |
| 12 | No read-only "preview generated Python" capability | `Flowgraph_Python_Code.md` | Optional `generate_python(preview: bool=true)` tool returning Mako-rendered source as a string, no disk write |
| 13 | Corpus contains its own outdated/dead code samples | `Stream_Tags.md` and `Polymorphic_Types_(PMTs).md` show Python-2 `print` statements that would `SyntaxError` if copied into a current `epy_block`; `Flowgraph_Python_Code.md`'s generated-boilerplate example uses `distutils` (removed in Python 3.12); `BlocksCodingGuide.md` describes pre-pybind11 SWIG bindings, contradicted by the corpus's own current OOT tutorial; `UsingEclipse.md` and `E310_FM_Receiver.md` are pinned to Python 2 / GR 3.7-era tooling | Same deprecation-preference rule as finding #4 covers this; flag to users that these specific pages describe unmaintained/EOL toolchains |
| 14 | Tutorial content models ad-hoc reinvention over an existing reliable mechanism | `Reading_and_Writing_Binary_Files.md` builds a hand-rolled filename-metadata scheme (string-concatenated rate/format/timestamp) while naming, but not using, GNU Radio's own File Meta Sink / SigMF (`gr-sigmf`) | Not a harness bug — but the agent should recommend File Meta Sink/SigMF over reproducing this pattern when asked to build a recording pipeline from scratch, per the "no brittle reinvention" engineering rule |
| 15 | One tutorial page's identity doesn't match its cluster | `index.php_Guided_Tutorial_GRC.md` was filed under "dev tooling/meta" by filename but is actually a genuine step-by-step beginner GRC tutorial (see Addendum below) | Re-classify; no harness action needed — see its row under Cluster 1's addendum |
| 16 | Corpus ships a duplicate page under two filenames | `Main_Page.md` and `index.md` are byte-identical (confirmed via `diff`) except for MediaWiki URL query-string formatting — the same page scraped twice, initially missed entirely by this audit's own file clustering and caught only during verification | Delete one of the two files — content is identical, so this is pure redundancy, not a factual issue |

---

## Cluster 1 — Flowgraph Fundamentals, Variables & I/O (20 files)

| File | What the tutorial has the user do | Achievable? | If not fully: exact failing step + root cause | Concrete proposed fix | Outdated content |
|---|---|---|---|---|---|
| Your_First_Flowgraph.md | Launch GRC, edit Options Id/Title, Save-As, drag/wire Signal Source→Throttle→QT GUI Sinks, Play. | Partial | Launch/Save/Play unreachable — correctly out of scope (no launch/filesystem/execution tool). Block adds, param edits, wiring achievable. | System-prompt boundary sentence (#2). | none |
| Flowgraph.md | Conceptual only; explicitly disclaims mutation authority. | Yes | N/A for graph actions — but this is one of the 9 synthetic corpus files (finding #1). | See finding #1. | flagged as synthetic, not "outdated" |
| Flowgraph_Python_Code.md | Generate `.py` from a flowgraph, read/edit it, run via `python3`. | No | Generate/edit/run all need filesystem write and/or execution — none exist. | Optional read-only `generate_python(preview=true)` tool (#12); running stays out of scope. | Generated boilerplate uses `distutils.version.StrictVersion` — removed from Python 3.12+ stdlib, won't run on current Python. |
| Variables_in_Flowgraphs.md | Add/rename a Variable, point a block param at it, run to watch the spectrum move. | Partial | Run/observe unreachable (out of scope). Add/rename/param-set achievable — `add_blocks` lets the caller choose `instance_name` directly, so no separate "rename" primitive is needed. | System-prompt boundary sentence. | none |
| Variables_Block_Parameters.md | Restates variable/param-reference mechanics; no steps. | Yes | N/A — synthetic corpus file (finding #1), near-duplicate content of Variables_in_Flowgraphs.md. | See finding #1. | flagged as synthetic |
| Python_Variables_in_GRC.md | Create Variable blocks holding floats/ints/strings/lists/tuples. | Yes | No gap — plain `add_blocks`/`update_params` with Python-literal string values. | none | none |
| Runtime_Updating_Variables.md | Replace a Variable with a QT GUI Range/Chooser sharing its Id, run, drag the live widget. | Partial | Run/live-widget-drag unreachable (out of scope). The ID-collision-then-disable dance has a cleaner direct equivalent: `remove_blocks` the old Variable + `add_blocks` the new widget with the same `instance_name` in one call. | System-prompt boundary sentence. | none |
| YAML_GRC.md | Reference for hand-authoring a new block's `.block.yml`/`.tree.yml`. | No | Requires filesystem write + GRC block-path rescan — correctly out of scope (defining new block *types*, not editing instances, per the OOT boundary in finding #3). | Document that the agent edits existing block instances only, never defines new block types. | none — schema still matches current GRC |
| Sample_Rate.md | Defines decimation/interpolation; no steps. | Yes | N/A — synthetic corpus file (finding #1). | See finding #1. | flagged as synthetic |
| Sample_Rate_Tutorial.md | Adjust a Frequency variable in a pre-built demo; references two externally-linked example `.grc` files. | Partial | Opening either linked external `.grc` is unreachable — no tool loads a fetched/pasted `.grc` into the canvas (gap #6). Frequency-adjust achievable; watching aliasing needs Run (out of scope). | `load_flowgraph` tool (#6). | none |
| Sample_Rate_Change.md | Build an interpolation demo (several blocks, cross-referencing param expressions, a Comment), run, enable Max Hold, drag a slider. | Partial | Run/Max-Hold/slider unreachable (out of scope). Every block/param/connection add, including the Comment param, is achievable. | System-prompt boundary sentence. | none |
| IQ_Complex_Tutorial.md | Mostly theory; opens linked example `.grc` files, adjusts `delta_f`, Save-As, delete/reconnect blocks, build an AM demodulator. | Partial | Opening linked `.grc` (gap #6) and Save-As (no filesystem tool) unreachable; observing rotation needs Run (out of scope). Reconnect/demodulator-build achievable via `query_knowledge`+`change_graph`. | `load_flowgraph` tool (#6); rest is correctly out of scope. | none |
| Signal_Data_Types.md | Change Output Type, cycle dtypes via UP/DOWN, run to see a real waveform. | Partial | Run unreachable (out of scope). UP/DOWN cycling has a direct one-shot equivalent (`update_params` sets the dtype string directly, strictly simpler); the red-mismatch-arrow status is exactly `inspect_graph`'s validation output. | none needed beyond boundary sentence. | none |
| Streams_and_Vectors.md | Build/rewire a vector demux flowgraph; move and reconnect two Time Sinks; run twice. | Partial | Run unreachable (out of scope). "Move" has no equivalent — `add_blocks`/`update_params` carry no x/y field (gap #11); "reconnect" is fully achievable. | Optional `position` field on `add_blocks` (#11) — cosmetic, low priority. | none |
| Converting_Data_Types.md | Fix dtype mismatches, add a Char-to-Float converter, Play. | Partial | Only Play unreachable (out of scope). Converter lookup + all dtype/connection edits achievable. | none needed beyond boundary sentence. | none |
| type_scaling.md | Reference table of float↔int scale factors. | Yes | No gap — reference only, internally consistent with Reading_and_Writing_Binary_Files.md. | none | none |
| Audio_Sink.md | Configure Sample Rate/Device Name (via OS-specific hardware enumeration)/Num Inputs; run to hear a tone. | Partial | Device enumeration and Run both correctly out of scope (real hardware, no execution). Sample Rate/Num-Inputs/default-Device-Name achievable. | System-prompt: never fabricate a device-name string — leave default/blank or ask. | none |
| Audio_Source.md | Symmetric to Audio Sink for a microphone input, referencing a sound-detector chain. | Partial | Same hardware/Run gap as Audio_Sink.md. Building the referenced detector chain fully achievable. | Same as Audio_Sink.md. | none |
| Binary_Files_for_DSP.md | Reference on binary sample-file conventions (dtype, real vs. complex I/Q, endianness). | Yes | No gap — conceptual only. | none | none |
| Reading_and_Writing_Binary_Files.md | Build File Sink/Source chains with a hand-composed filename (Import block + timestamp variable); pick among existing files by extension; add converter blocks; run briefly; check with `ls`. | Partial | Picking among existing files needs directory listing — no tool exposes this (gap #7). Run/`ls` correctly out of scope. Every block/param/Import/converter edit fully achievable. | Scoped read-only `list_files(dir, pattern)` (#7). | Reproduces a fragile ad-hoc metadata-in-filename scheme instead of using File Meta Sink/SigMF, which the page itself names but skips (gap #14). |

**Addendum — misfiled tutorial found during Cluster 6's audit:** `index.php_Guided_Tutorial_GRC.md` was listed under the dev-tooling/meta cluster by its saved filename, but its content is a genuine beginner GRC tutorial (add a QT GUI Time Sink; edit Options `ID`/`Generate Options`; add/wire Signal Source+Throttle; reproduce a dtype-mismatch error). Verdict: Partial — adding/editing/wiring blocks and reading the resulting error via `inspect_graph` are fully achievable; Execute(F6)/Generate(F5)/Kill(F7) and observing the rendered waveform are correctly out of scope (same boundary as row 1 above). No outdated content found (cites GRC "3.8.0+", still accurate against 3.10.x).

## Cluster 2 — DSP Building Blocks & Filters (14 files)

| File | What the tutorial has the user do | Achievable? | If not fully: exact failing step + root cause | Concrete proposed fix | Outdated content |
|---|---|---|---|---|---|
| Low_Pass_Filter_Example.md | Build Source→LPF→Throttle→Freq Sink→Range chain; set Cutoff/Transition Width; run; enable Max Hold; drag slider. | Partial | Run/Max-Hold/slider unreachable (out of scope). All construction/param-setting achievable. | System-prompt boundary sentence. | none |
| Band_Pass_Filter.md | Reference page (FIR Type, Decimation, Gain, Cutoff, Transition Width, Window, Beta) + AM-receiver example. | Yes | No gap. | none | none — `firdes.band_pass`/`gr_filter_design` still current (Context7-verified against `/gnuradio/gnuradio`) |
| Band-pass_Filter_Taps.md | Reference page for the Band-pass Filter Taps block, storing generated taps in a variable. | Yes | No gap — in-tree block, plain string-expression params. | none | none |
| Designing_Filter_Taps.md | Replace LPF with Frequency Xlating FIR + taps variable; add Import block; toggle Variable blocks disabled/enabled; flip filter Type Real→Complex; run + Max Hold repeatedly. | Partial | Every run/Max-Hold observation unreachable (out of scope). Import-block source, `update_states` disable/enable, and Type-enum edits all achievable. | System-prompt boundary sentence. | none — Import-block-for-numpy and Frequency Xlating FIR Filter are current idioms |
| Frequency_Shifting.md | Build noise→LPF→Freq Sink chain; add Multiply block to shift spectrum; swap a Variable for a QT GUI Range; run repeatedly to observe shift. | Partial | All run/observe steps unreachable (out of scope). Multiply-block wiring and variable-to-widget swap fully achievable. | System-prompt boundary sentence. | none |
| AGC.md | Reference page (Rate, Reference, Gain, Max gain). | Yes | No gap. | none | none |
| Binary_Slicer.md | Reference page, no parameters. | Yes | No gap. | none | none |
| Adaptive_Algorithm.md | Reference page (Algorithm Type, Constellation Object reference, Step Size, Modulus). | Yes | No gap — object reference is the same variable-instance-name pattern the harness already supports. | none | none |
| Bandlimited_threshold_detector.md | Build a synthetic-spectrum flowgraph with array-expression Variable blocks + an Embedded Python Block writing detections to a file; run to see the live demo. | Partial | Running/seeing the demo out of scope. Authoring the `epy_block`'s source via `update_params` achievable, but its `work()` logic cannot be verified (gap #8). | Optional `ast.parse()`-only syntax check surfaced in `inspect_graph` for `epy_block` source (#8); boundary sentence for the run step. | none — `gr.sync_block`/`in_sig`/`out_sig`/`work()` API still current |
| Constellation_Decoder.md | Reference page (Constellation object reference). | Yes | No gap. | none | none |
| M-ASK,_M-PSK,_and_QAM-M_Mod_and_Demod.md | Open a specific downloadable `.grc` (e.g. BPSK.grc), edit Constellation Modulus, add K-bit Unpack, swap constellation object type, observe BER as noise increases. | Partial | Opening an external `.grc` file — correctly out of scope (no filesystem tool; harness only ever has one already-open flowgraph). Observing BER needs Run (out of scope). Modulus/Unpack/type-swap edits achievable. | System-prompt sentence: "you can only edit the currently-open flowgraph; ask the user to open the target file first." | none — page edited Oct 2025, workflow matches current GR |
| Burst_Shaper.md | Reference page (Window Taps, Pre/Post-padding, Phasing Symbols, Length Tag Name). | Yes | No gap — Window Taps is a vector-valued string expression. | none | none — tagged "Tested With 3.10" by the wiki itself |
| Packing_Bits.md | Add Random Source, cycle its output type via UP/DOWN, build a Pack/Unpack-K-Bits histogram/time-sink chain, run+observe, click-drag to zoom. | Partial | Run/observe/zoom all unreachable (out of scope). The UP/DOWN shortcut is just a convenience for `update_params({"type": "byte"})` — fully achievable directly. | System-prompt boundary sentence. | none |
| constellation_modulation_upgrade.md | Not a genuine wiki page — see finding #1. | Yes (mechanically) | RAG corpus gap — see finding #1. | See finding #1. | flagged as synthetic/non-wiki, not a currency issue |

## Cluster 3 — End-to-End Simulation & Hardware Receiver Builds (10 files)

| File | What the tutorial has the user do | Achievable? | If not fully: exact failing step + root cause | Concrete proposed fix | Outdated content |
|---|---|---|---|---|---|
| Simulation_example__BPSK_Demodulation.md | Build a multi-stage BPSK sim, repeatedly deferring to "same as QPSK tutorial" for 3 key stages. | Partial | `QPSK_Mod_and_Demod.md` — the explicit prerequisite — doesn't exist in the corpus (gap #10), so the agent has nothing grounded to retrieve for those stages and would have to fabricate parameters. Visual plot verification also out of scope. | Re-scrape the missing QPSK page (#10); system-prompt boundary sentence for verification steps; the agent should say NEEDS CLARIFICATION rather than invent Symbol Sync/Costas-loop parameters. | none in this file's own text |
| Simulation_example__FSK.md | Part 1: build a self-contained FSK sim (fully achievable). Part 2: clone a separate GitHub repo, run 3 Python programs across 3 terminals for a real file transfer. | Partial (Part 1 Yes, Part 2 No) | Part 2 fails at `git clone` and every step after — no shell/git/process tool exists, by design. | System-prompt sentence: cannot clone repos, run shell commands, or launch external processes. Do not add a shell tool. | none — tested with 3.10 |
| Simulation_example__Narrowband_FM_transceiver.md | Build two separate flowgraphs (RX/TX), run both simultaneously in two terminals, speak into a mic, listen through speakers. | Partial | Running two simultaneous processes + real audio hardware correctly out of scope. Building each flowgraph's ZMQ/audio blocks and params fully achievable per-file. | System-prompt: can configure Audio/ZMQ params but cannot run or use real audio hardware. | none |
| Simulation_example__Single_Sideband_transceiver.md | Build a ~25-30 block SSB transceiver (dual RX methods), press F6, tune live by ear. | Partial | Execute/live-audio-tuning-by-ear out of scope. The full block structure is buildable in one or a few batched `change_graph` calls. | System-prompt boundary sentence. | none — tested with 3.10, edited Dec 2025 |
| Basic_OFDM_Tutorial.md | Conceptual/reference page; OFDM block names given, but no concrete numeric carrier/pilot/sync parameters for an actual working loopback. | Partial | Catalog lookup of `ofdm_*` blocks has no gap, but the page gives no actionable recipe — inventing FFT length/carrier allocation would violate the no-assumptions rule. | Agent should respond NEEDS CLARIFICATION rather than fabricate OFDM parameters; separately, the actual `gr-digital` `ofdm_tx`/`ofdm_rx` example (not in this wiki-only corpus) would be a better source to ingest. | none — terminology matches current gr-digital API |
| RTL-SDR_FM_Receiver.md | Build a full FM-receiver flowgraph (Soapy RTLSDR Source, WBFM Receive, resampler, Audio Sink); plug in real hardware; run; diagnose overrun characters from terminal. | Partial | Real hardware I/O, Run, and reading console overrun output all correctly out of scope (also no console-read tool). All block/param construction, including hardware-source string params, fully achievable. | System-prompt: hardware-source params are settable as strings, but no hardware detection/connection or console-reading capability exists. | none — correctly uses current Soapy RTLSDR Source (not legacy gr-osmosdr), edited Oct 2025 |
| B200-B205mini_FM_Receiver.md | Same shape as RTL-SDR receiver, using UHD: USRP Source. | Partial | Same hardware/Run/console gap as RTL-SDR row. | Same fix. | none — "UHD: USRP Source" still correct for B-series in current GR |
| E310_FM_Receiver.md | Almost entirely Linux/embedded-systems: `nmap` scan, SSH in, shell scripts for static IP, `sshfs` mount, IDE build-command config, hand-written standalone Python script running on the USRP. | No | Fails at the first `nmap` step and essentially everything after — no shell/SSH/filesystem/arbitrary-Python-authoring tool exists, by design; also real E310 hardware. | Explicit system-prompt boundary: no SSH, shell/network commands, filesystem mount, or standalone-script authoring. No tool addition is appropriate — fundamentally outside a GRC-graph-editing agent's mission. | **Significant** — embedded example is `gnuradio-companion 3.7.11` (long-EOL) and Python-2-only (`print "..."` without parens, `#!/usr/bin/env python2`, `distutils.version.StrictVersion`) — will not run on current GR 3.10 + Python 3.12 without a rewrite. |
| Guided_Tutorial_Hardware_Considerations.md | Build a USRP spectrum-analyzer with message-port click-to-tune; then four image-only "Building an FM Receiver" links with no transcribed text. | Partial | The spectrum-analyzer's message-port connection (`'qtgui_sink_0:freq'->'usrp_source_0:command'`) matches the tool's documented syntax exactly — no gap. The four image-only flowgraph diagrams have zero transcribed text anywhere in the corpus — RAG corpus gap. | For the images: either transcribe each diagram's blocks/params into the ingest corpus, or have the agent respond NEEDS CLARIFICATION rather than guess at an unreadable image's content. | none in the readable text |
| Pushbutton_IQ_Recorder_with_descriptive_filenames.md | Build a synthetic-spectrum flowgraph with Import blocks, Python-expression filename variables, QT GUI Entry/LED, and a conditional File Sink; verify with `watch ls -l`. | Partial (Yes for build) | Only the final `watch ls -l` filesystem-verification step is out of scope (no execution/filesystem-read tool). The entire build — Import blocks, Python-expression params, QT widgets, conditional File Sink — has no gap; cleanest fully-in-scope example in this cluster. | System-prompt boundary sentence for the verification step only. | none — explicitly requires "GNURadio 3.10+", current constructs throughout |

**Cross-cutting note from this cluster's audit:** flowgraph *size* is not itself a limitation — `change_graph`'s batched, fixed-phase-order shape (`add_blocks` before `add_connections` in the same call) lets a full 25-30-block topology land in one or a few calls, not one call per block.

## Cluster 4 — Messaging, Tags, Packets & PMTs (15 files)

| File | What the tutorial has the user do | Achievable? | If not fully: exact failing step + root cause | Concrete proposed fix | Outdated content |
|---|---|---|---|---|---|
| Packet_Communications.md | Build PDU- and stream-based packet flowgraphs; compile/run via `grcc`/terminal; run a standalone pre-padding script. | Partial | Executing (`python3 pkt_8.py`) and running the standalone padding script both out of scope/missing tool (no execution, no filesystem). All block/param/message-port wiring achievable. | System-prompt boundary sentence. | none — uses current CRC Append/Check, tested 3.10.9.2 |
| CRC_Check.md | Add and configure a single CRC Check block. | Yes | No gap. | none | none — added in 3.10.2.0 |
| CRC_Append.md | Add and configure a single CRC Append block. | Yes | No gap. | none | none — added in 3.10.2.0 |
| Async_CRC32.md | Reference for a legacy CRC block. | Yes (mechanically) | No functional gap, but the corpus can surface this deprecated page for a generic "CRC" query. | System-prompt deprecation-preference rule (finding #4). | Page itself states `Deprecated in 3.10`. |
| Tagged_Stream_Blocks.md | Conceptual note; explicitly not mutation authority. | Yes | RAG corpus gap — this is one of the 9 synthetic files (finding #1), reads as a paraphrase, not a verbatim scrape. | See finding #1. | flagged as synthetic |
| Stream_Tags.md | Write an `epy_block` that calls `add_item_tag`; run and visually confirm tag markers. | Partial | Run/visual-confirm out of scope. `inspect_graph`'s validation execs the block class and would catch import/syntax errors, but not tag-offset logic errors (gap #8). | Document the `inspect_graph`-catches-syntax-not-logic distinction (#8). | The example Python snippet uses Python-2 `print 'key:', key` syntax — a `SyntaxError` under GNU Radio's current Python-3-only runtime if copied verbatim. |
| Message_Passing.md | Learn the message-port API; run two ZMQ-linked flowgraphs as live processes, type into a live GUI widget; post a message via an external script's `_post()`. | Partial | Multi-process/live-GUI and the external-script example both out of scope/missing tool. Single-flowgraph message-port wiring (`'pdus'`, `'print'`) fully achievable. | System-prompt boundary sentence. | none — uses current 3.8+ `set_msg_handler` lambda form |
| Python_Block_Message_Passing.md | Create two `epy_block`s with message ports/handlers, wire them + a named Virtual Sink/Source pair, run to watch alternating output. | Partial | Run/visual-confirm out of scope. Block creation, source-code edits, and message-port wiring with string port IDs (`'selectPort'`) fully achievable — confirms the harness's own claim that message-port wiring works through the same syntax. | System-prompt boundary sentence. | none |
| Understanding_ZMQ_Blocks.md | Distinguish PUB/SUB, PUSH/PULL, REQ/REP pairs; run flowgraphs alongside standalone Python ZMQ scripts over real TCP. | Partial | Standing up/running the standalone scripts and real TCP exchange out of scope/missing tool. Adding/configuring the ZMQ blocks themselves fully achievable. | None needed — already fully covered for the achievable slice. | none |
| Understanding_XMLRPC_Blocks.md | Open two separate `.grc` files, run both, drag a live slider; extend to SSH-tunneled two-host networking, standalone automation, real SDR hardware. | No | Nearly everything fails: two-simultaneous-flowgraphs is architecturally out of scope (harness is scoped to one open flowgraph), live GUI dragging out of scope, SSH/networking/automation missing tool, real hardware out of scope. | State the narrower achievable scope (single block's IP/Port params on the one open flowgraph) explicitly in the system prompt so the model doesn't overpromise. | none |
| Virtual_Sinks_and_Sources.md | Add paired Virtual Sink/Source blocks with matching Stream IDs. | Yes | No gap. | none | none |
| Polymorphic_Types_(PMTs).md | Reference for the PMT API, used to hand-write message/tag value expressions. | Yes (for producing correct param strings) | Verifying a hand-constructed PMT expression is well-formed before running needs execution — same universal no-execution constraint (gap #8-adjacent). | No new tool — rely on `query_knowledge(catalog)` + GRC's own generate-time evaluation to catch gross errors. | The opening REPL example uses Python-2 `print P` syntax — `SyntaxError` under current Python 3. |
| pmt_architecture.md | Conceptual note on PMT dictionary immutability; no build steps. | Yes | No functional gap, but this is one of the 9 synthetic files (finding #1) — no wiki nav/footer, no matching real article title. | See finding #1. | flagged as synthetic |
| File_transfer_using_Packet_and_BPSK.md | Iteratively build a 7-stage BPSK file-transfer flowgraph; verify each stage by running, tuning live sliders, and (stages 6-7) real SDR hardware loopback. | Partial | Every verification/tuning/hardware step out of scope. All graph-construction stages (block adds, Tagged Stream Mux extension, Channel Model params, enable/disable) fully achievable — the right scope for agent value here. | System-prompt boundary sentence; hand off tuning/testing/hardware instructions instead of attempting them. | none — tested with 3.10.9.2, edited 2026-03-14 |
| Default_Header_Format_Obj..md | Reference for `digital.header_format_default(...)` as a Variable block's Value, paired with the "CRC32 Async block." | Yes (mechanically) | No tool gap, but a genuine cross-file contradiction — see finding #4 (this page recommends a block the corpus's own `Async_CRC32.md` documents as deprecated). | See finding #4. | Recommends a deprecated block — flagged in finding #4. |

## Cluster 5 — Custom Block / OOT Module Development & Porting (18 files)

| File | What the tutorial has the user do | Achievable? | If not fully: exact failing step + root cause | Concrete proposed fix | Outdated content |
|---|---|---|---|---|---|
| Creating_Your_First_Block.md | Add/edit an `epy_block` (rename param, edit `in_sig`/`out_sig`/`work()`), wire into a flowgraph, run, read plots. | Partial | Run/read-plots out of scope. `epy_block` creation and every source-code edit via `update_params` fully achievable — the one form of custom block coding that works end-to-end today. | System-prompt: document the `epy_block` path explicitly as fully usable; boundary sentence for the run step. | none — `gr.sync_block` API matches current GR |
| Creating_C++_OOT_with_gr-modtool.md | `gr_modtool add`, hand-edit `.h`/`.cc`/`.yml`, `cmake && make && sudo make install && ldconfig`, Reload, use in GRC. | No | Every step from `gr_modtool add` onward — no shell/filesystem tool, correctly out of scope (compiling + `sudo`-installing is a deliberate build/security boundary). | Document as permanently out of scope (finding #3); a partial file-write tool wouldn't close the gap since compilation/install remain unavoidable human steps. | none — pybind11 structure matches GR 3.10+; tutorial pinned to v3.10.1.1/Ubuntu 21.10 |
| Creating_Python_OOT_with_gr-modtool.md | `gr_modtool newmod`/`add`, hand-edit `.py`/`.yml`, build/install, use in GRC. | No | Same as C++ OOT row — missing tool + correctly out of scope. | Same as above. | none — matches current `gr_modtool` workflow |
| grcc.md | Project-authored explainer of the `grcc` headless compiler, used as this project's own compile-validation evidence. | Yes | N/A — explanation-only; not a GUI tutorial. One of the 9 synthetic corpus files (finding #1). | See finding #1. | flagged as synthetic, not outdated |
| Embedded_Python_Block.md | Project-authored explainer of what an `epy_block` is. | Yes | N/A — explanation-only. One of the 9 synthetic corpus files (finding #1). | See finding #1. | flagged as synthetic |
| Python_Block_Tags.md | Build two `epy_block`s (tag writer/reader) + Virtual Sink/Source/Tag Gate; run, read tag markers on a Time Sink. | Partial | Run/visual-read out of scope. All block creation and code edits achievable. | Same system-prompt note as Creating_Your_First_Block.md. | none — `add_item_tag`/`get_tags_in_window`/`pmt.intern` current |
| Python_Block_with_Vectors.md | Build a vector `epy_block` (max-hold logic), edit `in_sig`/`out_sig` to vector tuples, add a second port, run and compare plots. | Partial | Run/compare out of scope. All edits achievable. Separately surfaces a genuine GRC quirk: vector-size validation checks only the `__init__` default, not the runtime param — can show "valid" via `inspect_graph` and still crash on Run. | System-prompt caution about the vector-size validation quirk (#8). | none — vector tuple syntax and 3-D indexing current |
| Hier_Blocks.md | Project-authored explainer of what a hier block is. | Yes | N/A — explanation-only. Also carries the same "Provenance:" synthetic-content marker as the finding #1 set (confirmed by direct grep) — a 9th such file. | See finding #1. | flagged as synthetic |
| Hier_Blocks_and_Parameters.md | Right-click "Create Hier," set Options/Parameter/Pad blocks, "Generate the flow graph" (writes `.py`/`.yml`), copy into `~/.grc_gnuradio/`, Reload Blocks, use the new block, later `rm` + Reload to delete. | Partial | "Create Hier" (no `change_graph` equivalent), file generation/copy/Reload (no filesystem, no catalog-refresh path), and deletion all out of scope/missing tool. Building the inner sub-flowgraph's contents (blocks, Parameter blocks, Pad Source/Sink, Options params) is fully achievable in-memory. | System-prompt: document the achievable inner-content-editing slice; no new tool — writing hier artifacts + catalog reload would duplicate GRC's own Generate/Reload machinery for a rarely-batch-needed action. | Notes GR-version-dependent output paths (3.8 vs. 3.10) — documented version drift in the source tutorial, not a harness error, but worth the agent knowing current GR defaults differently than the 3.8 example shown. |
| BlocksCodingGuide.md | Reference guide for writing a C++ OOT block (headers, `work()`/`general_work()`, IO signatures, stream-tag APIs). | No (for hands-on use) | Any hands-on application needs filesystem + build — same root cause as the OOT tutorials. Retrievable as reference text only. | Document as reference-only/out-of-scope for hands-on use. | **Significant** — shows un-namespaced `gr_sync_block`/`gr_make_io_signature` and SWIG `.i` interface files; GNU Radio moved to `gr::` namespacing and pybind11 around 3.8/3.9, contradicted by this same corpus's own current `Creating_C++_OOT_with_gr-modtool.md`. |
| Coding_guide_impl.md | Redirect stub: "replaced by GREP1." | N/A | No content — genuine wiki page, but a dead end if retrieved (finding #4). | Consider pruning from the corpus, or ingest the actual GREP1 content it points to. | Self-superseded, but genuinely wiki content (has real MediaWiki nav/footer) — not synthetic, just unhelpful. |
| Importing_Libraries.md | Add an Import block (`import numpy as np`), create variables calling library functions, wire a randomized-amplitude Signal Source, run and observe. | Partial | Run/observe out of scope. Import-block and Python-expression variable creation fully achievable. | System-prompt boundary sentence. | none — current GRC behavior |
| CustomBuffers.md | Design doc for CUDA/GPU/FPGA custom-buffer blocks: C++ kernel code, `io_signature` changes, separate OOT module. | No | Requires real accelerator hardware + C++ editing + separate OOT compile/install — doubly out of scope. | None — inherently a code-and-hardware problem outside this agent's remit. | none — accurately describes current (3.10+) accelerated-buffer architecture as a design doc |
| Add.md | Reference for the installed "Add" block; example dial-tone flowgraph; run and hear the tone. | Partial (Yes for build) | Only "run and hear" out of scope (also needs real audio hardware/human hearing). Full construction achievable via the catalog lookup + `change_graph`. | System-prompt boundary sentence. | none |
| Porting_Existing_Flowgraphs_to_a_Newer_Version.md | Replace deprecated WX GUI blocks with QT GUI equivalents across many blocks; re-pick renamed enums; regenerate if referencing a stale filename. | Partial | Bulk replace-by-`block_id` isn't directly supported — `ParamUpdate`/`StateUpdate` require one explicit `instance_name` each, confirmed directly against the pydantic models in `agent.py` (gap #9). Regenerating the flowgraph needs GRC's own Generate action (no filesystem). | Document the enumerate-via-`inspect_graph`-then-batch-`change_graph` workflow (#9) — recommended over adding wildcard/pattern-match support, given true bulk-rename cases are rare enough that the added schema complexity/ambiguity risk isn't worth it. | none in the porting content itself — described migrations (3.7→3.10) are accurate history |
| GNU_Radio_3.10_OOT_Module_Porting_Guide.md | Update an existing OOT module's C++ source for 3.10 (C++17, CMakeLists/pybind, logging migration). | No | Every step is a file edit + rebuild — missing tool + out of scope. | Document as out of scope, same as other OOT rows. | Corpus has no sequel for a hypothetical future GR version (gap #10) — could surface this stale 3.10-only guide as if complete for a newer-version question. |
| index.php_OutOfTreeModules.md | Full legacy OOT tutorial: `gr_modtool newmod`/`add`, hand-edit impl, `cmake && make && make test`, `makexml`/`makeyaml`, `sudo make install`. | No | Same root cause as the two Creating_*_OOT tutorials. | Document as out of scope; no new tool. | **Confirmed outdated by the page itself** — opens with its own Deprecation Warning; shows XML block defs and SWIG glue, both superseded by YAML/pybind11 (see finding #4). |
| index.php_Guided_Tutorial_Programming_Topics.md | Stub/redirect: "merged with PMT/Stream Tags/Message Passing usage-manual pages." | N/A | No actionable steps — dead-end stub if its true content isn't separately ingested (finding #4). | Confirm the actual target usage-manual pages are separately present in the corpus; if not, ingest them. | Genuine wiki content, not synthetic — just a merged-elsewhere stub. |

## Cluster 6 — Dev Environment, Tooling & Community, meta pages (19 files)

Per the user's confirmed scoping decision, this cluster was audited briefly
to *confirm* out-of-scope rather than skipped outright.

| File | Content category | Actionable flowgraph-editing step? | Achievable / root cause if not | Fix | Outdated content |
|---|---|---|---|---|---|
| VOLK_Guide.md | Internals reference (SIMD kernels, `volk_profile`) | No | N/A — not `.grc` editing | none needed | none — page old (2019) but not factually wrong |
| DevelopingWithGit.md | Dev-workflow (git contribution process) | No | N/A | none needed | Branch-naming guidance (tracks `next`, SVN-to-git) is stale vs. current `main`/`dev-4.0` |
| UsingCB.md | IDE setup (Code::Blocks for OOT C++) | No | N/A — no IDE/shell tool | none needed | Looks superseded next to the actively-maintained VSCode path |
| UsingEclipse.md | IDE/debugging guide (Eclipse+GDB for OOT C++) | No | N/A | none needed | Clearly outdated: Eclipse CDT 4.6.3/Ubuntu 16.04 (2017), Python 2 (`raw_input()`), SWIG (`howto_swig`) |
| UsingVSCode.md | IDE setup (current) | No | N/A | none needed | none — most current page in the set (GR 3.10.9.2, VSCodium 1.92, Ubuntu 24.04, Sept 2024) |
| InstallingGR.md | Installation instructions | No | N/A — no package-manager/shell tool | none needed | Notes Ubuntu 20.04 support ending May 2025 — already past as of this audit (2026-07-18); rest current (edited Jan 2026) |
| Development.md | Contribution FAQ | No | N/A | none needed | none significant |
| DevelopersCalls.md | Community/events log | No | N/A | none needed | Stale — log stops mid-2021, not maintained since |
| Octave.md | Post-processing tooling guide (Octave on `file_sink` dumps) | No | N/A — operates outside the running flowgraph | none needed | none flagged; page itself notes Python/NumPy/SciPy as the more common current alternative |
| ALSAPulseAudio.md | OS audio config **with one embedded GRC step** | **Yes** — set Audio Sink/Source `device` param | Partially achievable: setting the param to a known device string works via `change_graph`; discovering the device name (`aplay -L`, `pactl list`) needs a shell/filesystem tool that doesn't exist | Document: agent performs the final in-GRC step once the user supplies the device string from their own terminal | none |
| gnu_native_helpers_reference.md | Internals/API reference (filter design, constellation, FFT-type helpers + CLI reference) | **Yes** — filter/constellation/FFT-type snippets are directly usable param strings | Fully achievable for the Python-helper content (plain param strings, no execution needed); the CLI section (`grcc`, `gr_modtool`) not achievable — no shell tool | Keep this content indexed in the `docs` domain for filter/constellation/performance questions; no fix needed for the CLI section beyond noting the agent can describe but not run those commands | none — matches current pybind11-based `gnuradio.filter`/`digital`/`fft` APIs |
| AcademicPapers.md | Bibliography | No | N/A | none needed | none material |
| Archive_of_Hack_Fests.md | Events index (ends 2021) | No | N/A | none needed | Stale/dead, not incorrect |
| Wiki_account.md | External-site account request process | No | N/A — explicitly out of scope by design | none needed | none — recently edited (Mar 2026) |
| Chat.md | Community chat channels/etiquette | No | N/A | none needed | none — recently edited (Mar 2026) |
| What_Is_GNU_Radio.md | Conceptual DSP primer + block-category catalog | No | N/A — good `query_knowledge` fodder, produces no `change_graph` action itself | none needed | none |
| index.md (Main Page) | Navigation index, pure link list | No | Confirmed zero instructional content of its own | none needed | none |
| Main_Page.md | Same wiki page as `index.md` — confirmed via direct `diff` to be byte-for-byte identical except for MediaWiki URL query-string formatting (`?title=Main_Page` vs. `/Main_Page`), i.e. the same page scraped twice under two filenames | No | N/A — not a tool gap, a corpus-duplication issue: `query_knowledge(domain="docs")` indexes both as separate chunks, so a "what is GNU Radio" query gets two near-identical results occupying two of the requested `k` slots instead of one plus a genuinely different result | Delete one of the two duplicate files from `docs/wiki_gnuradio_org/` (keep either; content is identical) | none |
| Tutorials.md | Navigation index (master ToC) | No | Confirmed zero instructional content of its own (nothing hidden that the cluster file-lists would miss) | none needed | none |
| index.php_Guided_Tutorial_GRC.md | **Not actually meta** — a genuine beginner GRC tutorial misfiled by name | **Yes** | See the addendum under Cluster 1 above (moved there for proper classification) | — | Cites GRC "3.8.0+" prerequisite — still functionally accurate against 3.10.x |

---

## Verification performed during synthesis

- Directly grepped `src/` for `tutorial_manifest`/`tutorial_chunk` — zero
  hits, confirming finding #5.
- Directly read `ingest.py:180-256` — confirmed the unfiltered `*.md` glob
  and the `docs_chunks`/`docs_fts`/`docs_idx` schema has no provenance
  column today, confirming finding #1's root cause.
- Directly grepped all 97 files for `Provenance:` — found exactly 7 hits
  (`Variables_Block_Parameters.md`, `Hier_Blocks.md`, `Sample_Rate.md`,
  `grcc.md`, `Flowgraph.md`, `Tagged_Stream_Blocks.md`,
  `Embedded_Python_Block.md`), then read `pmt_architecture.md` and
  `constellation_modulation_upgrade.md` directly and confirmed both lack
  MediaWiki nav/footer despite no `Provenance:` tag, bringing the confirmed
  non-wiki-content count to 9.
- Directly grepped `tests/` for these filenames and found
  `tests/output/09_docs_stream_tags_concept_ollama.md` already contains a
  real, checked-in `query_knowledge` tool result mixing the synthetic
  `Flowgraph.md`/`Tagged_Stream_Blocks.md` content with genuine
  `Stream_Tags.md`/`BlocksCodingGuide.md`/`Python_Block_Tags.md` wiki text
  in a single undifferentiated result — confirming finding #1 is a live
  behavior, not a theoretical risk.
- Directly confirmed `BlockAdd`/`ParamUpdate`/`StateUpdate` in `agent.py`
  have no wildcard/pattern-match field and `BlockAdd` has no
  canvas-position field, confirming gaps #9 and #11 as described rather
  than assumed.
- Cross-checked every subagent's file coverage against the corpus directory
  listing (`ls docs/wiki_gnuradio_org/*.md`, 97 files). This caught one gap
  in the audit process itself: `Main_Page.md` was never assigned to any
  cluster (an oversight in the original file-clustering step, not a
  subagent error) — confirmed via direct `diff` to be a byte-identical
  duplicate of `index.md` and added as finding/gap #16 above.
