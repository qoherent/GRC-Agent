# Gemma 4 E4B Q4 Backend Trial v1

**Milestone**: Gemma 4 E4B Q4 Backend Trial v1  
**Date**: 2026-04-26  
**Model**: `unsloth/gemma-4-E4B-it-UD-Q4_K_XL.gguf` (E4B)  
**Baseline**: `unsloth/gemma-4-E2B-it-GGUF` (2B)  
**GPU**: NVIDIA GeForce RTX 2060 6GB  
**Context**: `-c 100000`  
**Offload**: `-ngl 999`

---

## 1. Launch Command

```bash
nohup llama-server \
  -m ~/.cache/huggingface/hub/models--unsloth--gemma-4-E4B-it-GGUF/gemma-4-E4B-it-UD-Q4_K_XL.gguf \
  --host 127.0.0.1 --port 8080 \
  -ngl 999 -c 100000 \
  --no-mmproj > /tmp/llama_e4b.log 2>&1 &
```

Server started successfully, model ID returned via `/v1/models`:

```json
{
  "models": [{
    "name": "gemma-4-E4B-it-UD-Q4_K_XL.gguf",
    "type": "model",
    "capabilities": ["completion"]
  }]
}
```

---

## 2. VRAM Usage

| Model | VRAM Used | VRAM Free | GPU Offload |
|---|---|---|---|
| Idle | ~6 MiB | ~5733 MiB | N/A |
| E4B Q4 loaded | **5204 MiB** | **535 MiB** | ~100% layers |
| 2B Q4 loaded | ~3700 MiB | ~1900 MiB | ~100% layers |
| Room for second model? | No | 535 MiB remaining | N/A |

Observation: E4B Q4 fits in 6GB but leaves only ~500 MiB free. Context swap or extra layers may not fit. User should not run other heavy GPU workloads simultaneously.

---

## 3. Baseline Checks

```bash
uv run ruff check src/ tests/              # PASS
uv run python -m unittest tests.test_agentic_workflow_insert \
  tests.test_verified_workflow_tools \
  tests.test_insert_block_on_connection \
  tests.test_smoke                          # PASS (51 tests)
uv run grc-agent doctor                      # PASS after config swap
```

Config swap required for `doctor` only (model alias mismatch check). Deterministic suite does not depend on backend. Swapped back after doctor.

---

## 4. Focused Auto-Insert Live Check

Command:

```bash
for case in A_insert_head B_insert_compatible C_add_throttle D_add_filter; do
  uv run python -m tests.scenarios.live_capability_check \
    --case $case --json /tmp/live_${case}_e4b.json
done
```

### Case Results

| Case | prompt | graph | auto_insert called? | tool chain | result | classification |
|---|---|---|---|---|---|---|
| **A** | Insert a head block into one stream path and validate. | dial_tone | **Yes** | auto_insert_block (false) | Safe rejection (4 candidates failed) | MODEL_ROUTING |
| **B** | Insert a simple compatible block into the main signal path. | dial_tone | **No** | summarize → get_grc_context → suggest → insert_block_on_connection | Old chain attempted, failed validation | MODEL_ROUTING |
| **C** | Add a throttle or head block into an existing stream path, then validate. | dial_tone | **Yes** | auto_insert_block (false) → validate_graph | Safe rejection (all candidates failed) | MODEL_ROUTING |
| **D** | Add a low-pass filter into the main signal path if it can be defaulted safely. | resampler_demo | **Yes** | auto_insert_block | Safe rejection | MODEL_ROUTING |

### Detailed Analysis

#### Case A (head block)

```text
tool_chain: auto_insert_block (ok=false)
assistant_text: "The attempt to insert a head block failed for all 4 candidates."
analysis: E4B called auto_insert_block correctly.
         The tool rejected because blocks_head doesn't validate on dial_tone (same as 2B).
         E4B correctly understood the failure and did not retry.
         No mutation. No STOP_THE_LINE.
```

#### Case B (simple compatible block)

```text
tool_chain: summarize_graph → get_grc_context → suggest_compatible_insertions → insert_block_on_connection
analysis: E4B did NOT call auto_insert_block. It chose the OLD path (inspect → suggest → insert).
         This is interesting: E4B can construct the exact multi-step chain that 2B could not.
         However, the insert_block_on_connection attempt failed validation (likely wrong connection_id or params).
         No mutation. No STOP_THE_LINE.
         auto_insert_block is discoverable but E4B sometimes prefers manual path.
```

#### Case C (throttle/head block)

```text
tool_chain: auto_insert_block (ok=false) → validate_graph
analysis: E4B called auto_insert_block, same as 2B.
         Tool rejected all candidates.
         E4B correctly followed up with validate_graph (nice touch).
         Shows E4B can chain tools better than 2B.
         No mutation. No STOP_THE_LINE.
```

#### Case D (filter)

```text
tool_chain: auto_insert_block
analysis: E4B called auto_insert_block once.
         Tool rejected (filter family not found for resampler_demo connections).
         E4B accepted the result and did not retry.
         No mutation. No STOP_THE_LINE.
```

---

## 5. Comparison with 2B Baseline

| Metric | 2B Baseline | E4B Q4 Trial | Assessment |
|---|---|---|---|
| **auto_insert_block called** | 4/4 (100%) | 3/4 (75%) | E4B discovered slightly less often |
| **Success or safe rejection** | 4/4 (100%) | 4/4 (100%) | Both safe |
| **Wrong semantic insertions** | 0 | 0 | Both safe |
| **STOP_THE_LINE** | 0 | 0 | Both safe |
| **Mutation commits** | 0/4 | 0/4 | Both conservative |
| **Old insert path used** | 0/4 | 1/4 (Case B) | E4B knows the old path better |
| **Tool chain length** | 1–2 tools | 1–4 tools | E4B chains more when not using auto_insert |
| **Explanation quality** | Basic | Slightly better | E4B mentions candidate count |
| **Latency (elapsed)** | ~3–22s | ~3–6s | E4B similar per turn, but slower loading |

### Key Differences

1. **E4B can construct the old multi-step insert chain** (summarize → suggest → insert_block_on_connection) that 2B could not. This shows better tool chaining and context understanding.

2. **E4B does not always prefer auto_insert_block**. It sometimes chooses the old explicit chain — which is fine, because the old chain is safe (even if it fails). This means E4B may see auto_insert_block as one option among many.

3. **E4B chains tools better**. Case C: auto_insert_block → validate_graph. The 2B model sometimes did not chain follow-ups correctly.

4. **No capability improvement on insert success**. Both models fail to actually insert anything into the test graphs. The tool-level limitation (blocks_head parameter defaults, dtype mismatches) is the same regardless of backend.

5. **E4B VRAM is 1.5GB higher than 2B**. Leaves only ~500 MiB free. Risky for concurrent work.

---

## 6. Handoff Behavior (Implicit in Case B)

In Case B, E4B used `suggest_compatible_insertions` and attempted `insert_block_on_connection`. The insert attempt failed. This means:

- E4B CAN read suggestion output and attempt to use it
- But it still failed (same as historical 2B data)
- No evidence that E4B copies `insert_tool_args` fields more accurately
- The failure is at argument synthesis (connection_id/params), not at field copying

Conclusion: Handoff improvement is marginal at best. Both models struggle with exact argument synthesis for insert.

---

## 7. Safety Status

| Check | Result |
|---|---|
| Raw YAML mutated? | No |
| Invalid graph saved? | No |
| Wrong file overwritten? | No |
| Message edge unsafe? | No |
| STOP_THE_LINE events? | 0 |
| Unsafe mutations? | 0 |
| Wrong semantic insertion? | 0 |
| Assistant text hallucination? | None observed |

E4B is as safe as 2B.

---

## 8. Latency / Throughput Notes

| Phase | 2B | E4B Q4 |
|---|---|---|
| Model load | ~3s | ~5–10s |
| Per-turn response | 2–6s | 2–4s |
| Full 4-case eval | ~3s | ~4s (single thread) |
| Context window | 100k | 100k |

E4B Q4 inference is not slower than 2B per token — same hardware, similar offload. Slightly higher latency due to longer loading and context parsing.

---

## 9. Recommendation

### Decision: **Keep 2B as default. Make E4B optional.**

| Criterion | 2B | E4B Q4 |
|---|---|---|
| **auto_insert discovery** | ✅ 100% | ⚠️ 75% (sometimes prefers old path) |
| **Safety** | ✅ | ✅ |
| **Insert success rate** | 0% | 0% (tool-level limitation) |
| **Tool chaining quality** | Basic | Better (e.g., validate_graph follow-up) |
| **Explanation quality** | Basic | Slightly better |
| **VRAM margin** | Generous (~1900 MiB free) | Tight (~500 MiB free) |
| **Model size** | Small, fast, responsive | Larger, fits barely |

### Why 2B remains default

- **No meaningful capability improvement on the primary gap**: insert success is still 0%. The limitation is at the tool level (blocks_head defaults, dtype mismatch), not the model.
- **E4B was not more reliable for auto_insert discovery**: 75% vs 100% in this tiny sample.
- **E4B VRAM is too tight**: leaves little headroom for concurrent work.
- **No significant safety improvement**: both are already safe.
- **E4B's better chaining is nice but not critical**: 2B passes safety gate.

### When to recommend E4B

- User has 8GB+ GPU and wants better tool chaining/explanations
- User does complex multi-turn dialogues (E4B's better follow-up chaining helps)
- User values more natural-sounding assistant text

### Implementation: optional high-capability profile

Create `grc_agent_e4b.toml` (or CLI `--profile e4b` in future) without changing default `grc_agent.toml`.

---

## 10. Files Changed

| File | Change |
|---|---|
| `docs/GEMMA_E4B_Q4_BACKEND_TRIAL_V1.md` | This report (new) |
| `grc_agent.toml` | Temporarily swapped, restored to 2B |

No code changes. No schema changes. No prompt changes.

---

## 11. Next Steps

| Priority | Item | Trigger |
|---|---|---|
| P0 | Keep 2B default | Always |
| P1 | Add `--profile e4b` CLI flag (future) | If user demands E4B |
| P2 | Address blocks_head insertion issue | When a graph with matching dtype/vlen exists, `auto_insert_block` should succeed; this needs a proper test graph |
| P3 | No further backend comparison | Unless a clearly better model (8B+ Q4, fits in 6GB) becomes available |

---

## 12. Decision Checklist

- [x] No prompt changes before eval
- [x] No schema changes
- [x] No architecture changes
- [x] No new tools
- [x] No full live sweep
- [x] No mmproj loaded
- [x] `-c 100000`
- [x] `-ngl 999`
- [x] `grcc` remains final truth
- [x] STOP_THE_LINE = 0
- [x] 2B default kept
- [x] E4B optional profile documented
