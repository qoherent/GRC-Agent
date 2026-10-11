---
name: self-improving-harness
description: Design and recursively improve an LLM agent harness while resisting benchmark overfitting, context bloat, and unnecessary complexity. Uses model/task-aware runtime components, trace-driven diagnosis, sparse attributable edits, leakage screening, noise- and cost-aware promotion, pruning, rollback, and protected transfer audits.
---

# Self-Improving Agent Harness

Use this skill when an agent should improve the **harness around a fixed backbone model** over repeated tasks.

## Goal

Improve unseen-task success, reliability, and efficiency while keeping every persistent mechanism attributable, removable, and rollbackable.

A harness includes:

`prompt | control_flow | config | output_plumbing | context_mgmt | client_tool | skill | memory | subagent`

Do **not** make model weights, evaluators, protected held-out/OOD data, permissions, or safety boundaries self-editable.

## Evidence basis

This skill combines three complementary findings:

- **RRSI:** self-improvement generalizes better when proposal and selection are regularized: sparse edits, evidence-aware history, structured exploration, leakage screening, noise-aware selection, cost-aware acceptance, and structural pruning.
- **Empirical harness design:** planning, action space, and context management have conditional effects that depend on model capability, task type, and context budget; no component should be assumed universally beneficial.
- **Scientific context budgeting:** broad always-on instruction profiles can consume budget without improving task accuracy; load only guidance that changes an action, check, tool choice, or completion criterion.

Treat coding-specific findings as strong design priors for coding agents, not universal laws for every agent domain.

# I. Build a strong, measurable base harness

## 1. Keep components modular

Keep independently editable modules for:

- prompts / policies
- planning and stop logic
- action/tool interfaces
- retries and recovery
- context management
- skills and memory
- subagents
- output validation / plumbing

A failure should map to a component, and an edit should state which component it changes.

## 2. Put deterministic behavior in deterministic layers

Prefer:

- **code/control flow** for sequencing, guards, retries, stop conditions, and validation
- **tool schemas** for invocation contracts and side effects
- **context management** for what is retained, elided, summarized, retrieved, or cached
- **skills** for reusable procedures
- **memory** for durable state/facts
- **prompts** for judgment that cannot be enforced mechanically
- **subagents** only when context or work separation is useful

If code can enforce a requirement, do not repeatedly remind the model about it in the prompt.

## 3. Use capability-aware runtime policies

### Context management: cheap compaction first

Use context management primarily when trajectories approach the usable context limit.

Default policy:

1. keep the system/task preamble and recent turns verbatim
2. above a **soft threshold**, elide bulky stale tool outputs into short stubs
3. above a **hard threshold**, summarize the oldest middle-region history
4. preserve enough headroom for future tool output, reasoning, and the final answer

Do not add lossless recall machinery by default. Add it only when important observations are expensive or impossible to reproduce **and actual traces show the agent retrieves them**. Otherwise prefer re-reading/re-running or simple elision.

### Planning: conditional, not mandatory

Use a lightweight persistent plan when traces show:

- premature abandonment
- repeated loss of task structure
- failure to reach editing/execution
- redundant re-localization

For stronger agents, evaluate planning mainly as an **efficiency mechanism**: it may reduce repeated verification or wandering without improving accuracy. Remove or simplify it if it only adds overhead.

### Action space: match model and task

Use structured tools when the model benefits from scaffolding, typed arguments, state tracking, or automatic validation.

Use a smaller/general interface when the model is already proficient with it and can combine operations efficiently.

Do not infer tool preference from model size alone. Measure:

- invalid/unresolved tool calls
- failure before productive action
- calls per task
- cost per task
- success by task type

### Deterministic safety and recovery

For coding/workspace agents, good default mechanisms include:

- path/workspace guards
- read-before-write and stale-file checks
- permission classification
- tool errors returned as observations instead of crashing the loop
- fast post-edit diagnostics
- stuck detection for repeated identical failing calls
- bounded retry/backoff
- bounded pre-completion verification

These should live in code/control flow where possible.

## 4. Load task context selectively

Build a compact task pack containing only:

- assumptions to check
- ordered procedure
- relevant tools/references
- likely failure modes
- validation requirements
- definition of done

Prefer this order:

1. task-specific skill
2. selectively retrieved domain guidance
3. generic rigor/verification guidance
4. minimal baseline

Do not load a large profession/domain profile by default. If guidance does not change an action, check, tool choice, or completion criterion, omit it.

# II. Instrument for self-improvement

## 5. Maintain explicit evolution state

Persist:

- immutable baseline `H0`
- current incumbent `Ht`
- best reliable score `S_best`
- empirical noise tolerance `delta`
- edit/candidate history
- per-component evidence
- score, reliability, and resource metrics
- protected evolve / held-out / OOD manifests
- rollback snapshots

Use the templates in `templates/`.

## 6. Measure behavior, not just final score

Track at least:

- task score / verifier result
- end-to-end success
- first-pass delivery / infrastructure failure
- input/context tokens
- output/reasoning tokens
- total policy tokens if available
- tool calls and trajectory steps
- wall time and cost
- token/time/context-limit stops
- malformed deliverables / crashes

Also track behavioral signatures useful for diagnosis:

- context overflow rate
- runs ending before productive action/edit
- time spent localizing vs acting vs verifying
- repeated identical failing actions
- invalid/unresolved tool calls
- rework / repeated edits

Do not silently remove failed runs from the denominator.

## 7. Estimate evaluation noise before evolving

Evaluate the unchanged base harness repeatedly on the evolve set and estimate `delta`, the natural score fluctuation of the same harness.

`delta` is a measurement, not a tunable acceptance knob.

Keep held-out and OOD sets invisible to candidate proposal and selection.

# III. Recursive improvement loop

## 8. Diagnose before editing

Analyze incumbent trajectories and cluster failures by reusable mechanism, for example:

- context overflow or lost state
- premature stopping / excessive verification
- bad planning or no useful plan
- wrong action-space fit
- invalid tool invocation
- failed recovery / repeated action loop
- missing procedure or retrieval
- verification / output failure
- excessive cost or trajectory length

Prefer cross-task causal patterns over one-off task details.

## 9. Propose atomic edits with hypotheses

Every atomic edit records:

- `component`
- `hypothesis`
- `source_diff`
- `failure_pattern_targeted`
- `expected_benefit`
- `expected_cost_change`
- `risks`

The rationale may mention failing tasks. The **shipped diff must encode a reusable mechanism**, not task-specific facts.

## 10. Anneal the edit budget

Limit independently attributable edits per candidate.

Practical default:

- early rounds: 3–4 coordinated edits max
- middle rounds: 2
- late rounds: 1

Later rounds should make credit assignment easier, not accumulate ever-larger bundles.

## 11. Use the full history and explore structurally

For every measured edit retain its hypothesis, diff, score delta, cost delta, decision, and rejection reason.

Rejected mechanisms are evidence. Do not keep re-testing them without materially new implementation or evidence.

If several rounds show no improvement beyond `delta`, reserve a candidate for an underexplored component such as context management, action interface, skill retrieval, memory, subagents, or output plumbing. Do not let self-improvement collapse into prompt rewriting.

## 12. Prune continuously

Mark components for deletion or simplification when they have no recent positive contribution.

Strong pruning candidates include:

- rarely used recall/retrieval machinery
- standing prompt blocks that duplicate task instructions
- benchmark-shaped logic
- expensive mechanisms with no measurable gain
- tools the model consistently ignores or misuses

The harness should not grow monotonically.

# IV. Screen, evaluate, and promote

## 13. Leakage-screen the shipped diff first

Reject persistent diffs containing:

- benchmark/task names or task-specific values
- known answers
- grader/rubric exploits
- document-genre logic unique to the evolve suite
- benchmark-specific lookup tables
- inert machinery unrelated to the stated hypothesis

Screen the **diff**, not the proposer rationale.

## 14. Evaluate components with matched comparisons

Hold the model, tasks, execution loop, verifier, and unrelated components fixed whenever possible.

Prefer paired comparisons on the same tasks. For component questions, run targeted ablations such as:

- context policy A vs B at the same window budget
- planning on vs off
- structured tools vs minimal/general interface

Measure both success and resource cost. A harness component is useful only relative to the model, task mix, and budget it is meant to serve.

## 15. Apply conservative promotion gates

For each candidate compute:

- `dScore = score(candidate) - score(incumbent)`
- `dCost = (cost(candidate) - cost(incumbent)) / cost(incumbent)`

A candidate must satisfy:

`score(candidate) >= S_best - delta`

If `dScore > delta`, extra cost must be justified by the measured gain.

If `dScore <= delta`, do not treat a small apparent gain as evidence by itself. Prefer candidates that materially reduce cost or test a genuinely new structural mechanism while still clearing the performance floor.

If no candidate is admissible, keep the incumbent.

## 16. Promote, snapshot, and log

For every accepted change:

- snapshot the prior incumbent
- log the exact diff and hypothesis
- record score/reliability/cost deltas
- update `S_best` monotonically from reliable accepted results
- keep a one-step rollback path

# V. Transfer and release

## 17. Separate development from release evaluation

Use three surfaces when feasible:

1. **Evolve** — visible to diagnosis/proposal/selection
2. **ID held-out** — similar distribution, never used for candidate selection
3. **OOD audit** — different tasks, tools, wording, environments, or verifiers

Do not inspect held-out/OOD results every round and then adapt to them.

## 18. Release for transfer, not evolve-set prestige

Prefer a smaller evolve-set gain that transfers to unseen work over a benchmark specialist.

Release reports should include:

- unseen-task score
- reliability/failure rates
- cost and trajectory length
- context-overflow rate
- component-specific behavioral changes

Rollback production regressions in success, integrity, latency/cost envelope, or output validity. Production failures should generate hypotheses for the next measured cycle, not immediate hot patches.

# VI. Autonomous controller

```text
initialize immutable H0
measure H0 repeatedly -> delta
Ht = H0
S_best = reliable_score(H0)

for round in bounded_horizon:
    traces = run(Ht, evolve_set)
    feedback = diagnose_cross_task_mechanisms(traces)

    candidates = propose_sparse_edits(
        Ht,
        feedback,
        edit_history,
        annealed_budget(round),
        unexplored_components,
        prune_targets,
    )

    candidates = leakage_screen_shipped_diffs(candidates)

    for candidate in candidates:
        score, cost, behavior = matched_evaluate(candidate, evolve_set)
        log(candidate, score, cost, behavior)

        admissible = (
            score >= S_best - delta
            and complexity_is_justified(score, cost, delta)
            and domain_guards_pass(candidate)
        )

    if admissible candidates exist:
        Ht = best_admissible_candidate
        snapshot(Ht)
        S_best = max(S_best, reliable_score(Ht))

    update_component_evidence()
    mark_unproductive_components_for_pruning()

periodically:
    audit Ht on protected held-out/OOD sets
    release only if transfer, reliability, and cost envelope are acceptable
```

# Candidate record

```yaml
round: 7
component: context_mgmt
hypothesis: "Bulky stale tool outputs are causing context pressure without helping later actions."
failure_pattern_targeted: "context overflow on long trajectories"
source_diff: "..."
expected_benefit: "fewer overflow stops with no success regression"
expected_cost_change: "lower context tokens; fewer summarizer calls"
risks:
  - "elision may hide a needed detail"
result:
  score_delta: null
  cost_delta: null
  behavior_delta: null
  accepted: null
  rejection_reason: null
```

# Compact policy

**Build modularly → measure behavior → diagnose → propose sparse edits → screen → matched-evaluate → promote conservatively → prune → audit transfer → repeat.**

For context: **cheap compaction first; load only guidance that changes behavior.**
