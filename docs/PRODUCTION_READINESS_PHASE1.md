# Production-Readiness Phase 1 Evidence Baseline

Date: 2026-05-14

This is an evidence baseline and Phase 2+ execution plan. It does not change runtime behavior, add features, implement gameplay, or claim production readiness.

## Executive Classification

Current classification is supported by the local evidence run, but the runtime is not production-ready.

| Class | Capabilities |
| --- | --- |
| Release-validated | `R0_READ_ONLY`, `R1_SET_PARAM_ONLY` |
| Beta-validated | `R1_SET_STATE`, `R2_DISCONNECT`, `R3_REWIRE`, `R4A_INSERT_BLOCK_ON_CONNECTION`, `R4B_REMOVE_BLOCK`, `R4C_ADD_VARIABLE`, `R5_SAVE_LOAD` |
| Diagnostic-clean | `R7_EXACT_EXTERNAL`, `Tier5_ADVERSARIAL` |
| Diagnostic-partial | `R7_NATURAL_EXTERNAL` |
| Runtime | not production-ready |

The 2026-05-14 local sweep had a clean `R7_NATURAL_EXTERNAL` result, but this report keeps the documented classification at diagnostic-partial until the classification file, release policy, and repeated evidence are deliberately updated.

## External Sources Used

Official and primary sources consulted:

- Ollama API introduction, base URLs, and versioning: <https://docs.ollama.com/api>
- Ollama authentication and `OLLAMA_API_KEY`: <https://docs.ollama.com/api/authentication>
- Ollama Cloud: <https://docs.ollama.com/cloud>
- Ollama OpenAI compatibility: <https://docs.ollama.com/api/openai-compatibility>
- Ollama structured outputs: <https://docs.ollama.com/capabilities/structured-outputs>
- OpenAI function calling and strict schema guidance: <https://platform.openai.com/docs/guides/function-calling>
- OpenAI structured outputs: <https://platform.openai.com/docs/guides/structured-outputs>
- OpenAI agent evals: <https://platform.openai.com/docs/guides/agent-evals>
- OpenAI trace grading: <https://platform.openai.com/docs/guides/trace-grading>
- OpenAI graders: <https://platform.openai.com/docs/guides/graders/>
- GNU Radio YAML GRC: <https://wiki.gnuradio.org/index.php/YAML_GRC>
- GNU Radio Companion background: <https://wiki.gnuradio.org/index.php/GNU_Radio_Companion>
- llama.cpp official repository and server documentation: <https://github.com/ggml-org/llama.cpp> and <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>
- Qdrant FastEmbed documentation: <https://qdrant.tech/documentation/fastembed/> and <https://qdrant.tech/documentation/fastembed/fastembed-semantic-search/>
- FastEmbed primary repository: <https://github.com/qdrant/fastembed>

Notes from those sources:

- Ollama local API defaults to `http://localhost:11434/api`; Cloud uses `https://ollama.com/api`.
- Direct Ollama Cloud API calls require `Authorization: Bearer $OLLAMA_API_KEY`.
- Ollama Cloud currently does not support structured outputs; local Ollama structured outputs can use `format`, and OpenAI-compatible structured outputs use `response_format` where supported.
- Ollama OpenAI compatibility documents `/v1/chat/completions`, `/v1/models`, embeddings, tools, and a non-stateful `/v1/responses` compatibility surface.
- OpenAI recommends strict tool schemas for reliable function calls; strict schemas require all properties to be required and object schemas to set `additionalProperties: false`.
- OpenAI trace grading evaluates full agent traces, including decisions and tool calls, not only final text.
- GNU Radio YAML GRC documentation says `.grc` files are not meant for manual editing; this aligns with the repository safety rule that mutation must go through verified graph tools and `grcc`.
- llama.cpp server documentation describes OpenAI-compatible routes, function calling/tool use, schema-constrained JSON, monitoring endpoints, and context configuration; downstream apps should not depend on llama-server internal Web UI `/tools`.
- Qdrant/FastEmbed documentation supports local/in-memory semantic search and FastEmbed-based embedding generation; this fits the local docs/RAG baseline but does not prove answer quality by itself.

## Baseline Repo State

| Item | Evidence |
| --- | --- |
| Commit | `a0f59810c6f2` |
| Dirty state before report | clean; `release-manifest` reported `dirty: false` |
| `.env` handling | `.env` is ignored by `.gitignore`; `git check-ignore -v .env` reported `.gitignore:11:.env` |
| Ollama key | `ollama_key` presence was checked without printing, logging, or committing the value |
| Active tool surface | `mvp` |
| Model-facing tools | `inspect_graph`, `search_blocks`, `ask_grc_docs`, `change_graph`, `save_graph_explicit`, `load_graph_explicit` |
| Internal tools | `release-manifest` reported 17 internal tools |
| Context verification | desired 120000 tokens, actual 120064 tokens, verified true |
| Model/runtime | `unsloth/gemma-4-E2B-it-GGUF` via `http://127.0.0.1:8080` |
| Tool loop budget | `llama_max_tool_rounds: 8` |
| Assistant text fallback | false |
| Health status | ok |

`uv run grc-agent release-manifest` reported `ok: true`, health `status: ok`, and the six MVP model-facing wrappers only.

## Baseline Gate Results

| Gate | Result |
| --- | --- |
| `uv run ruff check src/ tests/` | pass, `All checks passed!` |
| `uv run ruff check` | pass, `All checks passed!` |
| `uv run python -m unittest` | pass, 1073 tests, 5 skipped |
| `uv run python -m tests.retrieval_eval.vector_regression` | pass, 290/290 provenance, 290/290 safety, vector top-k 276 threshold 276 |
| `uv run python -m tests.retrieval_eval.grc_docs_answer_eval` | pass as safety baseline; 35 rows, 35 generated answers, relevance 24, groundedness 19, misleading 0, mutation payload leakage 0, helper used 0 |
| `uv run grc-agent doctor` | pass, GNU Radio 3.10.9.2, `grcc` at `/usr/bin/grcc`, retrieval ready |
| `uv run grc-agent health` | status ok, context verified true, six MVP tools |
| `uv run grc-agent release-manifest` | ok true, dirty false before report |

The docs-answer eval exit code is not enough to claim docs QA. Its own metrics show a safety baseline with quality gaps: relevance passed 24/35 and groundedness passed 19/35; helper mode was not used.

## Dashboard Results

All dashboards were run with `--n-runs 3` source stores and `release_dashboard` with `--min-runs-per-case 3 --stability-threshold 1.0`.

| Suite | Attempts | Dashboard | Classification status |
| --- | ---: | --- | --- |
| `R0_READ_ONLY` | 42/42 | ready true | release_validated |
| `R1_SET_PARAM_ONLY` | 6/6 | ready true | release_validated |
| `R1_SET_STATE` | 9/9 | ready true | beta_validated, not release-gating |
| `R2_DISCONNECT` | 15/15 | ready true | beta_validated, not release-gating |
| `R3_REWIRE` | 21/21 | ready true | beta_validated, not release-gating |
| `R4A_INSERT` | 15/15 | ready true | beta_validated, not release-gating |
| `R4B_REMOVE` | 21/21 | ready true | beta_validated, not release-gating |
| `R4C_ADD_VARIABLE` | 15/15 | ready true | beta_validated, not release-gating |
| `R5_SAVE_LOAD` | 15/15 | ready true | beta_validated, not release-gating |
| `R7_EXACT_EXTERNAL` | 27/27 | ready true | diagnostic-clean, not release-gating |
| `R7_NATURAL_EXTERNAL` | 27/27 | ready true | diagnostic-partial, not release-gating |
| `Tier5_ADVERSARIAL` | 54/54 | ready true | diagnostic-clean, not release-gating |

Combined dashboard:

- Exit: 0
- Ready: true for the current release-dashboard policy
- Attempts: 267/267
- Scheduled runs: 267
- Unstable cases: 0
- Diagnostic unstable cases: 0
- Raw legacy entries: 0
- Raw-history issues: 0
- Manifest-missing entries: 0
- Malformed entries: 0
- Mixed-profile entries: 0
- Required phases: 20, 25, 35, 56, 57, 58, 59, 55, 71, 72, 50
- Missing required phases: none

Direct raw-history audit:

| Store | Runs | Requested raw | Executed raw | Non-MVP names | Missing raw fields |
| --- | ---: | ---: | ---: | ---: | ---: |
| `r0.json` | 42 | 39 | 39 | 0 | 0 |
| `r1_set_param.json` | 6 | 6 | 6 | 0 | 0 |
| `r1_set_state.json` | 9 | 9 | 9 | 0 | 0 |
| `r2.json` | 15 | 12 | 12 | 0 | 0 |
| `r3.json` | 21 | 9 | 9 | 0 | 0 |
| `r4a.json` | 15 | 15 | 15 | 0 | 0 |
| `r4b.json` | 21 | 21 | 21 | 0 | 0 |
| `r4c.json` | 15 | 15 | 15 | 0 | 0 |
| `r5.json` | 15 | 15 | 15 | 0 | 0 |
| `r7_exact.json` | 27 | 27 | 27 | 0 | 0 |
| `r7_natural.json` | 27 | 42 | 42 | 0 | 0 |
| `tier5.json` | 54 | 33 | 33 | 0 | 0 |

The raw names observed were only the MVP wrappers: `inspect_graph`, `search_blocks`, `ask_grc_docs`, `change_graph`, `save_graph_explicit`, and `load_graph_explicit`.

## Production-Readiness Evidence Matrix

| Area | Current status | Evidence available | Missing evidence | Risk | Next required proof |
| --- | --- | --- | --- | --- | --- |
| Graph mutation safety | Strong for tested cases, not production-complete | Unit tests, Tier5 adversarial, dashboards, rollback/validation semantics, raw-history audit | Wider graph corpus, longer conversations, repeated external examples | Untested graph shapes may expose mutation or rollback gaps | Run copied installed-example corpus with exact graph deltas and failed-validation commit checks |
| Release/beta capability coverage | Classification supported | Capability JSON, release-manifest, all dashboards n=3 | Promotion criteria for beta to release | Users may over-trust beta operations | Define promotion thresholds and require repeated clean runs across corpus |
| External examples | Partial | R7 exact/natural over installed examples | More examples across block families, hierarchical/PDU/QT graphs | Overfitting to a small installed subset | Build a versioned copied corpus and run exact/natural suites over it |
| Natural-language ergonomics | Partial | Natural R7 passed this run but remains diagnostic-partial | Multi-turn clarification, user correction, ambiguous names, realistic phrasing | Clean exact calls may not translate to real use | Gameplay/dogfood conversations with deterministic judge and transcript review |
| Adversarial safety | Good baseline | Tier5 54/54, raw YAML/docs/save/load/internal tool cases | Prompt-injection variants over docs, multi-turn coercion, malicious saved paths | Safety may fail in conversational pressure | Extend adversarial scenarios without weakening existing Tier5 |
| Docs/RAG quality | Safety baseline only | Docs-answer eval: no mutation leakage, no misleading answers, helper unused | Groundedness/relevance quality below production threshold | Users may get incomplete or weakly grounded docs answers | Set relevance/groundedness thresholds and add source coverage analysis |
| Model/runtime dependency | Known local dependency | Health verifies llama context 120064, model ready, fallback off | Model variance, restart behavior, degraded context, Cloud dummy-user validation | A single local model may hide variance | Run matrix across local model and optional Ollama Cloud dummy-user after authorization |
| Packaging/install/ops | Basic only | `doctor`, `health`, `pyproject`, console scripts | Fresh-machine install, wheel/sdist, missing GNU Radio paths, non-Linux checks | Users may fail before reaching agent behavior | Add install smoke tests and documented prerequisites |
| Health/doctor semantics | Good local signal | `doctor` and `health` pass, context verified | Negative health tests with unreachable model and unknown context in CI-like runs | False OK would be severe | Add scripted negative probes for unreachable server/context unknown |
| Debug/issue bundle | Insufficient | Traces and result stores exist | Sanitized issue bundle command, secret scrubbing proof | Debugging production failures may leak paths/secrets or omit evidence | Define redacted bundle artifact and tests |
| CI/release automation | Partial local gate | Local deterministic and dashboard runs | Hosted CI, artifact retention, release blocking rules | Manual release evidence may drift | Add CI matrix and require clean release-manifest artifact |
| User-facing workflow quality | Not proven | MVP wrappers and dogfood script exist | End-to-end conversational dogfood, save/load UX, clarification quality | Runtime can be safe but still hard to use | Implement Phase 2 gameplay runner and human-readable transcript review |

## Production-Ready Criteria

These criteria do not lower current safety standards.

| Requirement | Must-have | Should-have | Nice-to-have |
| --- | --- | --- | --- |
| Deterministic gates | `ruff`, unit tests, vector regression, docs safety eval, doctor, health, release-manifest all pass clean | Negative health tests included | Per-test timing trend |
| Local dashboards | Release-gating suites stable at n>=3, raw histories present, no legacy/internal tools | Beta suites stable over copied corpus | n>=5 release candidate burn-in |
| External exact examples | Exact external graph operations pass with graph-delta checks and no original mutation | Coverage across stream, message, QT, PDU, tags, variables | Versioned corpus dashboard |
| Natural external prompts | Natural prompts pass a published threshold without unsafe intermediate behavior | Multi-turn clarification coverage | Prompt paraphrase fuzzing |
| Adversarial suites | Raw YAML, docs-derived mutation, unsafe save/load, internal tool requests, failed validation, preview mutation all fail safe | Multi-turn coercion and prompt-injection variants | Red-team prompt pack |
| Gameplay/dogfood conversations | Dummy user, GRC Agent, deterministic judge, trace recorder produce complete artifacts | Human review sample of transcripts | Multiple dummy-user personalities |
| Docs QA thresholds | Misleading answers 0, mutation leakage 0, helper disabled or explicit research mode | Relevance and groundedness thresholds exceed agreed floor | Source diversity and freshness checks |
| Model variance checks | At least one local model and one independent dummy-user source tested without secrets in logs | Temperature/model seed variance matrix | Cloud cost/performance report |
| Install/package checks | Fresh venv install, console scripts, `doctor`, retrieval bootstrap, GNU Radio prerequisite docs | Wheel/sdist smoke | Container/devcontainer |
| Issue/debug bundle | Sanitized trace bundle contains raw calls, normalized args, graph deltas, validation results, no secrets | One-command redaction test | HTML report viewer |
| Release-manifest requirements | Dirty false, commit recorded, context verified, model ready, fallback off, six MVP tools | Signed/archived manifest | Diffable release dashboard index |

## Real GRC Graph Corpus Inventory

Original installed GNU Radio examples must not be mutated. Copy them into temporary or report workspaces before any edit.

GNU Radio version on this host: 3.10.9.2.

| Candidate | Copy required | Blocks | Connections | Stream/msg edges | Variables | Why useful | Safe operations to test | Expected delta category |
| --- | --- | ---: | ---: | --- | --- | --- | --- | --- |
| `tests/data/random_bit_generator.grc` | Recommended | 5 | 3 | 3/0 | `samp_rate` | Canonical compact fixture | inspect, preview, set `samp_rate`, safe failed edit | param/state/no-mutation |
| `tests/data/random_bit_generator_with_unused_var.grc` | Recommended | 6 | 3 | 3/0 | `samp_rate`, `unused_var` | Variable handling with unused variable | add/remove variable, inspect variable refs | variable delta |
| `tests/data/rewire_message_ambiguous.grc` | Recommended | 6 | 2 | 0/2 | none | Message-only ambiguity fixture | clarify/refuse ambiguous rewire, exact message disconnect | message edge delta/no-mutation |
| `/usr/share/gnuradio/examples/audio/dial_tone.grc` | Yes | 8 | 4 | 4/0 | `ampl`, `noise`, `samp_rate` | Small installed real example | set param, add variable, insert block on connection | param/block insertion/variable |
| `/usr/share/gnuradio/examples/digital/packet/simple_bpsk_tx.grc` | Yes | 25 | 14 | 11/3 | `amp`, `eb`, `freq`, `gain`, `pkt_len`, `rx_rrc_taps`, `samp_rate`, `sps` | Mixed stream/message packet graph | set state, inspect, exact safe param | state/param |
| `/usr/share/gnuradio/examples/digital/packet/tx_stage0.grc` | Yes | 4 | 3 | 0/3 | none | Message-only packet stage | exact disconnect/reconnect refusal checks | message edge delta |
| `/usr/share/gnuradio/examples/digital/burst_shaper.grc` | Yes | 16 | 10 | 8/2 | `samp_rate`, `window_taps` | Mixed edge graph with shaping blocks | rewire exact connection, inspect | stream edge delta |
| `/usr/share/gnuradio/examples/blocks/selector.grc` | Yes | 10 | 5 | 5/0 | `freq`, `input_index`, `output_index`, `samp_rate` | Connected remove and save/load coverage | connected remove refusal, explicit save/load on copy | refusal/save-load |
| `/usr/share/gnuradio/examples/pdu/tags_to_pdu_example.grc` | Yes | 31 | 26 | 24/2 | `samp_rate` | Larger PDU/tag graph | inspect, docs/RAG, preview only initially | no-mutation/explanation |
| `/usr/share/gnuradio/examples/qt-gui/qtgui_message_inputs.grc` | Yes | 25 | 19 | 10/9 | `fftsize`, `pkt_len`, `samp_rate` | Mixed QT/message UI graph | inspect, clarify ambiguous ports, exact disconnect on copy | message/stream delta |
| `/usr/share/gnuradio/examples/analog/sig_source_msg_ports.grc` | Yes | 6 | 4 | 2/2 | `samp_rate` | Analog source with message ports | message disconnect, set param | message/param |
| `/usr/share/gnuradio/examples/blocks/stream_mux_demo.grc` | Yes | 10 | 6 | 6/0 | `samp_rate`, `tag0`, `tag1` | Stream mux and tag variables | set variable, inspect tag parameters | variable/param |

The final corpus should record graph hashes and copies under temp/report paths. Originals and examples under `/usr/share/gnuradio/examples` must remain read-only inputs.

## Gameplay/Dogfood Harness Design

No gameplay implementation belongs in Phase 1. The proposed Phase 2 harness should be evidence-oriented.

Roles:

- Dummy user agent: emits user goals, follow-up answers, corrections, and occasional adversarial pressure. It must not receive secrets or direct filesystem authority.
- GRC Agent: the system under test, using the MVP model-facing wrapper surface.
- Deterministic judge: validates tool use, graph deltas, safety invariants, and final graph state using structured trace data and `grcc`.
- Trace recorder: stores full conversation, raw model tool calls, normalized arguments, tool outputs, graph snapshots, validation results, save/load events, and safety decisions.

Inputs:

- Copied graph path
- User goal
- Scenario script
- Allowed operations
- Expected graph delta or refusal category
- Optional clarification branches

Outputs:

- Full conversation transcript
- Raw model tool calls
- Normalized arguments
- Executed tool results
- Graph deltas
- `grcc` validation results
- Save/load events
- Safety decisions
- Final graph state and graph hash

Grading dimensions:

- Task success
- Runtime safety
- Model contract
- Clarification quality
- No unsafe mutation
- No raw legacy/internal tools
- No failed-validation commits
- No unsafe save/load
- No hidden canonicalization of unsafe requested calls

Artifact policy:

- All generated graphs go under temporary or report output directories.
- Original installed examples and repo fixtures are never mutated.
- Secrets are never logged, included in prompts, stored in traces, or copied to artifacts.
- Raw histories are preserved; dashboards fail closed when required raw fields are missing or malformed.

## Ollama Cloud Readiness Design

Phase 1 did not call Ollama Cloud. The root `.env` was checked only for presence of `ollama_key`; the value was not printed, logged, committed, or exported in the shell transcript.

Readiness findings:

- Official API base URL for direct Cloud calls: `https://ollama.com/api`.
- Official local API base URL: `http://localhost:11434/api`.
- Direct Cloud authentication uses `Authorization: Bearer $OLLAMA_API_KEY`.
- Model listing for the Ollama API is through API tags/list endpoints; OpenAI-compatible model listing is `/v1/models` where compatibility is used.
- Ollama OpenAI compatibility supports `/v1/chat/completions`, tools, JSON mode/`response_format`, embeddings, and non-stateful `/v1/responses`.
- Ollama Cloud currently does not support structured outputs, so Cloud should not be used as the deterministic structured judge unless a future doc or smoke test proves support.
- The repository's root `.env` uses `ollama_key`; if Cloud is authorized later, map it to `OLLAMA_API_KEY` in process memory only.
- Do not print environment variables. Do not include headers in traces. Do not save request payloads that include API keys.

Safe `.env` handling plan:

1. Load `.env` locally.
2. Read only `ollama_key`.
3. If present and the process lacks `OLLAMA_API_KEY`, set `os.environ["OLLAMA_API_KEY"] = value` in process memory.
4. Never echo the value, include it in exceptions, or store it in trace artifacts.
5. Redact any header named `Authorization`.

Fallback if Cloud structured outputs remain unsupported:

- Use Ollama Cloud only for free-text dummy-user prompts.
- Keep deterministic judging local.
- Use JSON extraction only when the selected API/model explicitly supports it.
- Treat Cloud output as scenario input, not safety authority.

## Required Phase 2 Code Changes

These are proposed changes only. They were not implemented in Phase 1.

| Group | Why needed | Risk | Minimal implementation | Tests required |
| --- | --- | --- | --- | --- |
| Evidence harness | Production needs conversation-level evidence, not only single-turn dashboards | Harness can accidentally become runtime policy | Add separate `tests/dogfood` runner that drives copied graphs and records artifacts | Unit test artifact schema and no original mutation |
| Corpus inventory | External coverage needs versioned graph metadata | Corpus can become stale across GNU Radio installs | Generate read-only inventory JSON with graph hash, counts, and allowed operations | Test inventory parser on fixtures |
| Trace storage | Trace grading needs raw calls and graph deltas preserved | Secrets or large artifacts can leak | Define sanitized JSONL/JSON artifact schema with redaction | Redaction tests and malformed raw-history fail-closed tests |
| Gameplay runner | Natural workflows require multi-turn simulation | Dummy user may introduce nondeterminism | Scripted scenarios with optional dummy-user text generation | Deterministic seed/offline scenario tests |
| Judge/scoring | Need separate task success, runtime safety, and model contract grades | Over-broad scoring can hide unsafe intermediate behavior | Implement deterministic judges over raw traces and final graph snapshots | Safe refusal, task failure, unsafe intermediate regression tests |
| Ollama client wrapper | Optional Cloud dummy-user requires safe auth handling | Key leakage and unsupported structured output assumptions | Small wrapper that maps `ollama_key` to `OLLAMA_API_KEY` in process and redacts headers | No-key, key-present, redaction, no-network unit tests |
| Secret handling | Production artifacts must never expose `.env` values | High severity if traces capture keys | Central redaction helper for env/header fields | Golden redaction tests |
| Docs/reporting | Evidence must be understandable and reproducible | Stale status claims | Add report generator that embeds gate versions, commit, dirty state, and dashboard summaries | Snapshot tests for report labels |

## Phase 2 Plan

1. Freeze current safety contracts as non-negotiable gates: six MVP tools, raw-history preservation, no original mutation, no raw YAML edits, explicit save only, `grcc` validation, rollback on failure.
2. Add a read-only corpus inventory artifact and a copied-graph corpus builder.
3. Implement the gameplay/dogfood runner outside runtime code.
4. Add deterministic trace judges for task success, runtime safety, model contract, clarification quality, and artifact hygiene.
5. Add sanitized trace/report bundle generation.
6. Add optional Ollama Cloud dummy-user support only after explicit authorization to call the API.
7. Repeat dashboards and gameplay on copied installed examples.
8. Define promotion rules for diagnostic-partial and beta capabilities before changing classification.

## Production-Readiness Gaps

The current system is not production-ready because:

- Only `R0_READ_ONLY` and `R1_SET_PARAM_ONLY` are release-validated.
- Most mutation and lifecycle capabilities remain beta-validated, not release-gating.
- Natural external prompting remains classified diagnostic-partial despite a clean local run.
- Docs/RAG is safety-clean but not quality-complete; groundedness and relevance are not high enough for production claims.
- Multi-turn gameplay/dogfood evidence has not been implemented or run.
- Model variance, Cloud dummy-user behavior, restart/degraded-context behavior, and fresh install/package checks are not proven.
- A sanitized issue/debug bundle is not yet defined and tested.
- CI/release automation is not yet sufficient to replace manual local release evidence.

## Final Classification

Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`

Beta-validated: `R1_SET_STATE`, `R2_DISCONNECT`, `R3_REWIRE`, `R4A_INSERT_BLOCK_ON_CONNECTION`, `R4B_REMOVE_BLOCK`, `R4C_ADD_VARIABLE`, `R5_SAVE_LOAD`

Diagnostic-clean: `R7_EXACT_EXTERNAL`, `Tier5_ADVERSARIAL`

Diagnostic-partial: `R7_NATURAL_EXTERNAL`

Runtime: not production-ready
