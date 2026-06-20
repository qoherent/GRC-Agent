# Plan — gemma4:e4b-it-qat on R-suite

Date: 2026-06-15
Inputs read: AGENTS.md, CHANGELOG.md [Unreleased],
  6 critique reports in R_test_results/critiques/,
  test files in tests/llama_eval/ and tests/eval_chat/,
  cited source files in src/grc_agent/ (agent.py, runtime/tool_schemas.py,
  runtime_tool_validation.py, tests/llama_eval/_recovery.py).
Subagents spawned: 3 read-only `explore` agents (R0/R1 harness semantics,
  DSP/scenario11/12 fixture review, eval_chat signal-validity review).

> **Headline.** One backend bug dominates every mutation suite: the
> `change_graph` schema **requires** a `reasoning` field
> (`tool_schemas.py:507`, `strict=True`) that the handler does not accept
> (`agent.py:1598-1608`), so every schema-compliant mutation call raises
> `TypeError` and is caught as `internal_error` (`agent.py:470-477`). The
> model only succeeds when it *disobeys* the schema by omitting `reasoning`.
> Four of six critiques misdiagnose this as "model hallucination"; only
> `scenario12_fft_critique.md` correctly identifies it as a code bug. Fix
> 1.1 first; until then no mutation-suite result is a valid capability
> signal.

---

## Test Design Findings (read this first)

These describe the tests, not the model. The tiers below are meaningless
if the signals that produced them are unsound.

### T.1 Mutation-suite pass rates are not valid capability signals yet
- **Location**: `src/grc_agent/runtime/tool_schemas.py:507`; `src/grc_agent/agent.py:462`, `1598-1608`
- **What is wrong**: The schema forces the model to emit `reasoning`; the
  handler rejects it. A mutation scenario therefore "passes" only when the
  model happens to omit the required field — i.e. it measures luck, not
  mutation skill.
- **Why it matters**: Every R1/R2/DSP/scenario11/scenario12 mutation result
  is confounded by this bug. The R1 "87.5%", R2 "100%", and DSP "10%"
  numbers do not describe graph-editing ability.
- **Suggested change to the test**: none to the test itself — apply fix
  1.1, then re-run. Do not quote these pass rates as baseline capability
  until re-run.

### T.2 `inline_swap` DSP category is unsolvable by design (9/9 rigged)
- **Location**: `tests/llama_eval/dsp_scenarios.py:289-293` (prompt); docstring `:280-282`
- **What is wrong**: The prompt literally instructs "Replace the … block
  with a **blocks_float_to_float** block". No such block exists in the GNU
  Radio catalog (the float→float identity block is `blocks_copy`); the
  author's own docstring calls it an "identity" block.
- **Why it matters**: PASS requires `mutation` + `saved_path_valid` (real
  grcc gate, `harness.py:1359-1377`), so a non-existent block id can never
  validate. The 9/9 failure measures the fixture, not the model
  (`dsp_gauntlet_critique.md` §Inline Block Swap).
- **Suggested change to the test**: replace the rigged id with a real one
  (`blocks_copy`) or rephrase the prompt to ask for a float identity/pass
  block without naming a non-existent id.

### T.3 `typo_agc` DSP category is underspecified and mislabeled (9/9)
- **Location**: `tests/llama_eval/dsp_scenarios.py:393` (prompt), `:400` (`release_profile`), `:406-410` (checks)
- **What is wrong**: The prompt is the bare string "Add an AGC block to the
  flowgraph." — no wiring, no `force` hint — yet the profile is
  `R1_SET_PARAM_ONLY` (a param task) for what is a topology *add*, and the
  checks require a cleanly grcc-validating saved graph.
- **Why it matters**: Adding `analog_agc_xx` leaves a dangling source port
  → `gnu_validation_failed`; the saved graph cannot validate without
  connections the prompt never mentions (`dsp_gauntlet_critique.md` §Typo).
- **Suggested change to the test**: either give wiring context in the
  prompt, change the expected end-state to tolerate a draft/forced add, or
  re-scope; correct the profile to `R3_REWIRE`.

### T.4 Two `docs` R0 scenarios pass with zero tools and a generic answer
- **Location**: `tests/llama_eval/r0_release.py:178`, `:192` (`allow_safe_text_only=True`); `tests/llama_eval/harness.py:1236-1241`
- **What is wrong**: `allow_safe_text_only=True` forces
  `routing_pass`/`argument_pass`/`tool_success_pass` to `True` when no tool
  is called. Combined with `READ_ONLY_CHECKS()` (`no_mutation`), both
  `pmt_dict_immutability` and `binary_short_scaling` PASS end-to-end even
  when the model ignores `query_knowledge` and answers with generic Python
  (`r0_release_critique.md` scenarios 9 & 10).
- **Why it matters**: The generic `routing_pass` is *not* vacuous
  (`harness.py:2559-2575`); the gap is these two opt-in scenarios. If they
  are meant to test GR-doc tool use, the signal is false.
- **Suggested change to the test**: drop `allow_safe_text_only` so the
  expected `query_knowledge` call is enforced, or add a semantic check
  requiring GNU-Radio-specific identifiers in the answer.

### T.5 `tests/eval_chat/` is a runtime-loop regression suite, not a model eval
- **Location**: `tests/eval_chat/harness.py:122-156` (mocked `chat_agent.step`); `tests/eval_chat/test_fixtures.py:21-34`
- **What is wrong**: `step` is a `MagicMock` that pops scripted
  `model_responses` from a fixture; the prompt is never seen by any model.
  `test_fixtures.py` asserts the runtime `expect` contract (dedup,
  ceiling, surface gating), **not** schema and **not** model behavior.
- **Why it matters**: CHANGELOG `[Unreleased]` frames it as an "eval
  harness"; citing its pass/fail as agent-capability evidence would violate
  the Anti-Symptom Rule. It is legitimate as a `_run_turn_events`
  regression suite.
- **Suggested change to the test**: relabel in docs/CHANGELOG as a runtime
  regression suite; no code change required.

---

## Tier 1 — Apply now (code/runtime)

### 1.1 Make `_change_graph()` accept the schema-required `reasoning` kwarg
- **Source**: `scenario12_fft_critique.md` §Root Cause (PRIMARY); corroborated by R1 §1, R2 §1, DSP §1, scenario11 §Tool 4
- **Location**: `src/grc_agent/agent.py:1598-1608` (handler signature); schema at `src/grc_agent/runtime/tool_schemas.py:386-388` & `:507`
- **Problem**: Schema declares `reasoning` required; the handler omits it,
  so `func(**kwargs)` at `agent.py:462` raises `TypeError` on every
  schema-compliant mutation. The validator's `reasoning=""` injection
  (`runtime_tool_validation.py:64-65`) only patches the *validation copy*
  (`agent.py:726-729`), not the dispatched `kwargs`.
- **Proposed fix**: Add `reasoning: str | None = None` to the
  `_change_graph()` signature (accept-and-ignore, or log at debug). This is
  the sole schema property the handler lacks; `debug` is already stripped
  for the MVP surface.
- **Why this is allowed**: This is a handler implementation fix that makes
  execution honor what the schema already declares — it does **not** change
  the model-facing schema, add a tool, or touch the system prompt (AGENTS.md
  "Tool schemas describe capability"; §3.5 not triggered).
- **Estimated effort**: S
- **Tests to re-run**: `tests/eval_chat/`, the live R1/R2/DSP/scenario11/12
  suites, and add a unit test asserting `change_graph` tolerates a
  `reasoning` kwarg.

---

## Tier 2 — Plan before applying (multi-file or harness)

### 2.1 Filter dispatch kwargs to the handler's accepted signature (hardening)
- **Source**: `scenario12_fft_critique.md` §Proposed Improvements #2
- **Location**: `src/grc_agent/agent.py:462` (`func(**kwargs)`)
- **Problem**: The dispatch splats every validated kwarg into the handler,
  so any future schema/handler drift reproduces the 1.1 crash class.
- **Proposed fix**: Before splatting, restrict `kwargs` to parameters the
  bound handler actually accepts (inspect signature or per-tool allowlist),
  so undeclared-but-schema-valid fields cannot reach a handler that rejects
  them.
- **Why this is allowed**: Runtime-internal change; no model-facing surface,
  prompt, or schema change. It complements (does not replace) 1.1.
- **Estimated effort**: M
- **Tests to re-run**: full `tests/eval_chat/` (covers dedup/ceiling/gating
  paths through `execute_tool`) plus a per-tool dispatch contract test.
- **Risk**: Touches the execution path of *all* tools; must not silently
  drop a field a handler genuinely needs. Requires an explicit allowlist,
  not a blanket `**kwargs` swallow.

---

## Decision Required (do not apply without authorization)

### D.1 Should `reasoning` remain a required model-facing field on `change_graph`?
- **Source**: R1 §1, R2 §1, DSP §1, scenario12 §Root Cause
- **Location**: `src/grc_agent/runtime/tool_schemas.py:386-388`, `:507`; band-aid at `src/grc_agent/runtime_tool_validation.py:64-65`; mirrored in `docs/MODEL_CONTEXT_BIBLE.md:106-108,272`
- **Why authorization is needed**: Making `reasoning` optional or removing
  it is a change to a model-facing tool schema (AGENTS.md "Active MVP
  Wrappers"; task §3.5). Fix 1.1 makes the current design *work*; this
  decision is about whether the field should exist at all. If removed,
  `tool_schemas.py`, the `runtime_tool_validation.py` injection, and the
  bible must all be updated together (no shims — AGENTS.md "No Backward
  Compatibility").

### D.2 Add a variable-reference cross-index to `inspect_graph` output
- **Source**: `r0_release_critique.md` Pattern 2 / scenario 4
- **Location**: `src/grc_agent/runtime/tool_schemas.py:336-360` (inspect_graph schema/output)
- **Why authorization is needed**: New output field on a model-facing tool
  (§3.5). Evidence: model stated `samp_rate` is unused, missing the
  `samples_per_second`/`srate` param references that live in block params,
  not in `connections[]`.

### D.3 Improve `query_knowledge` retrieval / suggestion behaviour
- **Source**: `r0_release_critique.md` scenario 7 & Proposed #6; `scenario12_fft_critique.md` §TERTIARY & #3
- **Location**: `query_knowledge` schema/implementation (`tool_schemas.py:361-381`) and the catalog/docs search backend
- **Why authorization is needed**: Schema/search-backend change (§3.5/§3.6).
  Evidence: "time sink block" and "Stream to Vector block id" returned
  irrelevant lexical matches; trailing noise tokens crowded out real terms.

### D.4 Strategy for orphan-prone block adds (auto-`force` / two-phase draft)
- **Source**: `dsp_gauntlet_critique.md` Critical #2; scenario11/12 missing-connection failures
- **Location**: `change_graph` `force` semantics (`tool_schemas.py:502-505`; handler `agent.py:1598-1619`)
- **Why authorization is needed**: Changes mutation semantics / application
  behaviour (AGENTS.md "No Application Flow Changes Without Permission";
  §3.5/§3.6). Note: AGENTS.md forbids behavioral error strings, so the fix
  cannot be "tell the model to use force" — the factual "Port is not
  connected" message already states the failure.

### D.5 Numeric↔named port resolution for message/PDU connections
- **Source**: `dsp_gauntlet_critique.md` §MAC Sniffer & #6
- **Location**: connection dispatch / `inspect_graph` port reporting
- **Why authorization is needed**: Runtime normalization or new output
  hints = model-surface/runtime change (§3.5/§3.6). Evidence: MAC-sniffer
  scenarios fail because the model uses numeric ports where named PDU ports
  are required.

---

## Needs Investigation

### N.1 Recovery reason-string cited by R1 does not match the classifier
- `r1_release_critique.md` §3 quotes `recovery_decision = no_recovery_needed`
  with reason "no failed tool result". The classifier
  (`tests/llama_eval/_recovery.py:34-103`) returns
  `NONRECOVERABLE_FAILED_MUTATION` (`recoverable=False`, reason "no recovery
  policy for failed tool") for a `change_graph` `internal_error`. The quoted
  string is absent. Missing: the harness path that *reports* the recovery
  decision to the critique (may differ from the classifier). Cannot
  reconcile without it.

### N.2 Is a retry circuit-breaker / dedup-key refinement still needed after 1.1?
- R2 §6 / DSP #5 propose a circuit-breaker after N identical errors. The
  retry-storm guard did not fire because the model varied the `reasoning`
  string, changing canonical args. After 1.1 those calls succeed, so the
  gap is likely benign — but there is no post-fix data to confirm. Defer
  until 1.1 lands and suites re-run.

### N.3 Do internal/non-MVP tool names leak into model-visible strings?
- `dsp_gauntlet_critique.md` #9 implies leakage, but `qam_order256` called
  `update_params` as a *tool* — `update_params` is a `change_graph` kwarg
  (`tool_schemas.py:429`), not a tool, so this is model confusion, not
  string leakage. Needs a grep of model-facing error payloads to confirm
  no internal tool names appear before any change is made.

---

## Critique Accuracy Notes

- **`reasoning` is schema-declared, not hallucinated.** R1 §1 ("the tool
  schema … does not include a `reasoning` parameter"), R2 §1 ("a `reasoning`
  keyword argument that does not exist in the tool schema"), DSP §1 ("the
  model was trained on tools that accept `reasoning`"), and scenario11
  ("Included `reasoning` — not in tool schema") are all incorrect:
  `tool_schemas.py:386-388` declares it and `:507` marks it **required**
  with `strict=True`. Only `scenario12_fft_critique.md` is correct. The
  "retrain/reprompt the model to stop emitting reasoning" recommendations
  are therefore backwards — they would instruct the model to violate its
  own tool schema.
- **R1 recovery reason string** — see N.1; the quote is not in the code.
- **Preflight already supplies factual recovery data.** R2/DSP claim the
  model gets no useful signal on failure; in fact `preflight_rejected`
  lists valid param names (e.g. "available params are comment, value") and
  `unknown_block_id` names the missing block. The model ignores it — a
  behaviour issue, not a missing-information issue.

---

## Out of Scope (deferred)

- **Search-backend / lexical-noise rework** (scenario12 #3 "strip noise
  tokens / boost semantic retrieval"): architectural, no empirical data —
  AGENTS.md Anti-Symptom Rule; routed as D.3 for authorization.
- **Server-side port normalization / port-name hints** (DSP #6): runtime/
  UX change — AGENTS.md "No Application Flow Changes Without Permission";
  routed as D.5.
- **Inter-turn "surface-compliance memory"** (R2 #8): new runtime state
  shape — architectural (§3.6); no spec cited.
- **Efficiency assertions / over-fetching penalties** (R0 #7, R1 #4/#5,
  R2 #5): these propose prompt steering ("tell the model to skip inspect /
  trust explicit block ids"), which is prompt folklore (see Appendix).

---

## Appendix — Dropped Findings

Each dropped for violating §3.3 (No Ad-Hoc Slop) and/or AGENTS.md
("The System Prompt Is the Sole Behavioral Authority"; "Error strings
return facts"; "In-Band Control Flow Is Prohibited"):

- **R0 #3** — "blocklist of terms (PMT, scale factor) that must trigger
  `query_knowledge`": regex routing + prompt folklore.
- **R0 #4** — "variable-reference training via prompt examples": prompt
  folklore (the real lever is D.2).
- **R1** "reprompt/​retrain the model to not emit `reasoning`": contradicts
  the schema (see Critique Accuracy Notes).
- **R1 #4 / #5** — "prompt the model to skip pre-mutation inspect / skip
  catalog lookup for named blocks": prompt folklore.
- **R2 #2** — "add a system-prompt line: 'reasoning is NOT a valid
  argument'": directly contradicts the declared schema; also a system-prompt
  change requiring authorization.
- **DSP #4 (strong-signal part)** — "append a strong (non-ALL-CAPS) signal
  to the error": behavioral error string, prohibited; the factual signal
  already exists (see Critique Accuracy Notes).
- **DSP #9** — "remove internal/non-MVP tool names from model-visible
  strings": unverified (see N.3); `update_params` is a kwarg, not a tool.
