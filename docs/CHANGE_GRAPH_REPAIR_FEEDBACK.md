# Change Graph Repair Feedback

This note records the current fix after the E4B null-sink edit audit.

## Problem

The live edit failed safely: E4B discovered `blocks_null_sink`, inspected
`blocks_char_to_float_0`, and attempted a single `change_graph` batch with an
added block plus connection. GNU validation rejected the candidate because the
new null sink defaulted to `complex` while the connected source output was
`float`.

The saved graph was not modified, so rollback and no-commit behavior worked.
The failure was mostly in model-visible repair feedback, not in the backend
mutation safety path.

## Resolution

The model-facing context now keeps the repair path grounded and generic:

- `search_blocks` compact model context preserves selected catalog params,
  enum options, and port metadata for high-confidence candidates.
- `change_graph` compact model context includes concise native GNU validation
  errors and the wrapper hint.
- Generic transaction hints use the flat `change_graph` vocabulary:
  `add_blocks[].block_id`, `add_blocks[].instance_name`,
  `add_blocks[].params`, `update_params[].params`, and
  `add_connections[].src/dst`.
- For repairable stream dtype mismatches on newly added configurable blocks,
  `change_graph` can return a catalog-derived hint such as:
  `retry with add_blocks[].params.type="float"`.

This is intentionally not a block-specific macro. It is derived from GNU
catalog metadata: the connected port dtype mismatch, the newly added block's
templated port dtype, and the enum parameter that controls that dtype.

## Verification Status

Deterministic tests were added for:

- compact `search_blocks` model context exposing the top candidate's catalog
  params and port dtype facts;
- failed `change_graph` add/connect preserving rollback and returning a flat,
  repairable dtype hint;
- compact `change_graph` model context rendering native GNU validation errors.

The interrupted live E4B retry was not completed. Run it again when ready.
