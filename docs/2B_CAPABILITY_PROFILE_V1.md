# 2B Capability Profile v1

## Model

`unsloth/gemma-4-E2B-it-GGUF` via llama.cpp

## Reliable

- Summarize, inspect, search, describe, validate, save, preview, raw-YAML refusal
- Simple single-parameter edits

## Partial

- Insert with exact args (verified tool works; model cannot synthesize args autonomously)
- Generic auto-insert: may trigger **MCQ clarification** when multiple candidates validate
- `auto_insert_block(preferred_block_type=...)` works end-to-end; model often sets preferred_block_type instead of goal

## Unreliable

- Natural-language insertion from vague prompt
- Multi-step graph creation
- Copying structured tool output fields into another call

## Tool-only

- `insert_block_on_connection` (needs exact connection_id, block_type, params)
- `suggest_compatible_insertions` (needs exact connection_id; returns copyable insert_tool_args)
- `auto_insert_block` (needs natural-language goal; may return clarification_required MCQ)

## Known limits

- Small model cannot effectively use `suggest_compatible_insertions` results
- Model may ask for clarification instead of acting
- Clarification Contract v1 addresses ambiguous insertions; see `docs/CLARIFICATION_CONTRACT_V1.md`
