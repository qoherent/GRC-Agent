# Backlog: model-struggle patterns and known gaps

Living list of observed problems with the local model's tool-use behavior, plus
codebase issues found while investigating them. Each entry cites the concrete
evidence it's based on (scenario trace file, error message, or file:line) —
per AGENTS.md "Evidence before assertions," nothing here is a guess. Two trace
sets are referenced: `tests/output/agent_flow_e4b_baseline/` (pre-fix baseline)
and `tests/output/agent_flow/` (post-fix rerun, same 21 scenarios, same model —
`gemma4:e4b-it-qat-120k`). Note: `tests/output/` is gitignored scratch, cleared
between sessions — historical directory names cited below are point-in-time
references, not live paths.

## Fixed this session (prompt humanization + gemma4/laguna reliability)

Scenario prompts in `tests/agent_flow/run_agent_flow.py` were rewritten to
describe existing blocks by role/position ("the noise source", "the adder")
instead of quoting raw internal instance names — no real user talks about
`analog_sig_source_x_0`. This surfaced (and then fixed) several genuine,
general bugs, none of them scenario-specific hacks:

- **Catalog param values leaked their type-descriptor prefix.**
  `catalog/schema.py`'s `to_payload()` rendered non-enum params as
  `f"{dtype}={default}"` (e.g. `"raw=analog.GR_GAUSSIAN"`), and a model was
  observed copying the whole compact string verbatim as a parameter value,
  triggering a GRC syntax-error rejection it then failed to recover from.
  The enum shape (`"enum=[complex,float]=complex"`) never had this problem
  since it was already bracketed. Fixed by bracketing every type descriptor
  uniformly (`f"[{dtype}]={default}"`) — same shape the model already
  parses correctly, no per-dtype exception.
- **`param_filter.py`'s `keep_param()` silently contradicted its own
  documented spec.** The module docstring states Stage B keeps a param when
  `hide == 'none'`, but the code never implemented that branch — a
  `hide='none'` param whose value happened to equal its native default
  (e.g. `analog_sig_source_x`'s `freq` defaulting to `'1000'`) silently
  vanished from `inspect_graph`'s overview. Deterministic, not model-
  specific: reproduced 3/3 fails without the fix, 5/5 passes with it
  (`tests/test_grc_native_adapter.py::test_render_parameter_...`). Fixed
  with the missing `if hide == "none": return True` branch.
- **`inspect_graph`'s `block_not_found` error gave names but no way to map
  a role-based description to the right one.** A `targets` miss returned a
  bare `valid_block_names` list; a model asked to scope to "the noise
  source" had no way to tell which of several returned names that was
  without a second, wasted overview call — and was observed giving up and
  asking the user instead of retrying. Fixed: the error now pairs each name
  with its `block_id` (`valid_blocks: [{instance_name, block_id}, ...]`).
  Verified live: every retry after this error succeeded immediately
  (though a separate run showed the data being correct doesn't guarantee
  the model acts on it — see the `16_expand_adder_input` note below).
- **A missing-port error never named the param that controls port count.**
  `blocks_add_xx`-style blocks expand their port count via a param
  (`num_inputs`) that has no fixed conventional name across blocks
  (`pad_source` uses `num_streams`) — a model connecting to a not-yet-
  existing port got only "port '3' not on block", with no signal that a
  param needed to change at all first. Root-caused via a 135-turn,
  57-`change_graph`-call outlier run of `21_type_conversion_and_conjugate`
  and confirmed independently in `20_multi_change_challenge` (which never
  once attempted `num_inputs` across 5 retries, despite the *isolated*
  version of that exact sub-task passing 4/5 times). Fixed with a new
  native-derived `port_count_controlling_params()` (mirrors
  `type_controlling_params` exactly, from each port's raw `_multiplicity`
  template) wired into `grc_native_adapter.py`'s `_find_port` error.
- **`change_graph` batches with every operation array empty/absent
  trivially returned `ok=true`.** The tool schema already documented "at
  least one array must be provided," but the dispatcher never enforced it.
  In the 135-turn outlier run above, ~50% of all `change_graph` calls were
  exactly this — a content-free stalling pattern. Fixed: rejected with
  `error_type: invalid_request`.
- **The stuck-loop detector only caught byte-identical repeated failures.**
  A model varying its arguments each time (a different instance name, a
  differently-malformed string) while repeating the same underlying
  mistake never tripped it, letting the outlier run above balloon to 135
  turns before the model eventually gave up on its own. Fixed: a second,
  more lenient detector layered alongside the existing one, keyed on
  `(tool_name, error_type)` with a higher threshold (6 vs. 3) to avoid
  false-positives on a few coincidentally-same-category mistakes. Verified
  live: the same scenario that took 135 turns pre-fix stopped at 13 turns
  post-fix, for the same underlying reason (6 `gnu_validation_failed`
  errors in a row).
- **A malformed tool call could corrupt the tool name itself.** Argument
  fragments occasionally leaked into the tool-name field (a `ToolAgents`
  provider-side parsing corruption, e.g. `"change_graph\n<arg_key>..."`),
  landing on the generic "tool not available" rejection instead of a
  "malformed call" diagnosis. Fixed: `_dispatch_tools` now detects this
  structurally (name starts with a real tool name but isn't equal to it —
  never a hardcoded corruption string) and names the real tool.
- **Narration without execution.** `21_type_conversion_and_conjugate` was
  observed correctly diagnosing a fix in prose and then ending the turn
  without ever issuing the `change_graph` call. Added one declarative
  system-prompt fact: describing a call in reply text does not execute it.
  Net effect on this specific pattern is unconfirmed (the underlying
  wiring-thrash capability limit dominates that scenario regardless).
- **A whole `max_tokens` generation cap, and a whole `max_tool_rounds`
  round-count cap, were both dead-or-harmful config surface.** The former
  was proven by direct replay to truncate a model mid-`<think>` before it
  could emit a tool call (`finish_reason: "length"`, 0 tool calls) — an
  artificial harness/production mismatch (the test harness hardcoded 2048
  while production already used a larger value). The latter was computed
  every turn (`toolagents_runtime.py`) but never consumed to bound
  anything — the turn loop is bounded only by the stuck-loop detectors
  above, per its own code comment; a `test_new_five_fixes.py` docstring
  claimed a "safety ceiling for max_tool_rounds" that was never actually
  implemented as a test. Both deleted completely — no field, no config
  key, no code path — rather than left as unused surface.

Net result on the 21-scenario suite: gemma4 14/21 → 17/21 in one clean run;
laguna 19-20/21 across repeated runs (was already strong, unaffected
negatively). The two hardest scenarios (`06`, `21`) still fail intermittently
on genuine model reasoning limits (hallucinated success, wiring thrash) —
tracked below as capability ceilings, not re-attempted with speculative
prompt tweaks per AGENTS.md's "no ad-hoc heuristics" rule.

## Fixed this pass (harness/model context-window investigation)

- **A misleading "the model failed" message for what is actually a context/
  output-limit truncation.** Tested a second, larger model
  (`laguna-xs-2.1:q4_K_M`) against the 3 scenarios still failing on gemma4 —
  it failed all 3 too, but with a different, more diagnosable signature: all
  3 traces end in `"finish_reason": "length"` (explicit truncation, not a
  natural stop), and one (`20_multi_change_challenge`) is cut off mid-word:
  `"I can see your dial tone graph has two sine waves (350 Hz and 440 Hz)
  being"`. Root cause, confirmed via `ollama ps`: laguna's Modelfile has no
  `PARAMETER num_ctx`, so it loads with Ollama's bare default — **4096
  tokens**, vs. `gemma4:e4b-it-qat-120k`'s custom-baked **120,000** (that
  model's Modelfile explicitly sets `PARAMETER num_ctx 120000`; nothing
  equivalent exists for a freshly-pulled model). Current Ollama docs
  (`docs.ollama.com`, checked live — Context7's indexed snapshot was stale
  on this point) confirm the default is VRAM-tiered (4k under 24GiB, 32k
  24-48GiB, 256k 48GiB+) and explicitly recommend **≥64000 tokens for agent
  workloads** — this app's exact use case.
  Confirmed empirically (not just from docs) that there is no per-request
  fix available on the endpoint this app actually calls: sent
  `/v1/chat/completions` requests with both a nested `options.num_ctx` and a
  top-level `num_ctx` field — `ollama ps` showed the loaded context
  unchanged at 4096 both times. Ollama's native `/api/chat`/`/api/generate`
  *do* accept per-request `options.num_ctx`, but this app deliberately uses
  the OpenAI-compatible `/v1` endpoint as one shared code path for both the
  Ollama and OpenRouter backends (`AGENTS.md`) — switching wire formats
  just for this is a real architecture change, not a quick fix, and out of
  scope here. The only server-wide lever (`OLLAMA_CONTEXT_LENGTH` env var
  at Ollama startup) requires restarting the Ollama service, which is
  daemon management — outside what this app does or should do.
  What *is* fixable in-app: `toolagents_runtime.py` already captures
  `finish_reason` per turn (earlier session's work) but discarded the
  signal when synthesizing the user-facing text for a fully-empty terminal
  response, always showing the generic `"No response was generated by the
  model."` — which, per AGENTS.md's "No Assumed Reasoning Failures," reads
  as blaming the model for what the wire response itself already proves is
  a length/context cutoff. Fixed: when `finish_reason == "length"`, the
  synthesized text now says so explicitly and points at the real,
  actionable check (`ollama ps`), for the benefit of any future user
  running any model without a custom-sized Modelfile — not just laguna.
  Regression test: `test_empty_terminal_with_finish_reason_length_names_the_real_cause`
  (`tests/test_toolagents_runtime.py`).
- **Operator-facing: no guidance anywhere for actually launching/sizing
  Ollama, only for the model name.** README and the GUI both assumed Ollama
  was already running correctly. Added a single shared hint
  (`OLLAMA_SERVER_HINT` in `toolagents_runtime.py`, reused by `startup.py`'s
  bootstrap probe failure message, the mid-session `backend_unreachable`
  payload, and the model toolbar's provider-selector tooltip) that names the
  concrete, known-good number — `OLLAMA_CONTEXT_LENGTH=120000` — matching
  this app's own default model's baked-in context, rather than a vague "set
  it higher". Platform-agnostic (no `systemctl`/`journalctl`), consistent
  with the existing backend-unreachable banner contract
  (`tests/test_gui_launch.py::test_banner_message_includes_platform_agnostic_hint`).
  Never shown for the OpenRouter backend (no local server to start).
  README's "LLM backend" and "Choosing a model" sections now explain
  `ollama serve` / `OLLAMA_CONTEXT_LENGTH` directly instead of only
  mentioning custom Modelfiles. New tests:
  `test_openrouter_backend_unreachable_has_no_ollama_hint`
  (`tests/test_toolagents_runtime.py`),
  `test_bootstrap_runtime_openrouter_connection_refused_has_no_ollama_hint`
  (`tests/test_startup.py`), `tests/test_model_toolbar.py` (new file, 3
  tests: tooltip present for Ollama, absent for OpenRouter, updates live on
  provider switch).
- **Correction to the above: the first version of this hint was itself an
  anti-pattern.** It told users to "make sure Ollama is running (`ollama
  serve`...)" and to set `OLLAMA_CONTEXT_LENGTH` "before starting Ollama" —
  i.e., implying a manual `ollama serve` invocation. Caught by direct,
  reproduced evidence: a standard Ollama Linux install already runs it as a
  systemd background service (confirmed against Ollama's own official
  `docs/linux.mdx`), so a manually-started second instance either (a) fails
  outright with "address already in use" (proven — this is exactly what
  happened when testing the original hint's own advice), or (b), if the
  conflicting instance is stopped first, starts using a *different* default
  model directory (`~/.ollama/models` for the invoking user vs.
  `/usr/share/ollama/.ollama/models` for the systemd `ollama` user) and
  can't see any previously-pulled models — also proven directly (`ollama
  ps`/`ollama list` came back empty after this exact sequence). `OLLAMA_
  CONTEXT_LENGTH` is documented as a server-startup variable only; `ollama
  run`/`ollama serve` never read it as a way to reconfigure an
  already-running instance, and `ollama serve` doesn't take a model-name
  argument at all (confirmed directly: `ollama serve <model>` →
  `Error: accepts 0 arg(s), received 1`). Fixed: `OLLAMA_SERVER_HINT` no
  longer suggests running any command — it states the number
  (`OLLAMA_CONTEXT_LENGTH=120000`) and points at the README's "LLM backend"
  section, which now gives the actual correct, official, persistent
  per-OS steps (`systemctl edit ollama` / `launchctl setenv` / Windows
  environment variables — all three are Ollama's own documented
  configuration method, not a workaround). Regression tests updated to
  assert the *absence* of a bare `ollama serve` command recommendation
  (word-boundary matched, since "Ollama server" the noun is fine — only
  "ollama serve" the command is not) across all three surfaces.
- **Root-cause confirmed end-to-end**: applied the real fix (persistent
  `OLLAMA_CONTEXT_LENGTH=120000` via a systemd override — after one false
  start where the override file's content was accidentally the command
  itself, `sudo systemctl edit ollama`, instead of the `[Service]` block;
  `ollama ps` caught it immediately, still showing 4096) and reran the same
  3 scenarios against `laguna-xs-2.1:q4_K_M`, same fixture, same prompts.
  Result: **2/3 pass, 0/3 truncated** (was 0/3 pass, 3/3 truncated). All 3
  traces now end `"finish_reason": "stop"` — the truncation signature is
  completely gone. `06_query_knowledge_multiply` and
  `21_type_conversion_and_conjugate` now pass outright.
  `20_multi_change_challenge` (the longest, most complex prompt in the
  suite — 11 distinct instructions) still fails, but on a genuine reasoning
  slip, not infrastructure: asked to "disable" `analog_noise_source_x_0`,
  the model called `remove_blocks` on it instead (confirmed via our own
  validation hint: `"'blocks_add_xx' input port is unconnected because it
  was fed by removed block 'analog_noise_source_x_0'"`), then reported in
  its own summary that it had "removed" the block — a clean instance of the
  already-tracked "disable/bypass conflation" pattern (item 1 above), not a
  new failure mode. Confirms the context-window diagnosis was the actual,
  complete root cause for this class of failure, and — separately — ruled
  out one more red herring while investigating: `nvidia-smi` showed laguna
  (20GB model) using only 6.6GB of an 8GB GPU with the rest on CPU
  (91%/9% CPU/GPU split per `ollama ps`); confirmed via process inspection
  this is just the model not fitting in VRAM, not a competing process — the
  only other GPU consumers were the desktop compositor, an unrelated
  system app, and Ollama's own small embedding-model process.
- **Root-caused the disable/remove conflation precisely, via a direct
  cross-model comparison, then fixed the fixable part of it.** Ran the same
  scenario 20 against OpenRouter's `deepseek/deepseek-v4-flash` (full
  context, stronger model) — it passed cleanly in 5 turns, 1 `change_graph`
  call. It used `update_states` with `state: "disabled"` correctly and
  batched it atomically with `remove_connections` in the same call,
  sidestepping the exact "Port is not connected" trap the system prompt
  warns about. Comparing traces line-by-line: the system prompt told the
  model *when* to disable something but never named the *mechanism*
  (`update_states`) — unlike how it already explicitly names `block_id:
  "variable"` for adding a variable. `deepseek` inferred the field from
  general tool-calling competence (its own research calls never mention
  `update_states` either); `laguna-xs-2.1:q4_K_M`, across two separate
  attempts, never tried it once, defaulting to `remove_blocks` both times.
  Fixed: added one line to `build_system_prompt()`
  (`runtime/model_context.py`) naming `update_states`/`{instance_name,
  state}`/the three valid `state` values explicitly, mirroring the existing
  `block_id: "variable"` pattern. Bumped `__version__` and regenerated
  `docs/MODEL_CONTEXT_BIBLE.md`.
  Reran laguna on all 3 scenarios: **confirmed improvement** — the model now
  reliably discovers and uses `update_states` (it never did before), and
  06/21 still pass (no regression). But scenario 20 still fails, now for a
  narrower, different reason: `state analog_noise_source_x_0='bypass'
  expected 'disabled'`. Deep-dive on the new trace: the model tried
  `disabled` first, got rejected for reasons unrelated to that block (other
  blocks' unwired connections), switched to `bypass`, got the identical
  unrelated rejection, then succeeded once the unrelated issues were
  cleared — by which point its own connection removal was 5 calls in the
  past. Its own final summary says *"using bypass instead of disabled since
  it was connected previously"* — it reasoned from the block's state in the
  *original* flowgraph, not its *live* state after its own edits. This is a
  live-state-tracking limitation, not a prompt-wording gap — no rewording
  fixes a model not re-checking graph state after its own prior edits, and
  `deepseek` doesn't have this problem at all. **Decision: keep the
  `update_states` prompt fix (confirmed real, side-effect-free improvement
  across all 3 gates + no regression), do not chase the disabled-vs-bypass
  edge case further** — doing so risks exactly the "ad-hoc rule targeting
  one scenario" AGENTS.md prohibits, for a gap that's a genuine small-model
  capability ceiling, not a design flaw. The same laguna rerun also
  surfaced two unrelated, pre-existing competence issues (adding blocks
  without wiring them in the same batch, repeated twice with no learning
  between attempts; one garbled instance-name typo) — not caused by this
  prompt change, already adjacent to already-tracked patterns above.

## Fixed this pass (confirmed via live-model rerun)

- **`inspect_graph` returned no port information.** `GrcBlock` had no
  `ports`/`inputs`/`outputs` field; the model could only discover an
  unconnected port reactively, via a native-validation error string. Fixed:
  `GrcBlock.inputs`/`.outputs` (native `GrcPort`), one Stage A/B pipeline —
  Stage A drops hidden ports (native `active_sinks`/`active_sources`), Stage
  B drops a port only if both optional and unconnected (native `Port.
  connections(enabled=True)`). Confirmed live in the rerun: both
  `21_type_conversion_and_conjugate.md` and `13_docs_informed_param_edit.md`
  now show real `{"connected": ..., "dtype": ..., "port_id": ...}` entries in
  every `inspect_graph` result the model received.
- **Overloaded naming across the tool surface.** `"id"` meant three
  different things depending on context; `dtype` was reused for a param's
  widget-kind vs. a port's IO type. Fixed: `NormalizedPort.id` →
  `port_id` (matches the new live-graph `GrcPort.port_id`); internal
  `block_type`/`key` parameters standardized to `block_id` at wrapper
  boundaries; matching schema prose for `add_blocks.params` /
  `update_params.params`.
- **`type`-controlling params weren't reliably auto-resolved.**
  `_phase_auto_resolve_types` only fired for `add_blocks`, only inferred
  from same-batch connections, and hardcoded the literal string `"type"` —
  missing `itype`/`otype`-style blocks (e.g. `fec_generic_encoder`) and any
  retyping via `update_params`. Fixed: type-controlling params are now
  derived mechanically from each port's raw dtype template
  (`type_controlling_params`, `ports_governed_by`), and `"auto"` is a real,
  documented sentinel value in both `add_blocks` (silent fallback to GRC's
  own default if unresolvable) and `update_params` (explicit
  `type_auto_unresolvable` error if unresolvable — no silent guessing).
  Confirmed live: `21_type_conversion_and_conjugate.md` — previously failed
  by retrying invalid `"auto"`/`"auto=0"` tokens 3 times across 11 calls;
  in the rerun the same scenario passes cleanly (11 calls, no invalid
  sentinel, `finish_reason: "stop"` on a genuine completion). The catalog
  also now suggests a real, usable default (`"enum=[...]=auto"` instead of
  a blank `"enum=[...]="`) — visible in the rerun's `query_knowledge` result
  for `analog_const_source_x`.
- **`inspect_graph` silently dropped an always-shown param whenever its new
  value coincidentally matched the block's native default.** Found live,
  not assumed: the scenario 13 prompt sets `analog_sig_source_x_0.freq` to
  `1000` — which is that param's own GRC-declared schema default. `freq`
  has `hide == 'none'` (GRC's own "always show this" marker), but
  `param_filter.py`'s `keep_param` only special-cased `hide == 'part'`;
  every other `hide` value fell through to the generic `value != default`
  check, so a `hide == 'none'` param sitting at its own default vanished
  from the render. This reproduced **identically in both trace sets** —
  baseline: `agent_flow_e4b_baseline/13_docs_informed_param_edit.md` (call 2
  sets `"freq": 1000`, the following `inspect_graph` omits `freq` entirely,
  and the model's final text — *"the resulting configuration did not show
  the change in an easily verifiable way"* — was an accurate bug report,
  not hedging); rerun: `agent_flow/13_docs_informed_param_edit.md`, same
  omission. Fixed: added an explicit `if hide == "none": return True` in
  `keep_param`, matching the rule the module's own docstring already
  claimed. Regression test:
  `test_render_parameter_keeps_hide_none_param_that_matches_native_default`
  (`tests/test_grc_native_adapter.py`). This also retroactively resolves
  what a previous backlog pass had filed as "verification hedging instead
  of using the available scoping tool" — the model wasn't hedging, it was
  reporting a real defect in the tool it was told to trust.

## Not yet addressed

1. **Disable/bypass conflation.** Asked to disable a connected block, the
   model went straight to `state: bypass` + `force=true`, skipping the
   validation round-trip the prompt described, then called the two states
   interchangeable in its own summary ("successfully disabled (set to state
   'bypass')"). Evidence: `07_force_disabled_connected_block.md`. Reconfirmed
   in the rerun under a different scenario: `20_multi_change_challenge.md`
   left `analog_noise_source_x_0` in state `'bypass'` when `'disabled'` was
   requested.
2. **Fixes the loud (syntax) error, ignores the quiet (semantic) one already
   shown in the same error list.** A `change_graph` call failed with 4
   simultaneous errors, including an orphaned-source-block warning explicitly
   covered by a system-prompt rule ("When removing blocks, also remove or
   disable any source blocks that become unconnected"). The next call only
   fixed the connection-string typo and resubmitted without addressing the
   orphan, which failed again for the identical reason. Evidence:
   `06_query_knowledge_multiply.md`, calls 3-4.
3. **Connection-string malformation.** Two independent slips in arrow
   notation: a hyphen instead of `->` (`"a:0-b:1"`), and a missing
   destination port index. Pure string-formatting fragility under multi-edit
   batches, not a domain-knowledge gap. Evidence:
   `06_query_knowledge_multiply.md` call 3, `21_type_conversion_and_conjugate.md`
   call 5 (baseline trace — the rerun's scenario 21 did not repeat this
   particular slip, consistent with local-model non-determinism; the
   documented failure mode can still recur).
4. **Hallucinate-then-verify inversion.** Despite explicit system-prompt
   rules ("Never use hallucinated block IDs", "Connections use numeric port
   keys"), the model's first `change_graph` attempt used a nonexistent block
   ID and a letter port (`'c'` instead of `'0'`) — guessing before querying
   the catalog, verifying only after both failed. Evidence:
   `21_type_conversion_and_conjugate.md` call 3 → error, call 4 (first real
   `query_knowledge` call). Not reproduced in the rerun (that run of 21
   passed cleanly) — single non-deterministic runs don't confirm a fix here,
   only that it doesn't always trigger.
5. **Ignoring repeated, explicit corrective error messages across retries.**
   Retried the same invalid `"auto"`/`"auto=0"` token 3 times across 11 tool
   calls despite the tool spelling out the valid enum options twice,
   verbatim, and also regressed on a param name it had previously gotten
   right (`const` → `constant`). Evidence:
   `21_type_conversion_and_conjugate.md` calls 5, 10, 11 (baseline). The same
   *shape* of failure reappeared in the rerun under a different scenario:
   `03_disable_and_enable.md` calls 3-5 sent the identical
   `remove_connections` + `update_states` batch three times in a row against
   the identical `"blocks_add_xx: Sink - in2(2): Port is not connected."`
   error, until the harness's own repeated-identical-call safety ceiling
   stopped it (`error_type: "safety_ceiling_reached"`) — the model never
   varied its approach (e.g. reducing `num_inputs`, or targeting the actual
   orphaned port) across three identical attempts.
6. **Total empty-response abandonment on long, multi-part prompts.** An
   11-part instruction produced exactly one `inspect_graph` call, then
   complete silence (`assistant_text: "No response was generated by the
   model."`, `finish_reason: "stop"` — confirmed NOT a truncation, see the
   Ollama bug below). Possibly the same token-leak family, amplified by
   prompt length, but that link is speculative and unverified. Evidence:
   `agent_flow_e4b_baseline/20_multi_change_challenge.md`. Not reproduced in
   the rerun (that run of scenario 20 failed instead on disable/bypass
   conflation, item 1 above) — consistent with non-determinism, not a fix.
7. **Narrates the correct next step, then stops without calling the tool —
   on the very first turn, skipping an explicit instruction.** Prompted with
   "Inspect the flowgraph" as the first sentence, the model skipped
   `inspect_graph` entirely and went straight to a `change_graph` guess
   (connecting to adder input port `'3'` without first bumping `num_inputs`
   from 3 to 4). After the resulting error, its final text said *"I am
   inspecting the `blocks_add_xx` mixer now"* — but the run ended
   (`finish_reason: "stop"`, `steps: 2`) without that call ever being made.
   Distinct from the Ollama parser-bug family below (no leaked token, no
   second JSON block in the text) and distinct from item 6 (there was
   substantive, correct-sounding text, not silence) — looks like a genuine
   instruction-following lapse compounded by the harness ending the turn as
   soon as the model stops requesting tools, with no forcing function to
   make it follow through on text it just committed to. Evidence:
   `16_expand_adder_input.md` (rerun).

## External, tracked, not actionable in this codebase

- **Ollama's `gemma4` parser drops/leaks tool calls on some turns.** The
  model narrates the correct plan in text (sometimes including a second,
  corrected JSON block) but the turn ends with `finish_reason: "stop"` and no
  tool call attached — proven NOT a `max_tokens` truncation issue (captured
  `finish_reason` is `"stop"` in every observed case). Root-caused to a
  known, currently open upstream bug: the Modelfile shows `RENDERER gemma4`
  / `PARSER gemma4` with `TEMPLATE {{ .Prompt }}` (a passthrough) — Ollama's
  own closed-source Go parser sits between the model's raw output and this
  app, and nothing in this codebase can intercept it. See
  [ollama/ollama#15943](https://github.com/ollama/ollama/issues/15943) and
  related: [#15539](https://github.com/ollama/ollama/issues/15539),
  [#15798](https://github.com/ollama/ollama/issues/15798),
  [#15315](https://github.com/ollama/ollama/issues/15315). Checked whether
  our own request settings match a known trigger (#15539 cites `think:false`
  + tools) — we send `think: true` (`toolagents_runtime.py`), so that
  specific trigger doesn't apply; our pattern matches #15943 instead, which
  isn't gated on that setting. Revisit when Ollama ships a fix.

  Second rerun (post AGENTS.md-compliance fixes), `tests/output/agent_flow/`:
  **18/21 passed** (up from 16/21) — 0 safety-ceiling hits, and scenarios 03,
  13, and 16 (all previously-observed failures) now pass cleanly. All 3
  remaining failures trace directly to this same bug, not to a regression:
  `06_query_knowledge_multiply.md` and `20_multi_change_challenge.md` both
  end in `"assistant_text": "No response was generated by the model.",
  "finish_reason": "stop", "error_type": "empty_model_response"` after a
  first `change_graph` attempt failed; `21_type_conversion_and_conjugate.md`
  shows the model narrate a full, correct plan in text and then emit the
  literal leaked token `<channel|>` as the very last character with no tool
  call attached — the exact #15943 signature.

## AGENTS.md compliance findings (fixed)

All five findings from the fresh audit are resolved:

- **Dynamic `gnuradio` import outside the adapter boundary.**
  `catalog/schema.py`'s `_resolves_to_hierarchical_class` did its own
  `importlib.import_module` on GNU Radio submodules to MRO-check for
  `hier_block2`. Moved the whole function (as
  `resolves_to_hierarchical_class`) into `grc_native_adapter.py` — the one
  module permitted to import `gnuradio` — and `catalog/schema.py` now calls
  it via a lazy import, matching the pattern `param_filter.py` already uses
  for its own native introspection helpers.
- **Dead "legacy" `CATALOG_DB_PATH` shim.** Confirmed zero production
  consumers (only its own regression test). While fixing it, found an
  identical, previously-unflagged twin: `doc_answer.py`'s `DB_PATH`, same
  dead-shim pattern, same comment text. Deleted both; the two live-test
  consumers now call `catalog_db_path("ollama")`/`docs_db_path("ollama")`
  directly, and the test that existed solely to assert the shims equaled
  their real counterparts is gone (nothing left to assert).
- **Dead `summarize_graph`/`summary_payload`.** Confirmed only their own
  dedicated test file exercised them — deleted both, plus
  `SUMMARY_PREVIEW_LIMIT`/`DEFAULT_SUMMARY_BLOCK_LIMIT`, plus
  `tests/session/test_summarize_graph.py` outright. Pulling the thread
  further: `FlowgraphSession.last_validation_ok`/`.last_validation_revision`
  were written by `validate()`/`validation_state()` but — once
  `summary_payload` was gone — read by nothing; removed those two
  attributes and the test helper (`_mark_session_valid`) that existed only
  to set them.
- **Raw character slice on a health-probe error payload.** `startup.py`'s
  backend-unreachable message sliced `response.text[:200]` with no
  indication anything was cut. Added an explicit `… (N chars total)` marker
  when truncated — still GUI-only, never model-facing, but no longer silent.
- **Undocumented `"options"` block-key check.** Confirmed GRC's own core
  (`FlowGraph.py`, `blocks/block.py`, both top-block generators,
  `flow_graph_complexity.py`) does the identical literal `key == 'options'`
  check throughout its own internals — there is no native "is this the
  options block" flag distinct from the reserved id. Added a comment citing
  this, matching the documented-exception style already used in
  `param_filter.py` for `showports`/`bus_structure_*`.
