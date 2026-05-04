# MVP Wrapper Controlled Dogfood - 2026-05-03

No sufficient copied user/workspace graph corpus (>=5) was explicitly available for this run; used copied installed examples only.

## Corpus Availability

- Copied user/workspace graphs discovered: 0
- User graph search directory: `None`
- Source used for this run: `installed_example`

## Scope

- Graphs selected: 30
- Candidate skips: `{"family_limit": 38}`
- Observations requested: 135
- Observations recorded: 135
- Intake path: `reports/dogfood/mvp_wrapper_controlled_2026-05-03.jsonl`

## Results

- Task distribution: `{"add_variable": 4, "clarification": 10, "disconnect": 3, "inspect": 15, "negative": 10, "other": 4, "param_edit": 4, "preview": 25, "retrieval": 40, "rewire": 5, "state_edit": 5, "validate": 10}`
- Task-group distribution: `{"clarification": 10, "commit_change": 25, "inspect_graph": 25, "preview_change": 25, "search_blocks": 25, "search_help": 15, "unsupported": 10}`
- Wrapper usage distribution: `{"change_graph": 36, "inspect_graph": 32, "search_blocks": 25, "search_help": 15}`
- Internal handler distribution (wrapper-level): `{"apply_edit": 7, "auto_insert_block": 2, "clarification": 10, "get_grc_context": 7, "propose_edit": 13, "remove_connection": 2, "rewire_connection": 2, "search_blocks_cache(miss)": 18, "search_grc(lexical,catalog)": 25, "search_manual": 15, "semantic_search_grc(catalog)": 18, "summarize_graph": 7, "validate_graph": 18}`
- Failure categories: `{"no_failure": 135}`
- Severity counts: `{"info": 135}`
- Clarification count: 10
- Unsupported/refusal count: 10
- STOP_THE_LINE count: 0
- Legacy tool exposure count: 0
- Wrong internal handler count: 0
- Preview mutation count: 0
- Unsupported mutation count: 0
- Invalid commit/save count: 0
- Checkpoint missing after commit count: 0
- Search cache hits observed: 8
- Search cache misses observed: 34
- Repeated generic failure clusters: 0

## Patch Decision

- No patch justified.

## Acceptance Check

- Default MVP wrappers only: PASS
- Legacy exposure = 0: PASS
- Wrong handler = 0: PASS
- Preview mutation = 0: PASS
- Unsupported mutation = 0: PASS
- Invalid commit = 0: PASS
- Checkpoint correctness: PASS
- No unresolved STOP_THE_LINE: PASS
