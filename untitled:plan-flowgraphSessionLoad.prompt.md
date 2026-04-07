## Rewritten plan

### Goal of this pass

Turn the current scaffold into a **real package**, implement the first usable `.grc` **load + summarize** path, and cover it with one focused test using `random_bit_generator.grc`.
This pass does **not** implement save, validate, mutation, or the agent loop.

---

## Workflow rules

* Use **`uv run`** for normal commands
* Use **`uv add`** / **`uv add --dev`** for dependencies
* Keep **`pyproject.toml`** authoritative
* Keep **`.python-version`** pinned to `3.12`
* Use **package imports**, not `PYTHONPATH=src`
* Keep the real package under **`src/grc_agent/`**

### Daily commands

```bash
uv run python scripts/check_env.py
uv run ruff check
uv run python -m unittest
```

---

## Phase 1 — normalize package layout

Create and use:

```text
src/grc_agent/__init__.py
src/grc_agent/models.py
src/grc_agent/flowgraph_session.py
src/grc_agent/cli.py
```

Move the current flat modules into this package and remove the old flat copies instead of keeping shims.

### Why

* stable imports
* cleaner tests
* no top-level module ambiguity
* better long-term packaging

---

## Phase 2 — align the in-memory model

Update the data model to reflect `.grc` semantics:

* `instance_name` = block instance label from `.grc` `name`
* `block_type` = GNU Radio block class id from `.grc` `id`
* `params` = preserve the nested block payload
* `Flowgraph.metadata` = all top-level sections except `blocks` and `connections`
* `Flowgraph.raw_data` = full raw parsed YAML for future round-trip safety

### Why

This preserves fidelity now and leaves room to tighten semantics later.

---

## Phase 3 — implement `FlowgraphSession.load()`

Implement `load()` with these rules:

### Required behavior

* parse YAML with `yaml.safe_load`
* require top-level YAML to be a mapping
* raise immediately on:

	* missing file
	* invalid YAML
	* non-mapping top level
* parse `blocks`
* parse `connections`
* assign `self.path`
* assign `self.flowgraph`
* set `self.is_dirty = False`

### Tolerance policy

* malformed block entries: **skip + warn later**
* malformed connection entries: **skip + warn later**
* top-level file errors: **raise**

### Scope boundary

No save, no validate, no mutation yet.

---

## Phase 4 — implement `FlowgraphSession.summarize()`

Keep `summarize()` temporary and diagnostic only.

### Output for now

* file name
* block count
* connection count
* compact block list

### Do not include yet

* detailed metadata report
* exact stable wording
* user-facing contract guarantees

---

## Phase 5 — add one dedicated fixture and unit test

Copy:

```text
workarea/random_bit_generator.grc
```

to:

```text
tests/data/random_bit_generator.grc
```

Add:

```text
tests/test_flowgraph_session.py
```

### Test style

Use stdlib **`unittest`**.

### Assert only

* load succeeds
* path is assigned
* session is clean after load
* expected block count
* expected connection count
* correct `instance_name` / `block_type` mapping
* summary contains key lines/counts

### Do not assert

* exact full summary string

---

## Phase 6 — verify

Run:

```bash
uv run python -m unittest tests.test_flowgraph_session
```

Then run a smoke check:

```bash
uv run python - <<'PY'
from grc_agent.flowgraph_session import FlowgraphSession

session = FlowgraphSession()
session.load("tests/data/random_bit_generator.grc")
print(session.summarize())
PY
```

### Success criteria

* tests pass
* imports are package-based
* summary prints file name, counts, and block list
* no `PYTHONPATH` hacks are needed

---

## Out of scope for this pass

* save
* validate with `grcc`
* mutation/editing
* richer metadata extraction
* Python block handling
* hierarchical block handling
* agent runtime / llama.cpp / OpenAI SDK

---

## Next pass after this

1. tighten metadata extraction
2. add `validate()` with `grcc`
3. then add first safe mutation: `set_param`

## Main improvement over the old draft

The biggest cleanup is:

* package migration happens first
* `uv run` is the default workflow
* no `PYTHONPATH=src`
* the pass stays strictly about **load + summarize + one test**.