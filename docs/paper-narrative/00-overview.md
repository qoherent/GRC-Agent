# Paper Narrative — Overview (science-agent entry point)
No refs or citations in this folder by design. The research/scientific agent owns all references, related work, evaluation framing, and academic prose.

## What this folder is
The non-scientific, GRC-Agent-specific narrative: the idea, the features, the system design with mermaid diagrams and flows, and the short evolution/pivot history. Everything here is grounded in the repo (`README.md`, `AGENTS.md`, `CHANGELOG.md`, `docs/technical_overview.md`, `docs/grc-interaction.md`, code) and the full git history (593 commits), plus compact context from `https://gnuradioagent.com/`.

## Ingest order
1. `PAPER-NARRATIVE.md` — the single comprehensive yet concise file. Start here.
2. `01-idea-features.md`, `02-system-design.md`, `03-flows.md` — detail behind each section.
3. `04-evolution-pivots.md` — pivot history with commit hashes.

## File map
| File | Contents |
|---|---|
| `PAPER-NARRATIVE.md` | Assembled handoff: idea, features, design + 2 mermaids, flows, pivots |
| `01-idea-features.md` | Idea, audience, feature catalog |
| `02-system-design.md` | Components, invariants, tool table, 2 mermaid diagrams, error model |
| `03-flows.md` | Edit loop, run-measure-verify loop, planner handoff, approval table |
| `04-evolution-pivots.md` | 5-phase arc + tried/failed/replaced table with hashes |

## Stale-claim warnings (do not regress)
- Event loop: `gi.events.GLibEventLoopPolicy` is primary; `gbulb` is the fallback for older PyGObject only (`src/grc_agent/event_loop.py`). The website says `gbulb` throughout — stale.
- Tool names: current surface is consolidated `grc_interaction` (start/stop/log/measure/plot/canvas) + planner `get_run_log`. Older `run_flowgraph` / `get_run_log`-as-primary names on the site and blog are superseded.
- Provider list: hot-swappable and growing — do not state an exact count.
- `save_block` destination: `~/.grc_gnuradio` (`src/grc_agent/adapter/block_library.py`).
