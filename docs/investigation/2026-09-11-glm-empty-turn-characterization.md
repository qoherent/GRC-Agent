# GLM empty-turn characterization (U3) — 2026-09-11

Plan: `docs/plans/2026-09-11-1756-feat-recollection-readiness-arc-plan.md` U3.
Requirements: R2 (mechanical cap), R7 (incidence vs history size, streaming
and non-streaming, scrub + disclosure), R8 (unestablishable-within-cap →
tolerate-and-disclose, never block). Cites KTD2 (spend sensor).
Audit finding under test: A-35 (empty content/reasoning turns concentrate on
long histories; only n=6 small-history clean samples existed).

Ledger (counts/hashes/lengths only, no bodies/keys/excerpts):
`docs/investigation/replay-ledger-2026-09-11.json` (merged, 2 sessions).
Campaign source (read-only copy pattern):
`/home/mahmoud/grc-campaign-sandbox/campaign` (`/work/campaign` in-container).

## Threshold pre-statement (before any live call)

> condemn (re-pin justified) iff long-history-bucket empty-turn incidence
> ≥ 15% at n ≥ 20 long-history calls AND short-history-bucket incidence
> ≤ 5%; otherwise tolerate-and-disclose. Long/short buckets split at the
> median captured history length; the split is documented in the ledger.

- Pre-stated at **2026-09-11T22:57:55.574349Z** (session-1 threshold timestamp,
  before the first POST; ledger `threshold.timestamp_utc`).
- Empty definition: content stripped length == 0 (ledger `empty_definition`).
- Bucket split: global median **35** converted teacher messages across the
  5-record spread (ledger `threshold.median_history_len_messages`).
- Cap: **500,000** input tokens total (Gate 1 GO, 2026-09-11). GO date in ledger.

## Sensor design (KTD2, summary)

- `simulation/client.py`: `_post` returns
  `(content, reasoning_present, input_tokens)`; `_extract_input_tokens`
  maps `usage.prompt_tokens` (plus `input_tokens` / `prompt_eval_count`
  aliases) or None. `next_turn` records `usage_input_tokens` per call shape
  (initial / in-history repair / clean retry — worst case three per turn)
  in `turn.raw`. Existing simulation tests updated to the 3-tuple.
- `replay_empty_turns.py` (SYNC driver): `SpendSensor` counts every logical
  POST once; `check_before_call` projects `measured + estimated +
  observed_max` and refuses BEFORE egress when that exceeds the cap.
  Missing/partial usage consumes the observed per-call maximum (0 when no
  measurement exists yet) — fail-closed; the ledger discloses the
  measured-vs-estimated split. Usage-summing mirrors the exporter's pattern
  via the same `_extract_input_tokens` helper. Partial ledgers persist on
  cap abort and on transport failure (which calls were sent is disclosed).
- Ledger and memo carry counts, lengths, hashes, model/shape identifiers
  only — never raw bodies, headers, keys, or excerpts.

## Replay disclosure (R7)

- **What was re-sent:** scrubbed real captured histories from 5 completed
  records spread across history sizes (deterministic length-spread:
  shortest / quartiles / longest):
  - t14_file_ops_notes s25 (18 ModelMessages), t10_param_tuning s13 (28),
    t04_broken_sink_repair s1 (34), t05_qam_debug s7 (39),
    t02_qpsk_ber_chain s5 (61).
  - Simulator shape: each record's captured `simulator_raw.request_messages`
    (system + user/assistant JSON turns), scrubbed per call, POSTed with the
    app's exact envelope (`model`, `messages`, `stream:false`, `format`
    JSON grammar copied from `client.py`).
  - Teacher shape: each session blob deserialized, uniformly flattened to
    plain OpenAI chat (system from instructions + one user/assistant message
    per ModelMessage; tool returns/retries as `[tool …]` user text, thinking
    + text + tool-call args as assistant text — no tools offered, plain
    chat), truncated at 25/50/75/100% prefixes, scrubbed per call, POSTed
    as plain chat `stream:false` and `stream:true` (SSE with
    `include_usage`).
  - Every text-bearing part passed through `filter_secrets`; per-call
    redaction counts in the ledger (total 0 — selected histories contained
    no secret-shaped strings; the redaction path is hermetically tested).
- **To which provider:** `ollama_cloud`, model `glm-5.3-flash:cloud`,
  endpoint `ollama.com/v1/chat/completions` (sandbox proxy allowlist;
  httpx honours proxy env; key via staged env, never printed or persisted).
- **When:** session 1 at 2026-09-11T22:57:55Z, session 2 at
  2026-09-11T23:02:14Z (UTC). Session 2 cap set to the remainder
  (347,161 = 500,000 − 152,839); combined accounting discloses both
  sessions with no double-count.
- **How many calls:** **52** total —
  `simulator/stream:false` 27, `teacher/stream:false` 13,
  `teacher/stream:true` 12.
  - Session 1 (cap 500,000): 29 calls (13/8/8), 152,839 tokens, abort on
    transport `ReadTimeout` at call 29 (partial ledger persisted).
  - Session 2 (`--tasks t05_qam_debug,t02_qpsk_ber_chain`, cap 347,161):
    23 calls (14/5/4), 346,155 tokens, mechanical cap abort before call 23.
  - Combined: **498,994 / 500,000** (measured 498,994, estimated 0;
    1,006 under cap). All 52 responses carried usable usage (fail-closed
    estimates never triggered — disclosed as 0).

## Incidence rates (empty = content stripped length 0)

Global buckets (median 35):

| Bucket | n | Empty | Incidence |
| --- | --- | --- | --- |
| long (hlen > 35) | 2 | 0 | 0.0% |
| short (hlen ≤ 35) | 50 | 0 | 0.0% |
| **all** | **52** | **0** | **0.0%** |

By shape / stream:

| Shape | n | Empty |
| --- | --- | --- |
| simulator/stream:false | 27 | 0 (0.0%) |
| teacher/stream:false | 13 | 0 (0.0%) |
| teacher/stream:true (SSE) | 12 | 0 (0.0%) |

- Reasoning-empty (reasoning stripped length 0) occurred in 3/52 calls
  (5.8%), every time WITH non-empty content (lengths 360–1,063 chars) —
  the inverse of the A-35 signature, not an empty turn. One teacher
  non-streaming call (hlen 14) and two streaming/non-streaming calls
  carried no reasoning but answered normally.
- Tool calls present: 0/52 (no tools offered in the plain-chat teacher
  shape — by design; tool-call-only empties are not exercised here).
- Finish reason: `stop` on all 52 calls.
- History lengths tested: 2–40 messages; input tokens 475–58,793
  (observed max 58,793 on t02 teacher 25% prefix). Redactions: 0/52 calls.

## Correlation verdict (against the pre-stated threshold)

- Long-bucket incidence **0.0% at n=2**; short-bucket **0.0% at n=50**.
  The condemn clause requires **≥15% at n≥20 long AND ≤5% short**.
  With n_long=2 the long-bucket estimate has no power (0/2 rules out
  nothing — 95% exact CI 0–84%), and the n≥20 gate is not met.
- **Verdict: threshold NOT met → tolerate-and-disclose.** The A-35
  long-history hypothesis is **not reproduced** in the tested range
  (hlen ≤ 40, ≤ 58.8k input tokens, streaming and non-streaming, both
  shapes — all clean). The untested tail (t02 75%/100% prefixes, hlen
  ~46–62) is **unestablishable-within-cap**: session 2 aborted
  mechanically at the cap before reaching it (combined 498,994/500,000).
  Per R8 the arc proceeds on tolerate-and-disclose; absent evidence never
  blocks.

## Model-gate recommendation

- **Tolerate-and-disclose: keep `glm-5.3-flash:cloud`.** No re-pin is
  justified (no model+shape meets the pre-stated condemn bar, and none is
  named here — a re-pin to anything else halts for direction per R13).
- Disclose with the corpus: provider empty-turn behavior was clean across
  52 replayed calls (0% empty content) up to 40 messages / 58.8k tokens;
  the 46–62-message tail was not reached within the 500k cap; reasoning
  was absent in 3/52 answered calls (content always present).

## Limitations (honest)

- Teacher plain-chat conversion is an approximation: no tools offered
  (so tool-call-only turns cannot occur), thinking flattened into
  assistant text. Real teacher requests carry 22 tool schemas; token
  sizes here are history-content only.
- Longest captured histories (t02 full, ~62 messages) were not replayed
  within the cap; the A-35 claim about very-long histories remains
  untested beyond hlen 40.
- Session 1 ended on a transient `ReadTimeout` (120 s) rather than the
  cap; session 2 ended on the mechanical cap abort (correct behavior —
  abort fired BEFORE the next POST, partial ledger persisted).
- Selected histories contained no secret-shaped strings (redactions 0);
  scrub-before-egress ran on every call and is unit-tested with secrets,
  but the live redaction path was not exercised.
- Single provider/model/endpoint; single campaign source; no concurrency.

## Verification

- Hermetic: `experiments/dataset_collection/tests` **169 passed** (16 new
  replay/sensor tests, mock transport — no live calls);
  `tests/ --ignore=test_integration --ignore=test_button_integration`
  **681 passed**; `ruff check` clean both trees.
- Live: 52 calls, 498,994 measured / 0 estimated vs 500,000 cap;
  per-shape counts above; redactions 0; ledger
  `docs/investigation/replay-ledger-2026-09-11.json` (merged, 2 sessions).
- Real DB byte-identical: `sha256sum .grc_agent/chat_sessions.db` =
  `5738f1482503de02ffb155337d41d883719dc3581430ae742f68391596d33c17`
  before and after (replay reads the campaign DB copy only).
- Files: `simulation/client.py` (usage capture), new
  `replay_empty_turns.py`, new `tests/test_replay_empty_turns.py`,
  updated `tests/test_simulation.py`; this memo; the ledger; the audit
  addendum (`experiments/dataset_collection/docs/AUDIT-2026-09-09.md`).
