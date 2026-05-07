# Online Review Evidence Packet

Updated: 2026-05-07

This packet is intended for an online reviewer that may not have direct access to the full repository. It summarizes local evidence gathered during the harness audit. It is not a substitute for source review. Treat it as secondary evidence and label any conclusion based only on this file as `locally reported` or `unverified by reviewer`.

Attach this together with:

- `docs/BLUEPRINT.md`
- `docs/BLIND_HARNESS_AUDIT.md`
- `README.md`

If possible, also attach the full source tree or the files listed in `docs/BLIND_HARNESS_AUDIT.md`.

## Review Mode Guidance

If full source is available:

- Verify every claim against source, tests, config, and command output.
- Give a final verdict only after checking STOP_THE_LINE conditions.

If only docs plus this packet are available:

- Do a preliminary architecture review.
- Do not give an unqualified final production verdict.
- State which claims are documented, locally reported, externally verified, or unverified.
- Ask one next question for the smallest evidence needed to resolve the highest-risk uncertainty.

## Repository State Reported Locally

Collected on 2026-05-07 in `/home/mahmoud/Desktop/GRC_Agent`.

- Branch: `main`.
- HEAD: `fca34efbb6d3`.
- Recent commit: `fca34ef refactor: rename search_help to ask_grc_docs and finalize MVP tool integration with new DocsAnswerAdvisor.`
- Working tree during the original audit had untracked generated `reports/`; those were later deleted.
- Current docs consolidation changed:
  - `README.md`
  - `docs/BLUEPRINT.md`
  - `docs/PACKAGE_GUIDE.md`
  - `docs/SYSTEM_DESIGN_BIBLE.md`
  - `docs/BLIND_HARNESS_AUDIT.md`
  - `docs/ONLINE_REVIEW_EVIDENCE_PACKET.md`

## Verification Commands Reported Locally

These commands were run locally during the audit.

| Command | Reported result |
| --- | --- |
| `uv run ruff check src/ tests/` | Pass, `All checks passed!` |
| `uv run ruff check` | Pass, `All checks passed!` |
| `uv run python -m unittest` | Pass, 998 tests, 5 skipped, 1670.319s |
| `uv run python -m tests.retrieval_eval.vector_regression` | Pass, 290 total cases, 276 vector top-k hits, 290 provenance passes, 290 safety passes |
| `uv run python -m tests.retrieval_eval.grc_docs_answer_eval` | Exit 0, 35 rows, 0 mutation leakage, 0 misleading answer count, 24/35 relevance passes, 19/35 groundedness passes |
| `uv run grc-agent doctor` | Pass: Python 3.12.3, `grcc` on PATH, GNU Radio 3.10.9.2, config found, retrieval ready |
| `uv run grc-agent health` | Exit 0 with `status=ok`, `llama_actual_context_tokens=null`, and `/props` connection refused |

Interpretation:

- Deterministic safety gates are broad and passing.
- Full unittest is too slow for a normal inner-loop gate.
- Vector retrieval safety/provenance regression is strong.
- Docs-answer quality is not production-grade yet.
- Health semantics are the highest-priority readiness issue.

## Key Source Claims Reported Locally

These were observed by local source inspection. Verify against code if source is available.

### Tool Schemas

Reported file: `src/grc_agent/runtime/tool_schemas.py`.

- `PUBLIC_TOOL_NAMES` contains 17 legacy/internal tools:
  - `new_grc`
  - `load_grc`
  - `summarize_graph`
  - `search_grc`
  - `get_grc_context`
  - `describe_block`
  - `search_manual`
  - `semantic_search_grc`
  - `suggest_compatible_insertions`
  - `insert_block_on_connection`
  - `auto_insert_block`
  - `remove_connection`
  - `rewire_connection`
  - `apply_edit`
  - `propose_edit`
  - `validate_graph`
  - `save_graph`
- `MVP_MODEL_TOOL_NAMES` contains exactly four wrappers:
  - `inspect_graph`
  - `search_blocks`
  - `ask_grc_docs`
  - `change_graph`
- `MODEL_TOOL_NAMES_ORDERED` includes legacy tools first, then MVP wrappers.
- `build_tool_schemas()` returns legacy schemas plus MVP schemas.

Risk:

- Default CLI chat reportedly narrows to MVP wrappers, but direct callers using all schemas can see the legacy surface.
- Tool surface should be made profile-aware through a single `ToolSurface` or equivalent object.

### Default Tool Profile

Reported files:

- `grc_agent.toml`
- `src/grc_agent/config.py`
- `src/grc_agent/cli.py`
- `tests/test_llama_server.py`

Reported behavior:

- Config default: `legacy_model_tool_surface=false`.
- CLI passes `mvp_tool_profile=not config.agent.legacy_model_tool_surface`.
- Test coverage reportedly asserts the first default CLI chat request exposes exactly `MVP_MODEL_TOOL_NAMES`.

Interpretation:

- Default MVP wrapper exposure appears implemented and tested in the main CLI chat path.
- This remains source-verifiable if the reviewer has the repository.

### Prompt/Profile Drift

Reported file: `src/grc_agent/runtime/prompt.py`.

Reported issue:

- The system prompt still instructs legacy low-level tools such as `apply_edit`, `propose_edit`, `search_grc`, `describe_block`, `validate_graph`, `save_graph`, and `suggest_compatible_insertions`.
- This conflicts with default MVP mode where those tool schemas are not exposed to the model.

Risk:

- Wastes context.
- Encourages impossible tool calls.
- Keeps old policy alive through prompt text.

Recommended decision:

- Create profile-specific prompts.
- MVP prompt should mention only `inspect_graph`, `search_blocks`, `ask_grc_docs`, and `change_graph`.
- Legacy prompt should exist only for explicit compatibility/research mode.

### Health Semantics

Reported files:

- `src/grc_agent/agent.py`
- `src/grc_agent/cli.py`

Reported behavior:

- `GrcAgent.health_check()` computes `status=ok` from internal tool count and retrieval readiness.
- CLI health then probes llama `/props`; if unreachable, it records `llama_actual_context_tokens=null` and `llama_props_error`, but still exits 0 when base status is OK.
- Local command output showed `status=ok` while llama was unreachable and actual context unknown.

Risk:

- A local model agent cannot claim end-to-end readiness if the model path is unavailable.
- Desired 120k context is not verified by health when `/props` is unreachable.

Recommended decision:

- Split health into explicit fields: `core_ready`, `retrieval_ready`, `model_ready`, `context_verified`, `tool_surface`, `model_tool_count`, `internal_tool_count`, `status`.
- Return `degraded` or `not_ready` when configured model runtime is required but unreachable or context is unknown.

### Mutation Core

Reported files:

- `src/grc_agent/transaction/apply.py`
- `src/grc_agent/flowgraph_session.py`

Reported behavior:

- `apply_edit()` calls `propose_edit()` first.
- It clones the session.
- It applies operations to the candidate clone.
- It validates the candidate with `candidate.validate()` using `grcc`.
- It commits candidate state only after validation passes.
- Failed preflight, exceptions, validation failure, and validation timeout return failure payloads without committing live state.
- `FlowgraphSession.save()` writes atomically with temp file, fsync, and replace.

Interpretation:

- Mutation safety core appears strong and should be preserved.
- Verify source before making a final safety claim.

### Fallback Parser

Reported file: `src/grc_agent/llama_server.py`.

Reported behavior:

- Parses native OpenAI-compatible llama.cpp tool calls.
- If no native tool calls exist, can parse assistant text as pseudo tool calls or mutation-shaped JSON.
- Repairs unclosed JSON stubs in some paths.
- MVP-mode tests reportedly verify fallback parser does not execute legacy mutation tools.

Risk:

- Compatibility fallback is a safety tax.
- It should not expand into a hidden router or repair layer.

Recommended decision:

- Freeze fallback parser.
- Disable it by default in MVP mode unless a model-specific compatibility flag requires it.
- Keep route/schema validation mandatory for all fallback-derived calls.

### Deterministic Turn Policy

Reported files:

- `src/grc_agent/runtime/turn_plan.py`
- `src/grc_agent/agent.py`

Reported behavior:

- Current deterministic TurnPlan contains phrase/regex-based routing assumptions for preview, load, state edit, removal, parameter edit, uncertain mutation, insertion anchors, and related categories.
- Advisor is shadow-only and does not control default runtime routing.

Risk:

- The documented advisor-first direction conflicts with remaining phrase-list policy.
- This is acceptable as production-candidate scaffolding only if explicitly acknowledged and tested.

Recommended decision:

- Do not add more phrase dictionaries.
- Consolidate policy behind a typed executor or promote advisor only after eval evidence.
- Runtime must still own safety regardless of advisor status.

### RAG/Search

Reported files:

- `src/grc_agent/retrieval/vector.py`
- `src/grc_agent/manual/search.py`
- `src/grc_agent/agent.py`
- `tests/retrieval_eval/*`

Reported behavior:

- Qdrant local mode + FastEmbed default `BAAI/bge-small-en-v1.5`.
- Vector search is read-only and strips mutation-shaped keys.
- Manual search is lexical over cleaned GNU Radio docs and returns citations.
- `ask_grc_docs` is explanation-only and helper synthesis is disabled by default.

Reported eval:

- Vector regression strong: 290 safety/provenance passes.
- Docs answer quality incomplete: 24/35 relevance, 19/35 groundedness.

Recommended decision:

- Keep Qdrant + FastEmbed for now.
- Improve docs source coverage and answer thresholds before adding rerank/hybrid complexity.

## STOP_THE_LINE Status From Local Audit

No STOP_THE_LINE mutation issue was proven locally in the audited default paths.

Not proven locally:

- Legacy mutation tools exposed in default MVP model chat.
- Preview mutation.
- Raw YAML mutation reaching live graph.
- Failed preflight commit.
- Failed `grcc` validation commit.
- Rollback bypass.
- Source retrieval driving mutation payloads.
- Fallback parser bypassing route validation.
- Save without explicit request.

High-risk readiness issue reported locally:

- `grc-agent health` returned `status=ok` while llama.cpp was unreachable and actual context was unknown.

Suggested classification:

- Treat this as a production-readiness blocker.
- Treat it as STOP_THE_LINE for release claims, not as a proven graph mutation safety failure.

## Suggested First Reviewer Question

Ask this first if the owner has not already answered it:

> Should `grc-agent health` represent core package readiness only, or end-to-end model-runtime readiness?

Recommended answer:

> Split health into explicit readiness fields and return degraded/not_ready when llama.cpp is configured but unreachable or actual context is unknown.

Reason:

> This keeps health honest and avoids claiming local model readiness when the model path cannot serve requests or prove context capacity.

## Minimum Evidence Needed For Final Review

If the reviewer does not have full source, ask for one of these:

1. Full repository snapshot at commit `fca34efbb6d3` or newer.
2. A zip containing the source/test files listed in `docs/BLIND_HARNESS_AUDIT.md`.
3. Command outputs plus source excerpts for the highest-risk claims:
   - default tool schema exposure
   - prompt contents
   - health implementation
   - `apply_edit` transaction path
   - fallback parser tests
   - MVP wrapper live eval/dogfood evidence

Do not give an unqualified production-ready/not-ready verdict from docs alone.
