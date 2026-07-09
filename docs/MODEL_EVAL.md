# Model Eval: live agent-flow scenario suite

Living tracker of pass-rate results for the 21-scenario live-model suite
(`tests/agent_flow/run_agent_flow.py`) across different backends/models. Each
row is one full run of all 21 scenarios; per-scenario failure reasons are
recorded so a regression (or a genuine fix) is traceable to evidence, not
just a headline number, per AGENTS.md "Evidence before assertions."

## How to reproduce a row

Local Ollama model:

```
OLLAMA_MODEL="<model:tag>" uv run python tests/agent_flow/run_agent_flow.py --provider ollama
```

OpenRouter (reads `OPENROUTER_MODEL`/`OPENROUTER_API_KEY` from `.env`):

```
uv run python tests/agent_flow/run_agent_flow.py --provider openrouter
```

Add `--runs N` to repeat every scenario N times and get a per-scenario
pass-rate instead of a single pass/fail (results are stochastic — always
prefer this for anything you plan to draw a conclusion from). Results land in
`tests/output/agent_flow/` (gitignored scratch, cleared between sessions —
copy out anything you want to keep before rerunning).

The pytest-gated version (`GRC_AGENT_LIVE_MODEL=1 pytest tests/test_agent_flow_live.py`)
runs the same 21 scenarios against the Ollama default only — use the
standalone CLI above for anything other than the default model/backend.

## Results

Fix-state markers (see `docs/CHANGELOG.md` / `docs/BACKLOG.md` for detail):

- **baseline** — before this session's reliability-investigation fixes.
- **fixes-r1** — catalog bracket notation + `param_filter` `hide=='none'` +
  `inspect_graph` `block_id` pairing on `block_not_found` errors.
- **fixes-full** — fixes-r1 plus: `max_tokens`/`max_tool_rounds` deleted
  entirely, native-derived `port_count_controlling_params()`, `change_graph`
  empty-batch rejection, category-based stuck-loop detector, garbled-tool-name
  diagnosis, and the "narration isn't execution" system-prompt line.

| Model | Backend | Fix-state | Pass rate | Notes |
|---|---|---|---|---|
| `gemma4:e4b-it-qat-120k` | Ollama (default) | baseline (pre-session, old instance-ID-quoting prompts) | 16/21 | Documented historical baseline. |
| `gemma4:e4b-it-qat-120k` | Ollama (default) | baseline (humanized prompts, no reliability fixes yet) | 14/21 | Failures: 04, 06, 11, 16, 18, 20, 21. |
| `gemma4:e4b-it-qat-120k` | Ollama (default) | fixes-r1 | 17/21 | Failures: 06, 14, 20, 21. Not yet rerun under fixes-full. |
| `laguna-xs-2.1:q4_K_M` | Ollama | fixes-r1 | 20/21 | Failure: 21 (135 turns, 57 `change_graph` calls before safety-ceiling — the outlier run that motivated fixes-full). |
| `laguna-xs-2.1:q4_K_M` | Ollama | fixes-full | 19/21 | Failures: 06, 21. Scenario 21 dropped to 13-14 turns before stopping (vs. 135) — stuck-loop fix confirmed working. |
| `deepseek/deepseek-v4-flash` | OpenRouter | fixes-full | **21/21** | Clean sweep, 4-7 turns/scenario, 0 safety-ceiling hits. Confirms remaining local-model failures are capability limits, not harness bugs. |
| `ornith:35b-q4_K_M` | Ollama | fixes-full | 17/21 | Failures: 04 (composed `'ampl * gain_value'` instead of replacing with `'gain_value'`), 05 (safety-ceiling after a degenerate empty-response retry), 19 & 20 (0/N `change_graph` calls succeeded at all). |
| `north-mini-code-1.0:q4_K_M` | Ollama | fixes-full | 18/21 | Failures: 12 (0/1 `change_graph` calls succeeded — never created either throttle block), 20 (30 turns, safety-ceiling on the 11-step batch), 21 (22 turns, safety-ceiling, invalid graph). |
| `qwen3.6:35b-a3b-q4_K_M` | Ollama | fixes-full | **21/21** | Clean sweep — best local-model result of the session, matching OpenRouter's deepseek. Cleared scenarios 20 and 21 (the two hardest) in 6 and 5 turns respectively, 0 safety-ceiling hits. 3 transient "degenerate empty response" retries (01, 10, 17), all recovered cleanly. |

## Known hard scenarios

`21_type_conversion_and_conjugate` (7-step multi-block rewire: float→complex
conversion, imaginary-input synthesis, resampler/spectrum rewiring, conjugate
insertion) has been the single most model-discriminating scenario in the
suite — every weaker local model has failed it at least once, while
`deepseek-v4-flash` (OpenRouter) and `qwen3.6:35b-a3b-q4_K_M` (local, MoE
with only ~3B active params) both clear it cleanly in 5 turns. So it's a
genuine capability differentiator, not an unwinnable scenario. `06_query_
knowledge_multiply` and `20_multi_change_challenge` (an 11-step batch) are
the next hardest, both requiring sustained multi-part correctness in a
single `change_graph` batch. These are tracked as capability-ceiling
patterns per model in `docs/BACKLOG.md`, not chased with scenario-specific
prompt hacks per AGENTS.md's "no ad-hoc heuristics" rule.

## Benign noise to ignore

A native GRC traceback (`ERROR:gnuradio.grc.core.FlowGraph:Failed to
evaluate variable block ... NameError: name 'value' is not defined`) has
been observed printed mid-run on more than one model/scenario (e.g.
`deepseek-v4-flash` on scenario 17, `north-mini-code-1.0` on scenario 20)
without causing a failure. It's native GRC's own variable-evaluation
logging firing on a transient state during a multi-block batch add (a
variable referencing another before both are committed in evaluation
order) — self-resolves once the batch finishes, not something this app's
code produces, and not evidence of a real problem on its own.
