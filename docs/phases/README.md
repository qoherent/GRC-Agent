# GRC Agent Phase Plans

This folder contains the isolated implementation plans for the GRC-native agent pivot.

Order:
- `phase_0_context_and_rules.md`
- `phase_1_retrieval_index_search_grc.md`
- `phase_2_block_describe.md`
- `phase_3_session_graph_access.md`
- `phase_4_validation_preflight.md`
- `phase_5_transaction_editing.md`
- `phase_6_agent_cli_integration.md`

Package focus by phase:
- Phase 1: `src/grc_agent/retrieval/`
- Phase 2: `src/grc_agent/catalog/`
- Phase 3: `src/grc_agent/session/`
- Phase 4: `src/grc_agent/validation/`
- Phase 5: `src/grc_agent/transaction/`
- Phase 6: `src/grc_agent/agent.py`, `src/grc_agent/cli.py`

Current implementation status:
- Phase 1 retrieval is implemented in the current repo state.
- Phase 2 catalog description is implemented in the current repo state.
- Phase 3 through Phase 6 remain planning docs and should treat Phase 1 and Phase 2 as the baseline below them.

Start with Phase 0, then work in order.
