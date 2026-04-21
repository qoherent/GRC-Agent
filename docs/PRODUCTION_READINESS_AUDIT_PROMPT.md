# Fresh-Eyes Production Readiness Review Prompt

You are a senior GRC/CLI systems engineer reviewing this repository with fresh eyes.
Audit the real harness, fix only real issues, strengthen tests only where the
current coverage is genuinely weak, and prove your conclusions with the real
llama.cpp + CLI path.

Do not anchor on prior reviews. Treat this prompt as scope, not evidence.

## Ground rules

- Trust source and live behavior over docs or prior summaries.
- Do not write speculative code or “cleanup” unrelated areas.
- Prefer the smallest correct fix.
- Use `uv run` for commands.
- Keep the package under `src/grc_agent/`.
- Use `unittest` unless a stronger existing pattern already owns that surface.
- Use targeted tests while iterating. The full eval sweep is slow, so run it only
  after you finish the fixes.
- If you find a real blind spot in tests or evals, add focused coverage of your
  own choosing. Do not just mirror this prompt's wording.

## Audit focus

1. **Runtime path**
   - CLI startup order
   - session load / retrieval readiness / agent construction / launcher readiness
   - redundant work on the hot path

2. **Tool contract**
   - tool ordering
   - schema validation
   - model-facing surface width
   - duplicated or stale routing assumptions

3. **State and isolation**
   - hidden cross-session coupling
   - stale snapshots shown to the model
   - history compaction correctness
   - session/retrieval/launcher state ownership

4. **Docs vs implementation**
   - stale guarantees
   - overstated eval claims
   - drift in fixture paths, commands, or runtime contract

5. **Readiness evidence**
   - whether current tests/evals justify the repo's claims
   - missing resilience, concurrency, large-graph, or workflow coverage

## Read first

Inspect at minimum:

- `README.md`
- `AGENTS.md`
- `docs/BLUEPRINT.md`
- `docs/PACKAGE_GUIDE.md`
- `docs/LLAMA_EVAL.md`
- `src/grc_agent/agent.py`
- `src/grc_agent/cli.py`
- `src/grc_agent/llama_server.py`
- `src/grc_agent/llama_launcher.py`
- `src/grc_agent/runtime_tool_validation.py`
- `src/grc_agent/retrieval/search.py`
- `src/grc_agent/flowgraph_session.py`
- `tests/llama_eval/*`
- `tests/test_agent.py`
- `tests/test_llama_launcher.py`
- `tests/test_llama_server_live.py`

## Required workflow

1. **Inspect first**
   - Read the runtime/docs/tests source of truth.
   - Build hypotheses from source, not from this prompt.

2. **Fix only real problems**
   - Hidden state / concurrency issues first
   - Then runtime correctness issues
   - Then stale docs or misleading proof language
   - Then real test/eval blind spots

3. **Use focused validation while iterating**
   - Run the smallest relevant test slice for the area you are changing.
   - If you add or change tests, make them about a real failure mode, not padding.
   - If an eval phase is weak in a real way, strengthen it. Choose the case(s)
     yourself based on what the code actually lacks.

4. **Run the standard gates after fixes**

```bash
uv run ruff check
uv run python -m unittest
```

5. **Run the full llama.cpp suite last**

```bash
uv run python -m tests.llama_eval.run_all
```

If you need phased iteration before that, rerun the affected phases at full
strength before the final `run_all`.

6. **Prove the real CLI path**

```bash
uv run grc-agent doctor
uv run grc-agent chat tests/data/random_bit_generator.grc --message "Summarize the graph."
uv run grc-agent chat tests/data/random_bit_generator.grc --message "Change samp_rate to 48000 and validate the graph."
```

Confirm whether the CLI cold-starts or reuses the local backend and whether the
tool flow succeeds end to end.

7. **Update docs**
   - Update any docs that drifted from the verified implementation or proof level.
   - Keep them concise and honest.

## What counts as done

- Real issues fixed
- Tests updated only where they needed to be
- Docs consistent with the code
- `ruff` passes
- `unittest` passes
- the full llama.cpp suite is run at the end and reviewed
- the live CLI path is verified
- you clearly state what is still not proven

## Final output format

Return a short audit report with:

1. Passed
2. Blockers
3. Non-blocking suggestions
4. Tests run
5. Real llama.cpp verification
6. Final readiness verdict

Be blunt, specific, and evidence-based.
