# Docs RAG False-Refusal Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rescue the 2 docs queries (Q03 sample-rate, Q10 band-pass-taps) that over-refuse despite correct retrieval, without regressing the 8 currently-grounded answers.

**Architecture:** A deterministic eval harness (10-query battery → classify each answer → pass/fail) gates two experiments. Experiment A is a prompt-only reframing of `_generate_grounded_answer`; Experiment D (fallback) adds a two-call relevance gate. Only `src/grc_agent/runtime/doc_answer.py` and a new eval script change.

**Tech Stack:** Python 3.12, Ollama (gemma4:e4b-it-qat-120k via native `/api/chat`), sqlite-vec docs index, pytest, httpx.

**Spec:** `docs/superpowers/specs/2026-06-25-docs-rag-false-refusals-design.md`

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `playground/query_knowledge_experiment/eval_docs_rag.py` | Eval harness: pure classifier + 10-query runner + verdict table + exit code | Create |
| `tests/test_docs_rag_eval.py` | Unit tests for the pure classifier (no Ollama) | Create |
| `playground/query_knowledge_experiment/eval_outputs/_baseline_8grounded/` | Snapshot of the 8 currently-grounded answers (regression reference) | Create (artifact) |
| `src/grc_agent/runtime/doc_answer.py` | `_generate_grounded_answer` prompt (Experiment A) + optional relevance gate (Experiment D) | Modify |

The classifier lives in the eval script as importable module-level functions so the unit test can exercise it deterministically without touching Ollama.

---

## Task 1: Snapshot the baseline (8 grounded answers)

**Files:**
- Create dir: `playground/query_knowledge_experiment/eval_outputs/_baseline_8grounded/`

- [ ] **Step 1: Regenerate the current docs outputs (120K model)**

Run:
```bash
uv run python playground/query_knowledge_experiment/run_10_queries.py
```
Expected: 10 lines `[NN] OK   <query>  ->  NN_<slug>.md`. This is the *current* (pre-fix) behavior: Q03 and Q10 will be refusals; the other 8 grounded.

- [ ] **Step 2: Snapshot the 8 grounded answers as the regression reference**

Copy the 8 grounded result files (all except `03_*` and `10_*`) into the baseline dir:
```bash
mkdir -p playground/query_knowledge_experiment/eval_outputs/_baseline_8grounded
for f in playground/query_knowledge_experiment/results/[0-9]*.md; do
  case "$(basename "$f")" in
    03_*|10_*) ;;  # skip the 2 refusals
    *) cp "$f" playground/query_knowledge_experiment/eval_outputs/_baseline_8grounded/ ;;
  esac
done
ls playground/query_knowledge_experiment/eval_outputs/_baseline_8grounded/
```
Expected: 8 files listed (01, 02, 04, 05, 06, 07, 08, 09).

- [ ] **Step 3: Commit**

```bash
git add playground/query_knowledge_experiment/eval_outputs/_baseline_8grounded/
git commit -m "test(docs-rag): snapshot 8 grounded answers as regression baseline"
```

---

## Task 2: Eval classifier — pure function (TDD)

**Files:**
- Create: `playground/query_knowledge_experiment/eval_docs_rag.py`
- Test: `tests/test_docs_rag_eval.py`

- [ ] **Step 1: Write the failing test for the classifier**

Create `tests/test_docs_rag_eval.py`:
```python
"""Unit tests for the docs-RAG eval classifier (no Ollama required).

The classifier lives in a playground script (not a package), so load it via
importlib from its file path.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "eval_docs_rag",
    Path(__file__).resolve().parents[1]
    / "playground" / "query_knowledge_experiment" / "eval_docs_rag.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
classify_answer = _MOD.classify_answer


class TestClassifyAnswer:
    def test_refusal_string_present_is_refusal(self):
        a = "The provided documentation does not cover this."
        assert classify_answer(a, "03") == "refusal"

    def test_empty_string_is_empty(self):
        assert classify_answer("", "03") == "empty"

    def test_short_non_refusal_is_empty(self):
        assert classify_answer("Yes.", "03") == "empty"

    def test_long_no_topic_non_rescue_is_grounded(self):
        a = "Stream tags are metadata annotations attached to samples in a stream."
        assert classify_answer(a, "01") == "grounded"

    def test_rescue_query_with_topic_is_expected_grounded(self):
        a = "The sample rate determines how many samples per second are processed."
        assert classify_answer(a, "03") == "expected-grounded"

    def test_rescue_query_without_topic_is_grounded_not_expected(self):
        # Grounded but missing the expected topic token -> not expected-grounded.
        a = "This value controls how fast the engine runs in the system configuration."
        assert classify_answer(a, "03") == "grounded"

    def test_band_pass_topic_tokens_match(self):
        a = "This block generates taps for a bandpass filter design."
        assert classify_answer(a, "10") == "expected-grounded"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
uv run pytest tests/test_docs_rag_eval.py -v
```
Expected: FAIL / collection error — `eval_docs_rag.py` does not exist yet (ModuleNotFoundError / FileNotFoundError).

- [ ] **Step 3: Create the eval script with the classifier**

Create `playground/query_knowledge_experiment/eval_docs_rag.py`:
```python
"""Eval harness for the docs-RAG false-refusal fix.

Classifies each answer in the 10-query battery deterministically and exits
non-zero if any query fails its expected verdict. The classifier is a pure
function (no Ollama) so it can be unit-tested directly.

Run::

    uv run python playground/query_knowledge_experiment/eval_docs_rag.py
"""

from __future__ import annotations

REFUSAL_STRING = "the provided documentation does not cover this"
MIN_GROUNDED_CHARS = 60

# Queries that MUST reach expected-grounded (the rescue targets). Each maps
# to topic tokens; ANY token (case-insensitive) satisfies the topic check.
RESCUE_QUERIES: dict[str, tuple[str, ...]] = {
    "03": ("sample rate",),
    "10": ("band-pass", "bandpass", "filter"),
}


def classify_answer(answer: str, query_index: str) -> str:
    """Classify a docs answer. Returns one of:
    'refusal', 'empty', 'grounded', 'expected-grounded'.

    'expected-grounded' = grounded AND (for rescue queries) an expected topic
    token is present. 'empty' covers both truly empty and degenerate short
    non-refusal answers.
    """
    a = (answer or "").strip()
    if not a:
        return "empty"
    if REFUSAL_STRING in a.lower():
        return "refusal"
    if len(a) < MIN_GROUNDED_CHARS:
        return "empty"
    if query_index in RESCUE_QUERIES:
        tokens = RESCUE_QUERIES[query_index]
        if any(t in a.lower() for t in tokens):
            return "expected-grounded"
    return "grounded"


def verdict_for(query_index: str, category: str) -> bool:
    """True if the category satisfies the pass bar for this query index."""
    if query_index in RESCUE_QUERIES:
        return category == "expected-grounded"
    return category in ("grounded", "expected-grounded")
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
uv run pytest tests/test_docs_rag_eval.py -v
```
Expected: PASS — all 7 classifier tests green.

- [ ] **Step 5: Commit**

```bash
git add playground/query_knowledge_experiment/eval_docs_rag.py tests/test_docs_rag_eval.py
git commit -m "test(docs-rag): add eval classifier (pure function) + unit tests"
```

---

## Task 3: Eval runner — 10-query battery + verdict table

**Files:**
- Modify: `playground/query_knowledge_experiment/eval_docs_rag.py`

- [ ] **Step 1: Add the runner (battery, call, classify, print, exit code)**

Append to `playground/query_knowledge_experiment/eval_docs_rag.py`:
```python
import json
from datetime import datetime
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.doc_answer import ask_grc_docs
from grc_agent.session import load_grc

WORKSPACE = Path(__file__).resolve().parents[2]
FIXTURE = WORKSPACE / "tests" / "data" / "dial_tone.grc"
OUT_DIR = WORKSPACE / "playground" / "query_knowledge_experiment" / "eval_outputs"

QUERIES: list[tuple[str, str]] = [
    ("01", "What are stream tags in GNU Radio?"),
    ("02", "What is PMT in GNU Radio?"),
    ("03", "How do I choose a sample rate for a flowgraph?"),
    ("04", "What is OFDM and how is it built in GNU Radio?"),
    ("05", "What is VOLK and why does it matter for performance?"),
    ("06", "How do message ports differ from stream ports?"),
    ("07", "How do I configure an audio sink (sample rate, device name)?"),
    ("08", "What is the USRP Hardware Driver (UHD)?"),
    ("09", "How do I add a new block to GRC?"),
    ("10", "What does the band-pass filter taps block do?"),
]


def main() -> int:
    session = load_grc(FIXTURE)
    agent = GrcAgent(session=session)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = OUT_DIR / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, str, str, bool, str]] = []
    failures = 0
    for idx, question in QUERIES:
        try:
            result = ask_grc_docs(agent, question=question)
            answer = result.get("answer", "") if isinstance(result, dict) else str(result)
        except Exception as exc:  # noqa: BLE001 - eval must not abort on one query
            answer = ""
            rows.append((idx, question[:40], "error", False, f"{type(exc).__name__}: {exc}"))
            failures += 1
            continue
        category = classify_answer(answer, idx)
        ok = verdict_for(idx, category)
        if not ok:
            failures += 1
        rows.append((idx, question[:40], category, ok, answer[:80].replace("\n", " ")))
        # Persist the raw answer for review/diffing.
        (run_dir / f"{idx}.md").write_text(
            f"# Q{idx}: {question}\n\nCategory: {category}\n\n{answer}\n",
            encoding="utf-8",
        )

    print(f"\n{'idx':<4}{'category':<20}{'pass':<6}query / preview")
    print("-" * 90)
    for idx, q, category, ok, preview in rows:
        print(f"{idx:<4}{category:<20}{'OK' if ok else 'FAIL':<6}{q} | {preview}")
    print("-" * 90)
    n_pass = sum(1 for _, _, _, ok, _ in rows if ok)
    print(f"\n{n_pass}/{len(QUERIES)} verdicts pass.  Run outputs: {run_dir}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the eval against the CURRENT (pre-fix) prompt to establish the failing baseline**

Run:
```bash
uv run python playground/query_knowledge_experiment/eval_docs_rag.py
```
Expected (pre-fix): Q03 and Q10 show `refusal` / `FAIL`; the other 8 show `grounded` / `OK`. Exit code 1. This **proves the eval catches the bug** before any fix.

> If the run instead shows 10/10 pass, the bug is not reproducing — stop and re-verify before continuing.

- [ ] **Step 3: Commit**

```bash
git add playground/query_knowledge_experiment/eval_docs_rag.py
git commit -m "test(docs-rag): add 10-query eval runner (catches Q03/Q10 refusals)"
```

---

## Task 4: Experiment A — groundedness-first prompt reframing

**Files:**
- Modify: `src/grc_agent/runtime/doc_answer.py:267-275` (the `prompt = (...)` block in `_generate_grounded_answer`)

- [ ] **Step 1: Replace the prompt body**

In `src/grc_agent/runtime/doc_answer.py`, find the `prompt = (...)` assignment inside `_generate_grounded_answer` (currently lines 267-275) and replace its string content with:

```python
    prompt = (
        "You are answering a GNU Radio question. Use ONLY the documentation "
        "below. Ground every claim in the docs and cite the source file name. "
        "The sources below were retrieved as relevant to this question.\n\n"
        "Answer concisely and directly. If a specific sub-question is not "
        "addressed by the sources, say which part is not covered, but still "
        "answer what IS covered.\n\n"
        "Do not make up information. If NONE of the sources are related to "
        'the question, say exactly: "The provided documentation does not '
        'cover this."\n\n'
        f"Question: {question}\n\n"
        f"Documentation:\n{context}"
    )
```

Leave the surrounding code (context assembly, the httpx call, `options`, return) unchanged.

- [ ] **Step 2: Run the eval (Experiment A verdict)**

Run:
```bash
uv run python playground/query_knowledge_experiment/eval_docs_rag.py
```

**Decision branch:**

- If output is `10/10 verdicts pass` and exit code 0 → **Experiment A succeeded.** Go to Step 3 (review the 8 grounded answers against the baseline) then Task 6.
- If any FAIL (a rescue query still refuses, OR one of the 8 became a refusal, OR a rescue query is grounded but missing its topic token) → **Experiment A regressed.** Revert the prompt:
  ```bash
  git checkout src/grc_agent/runtime/doc_answer.py
  ```
  and proceed to Task 5 (Experiment D fallback).

- [ ] **Step 3: Review the 8 grounded answers for fabrication (manual diff)**

Compare this run's outputs against the baseline:
```bash
RUN_DIR=$(ls -td playground/query_knowledge_experiment/eval_outputs/2026* | head -1)
for n in 01 02 04 05 06 07 08 09; do
  echo "=== $n ==="
  diff <(rg -A999 "^Category:" "$RUN_DIR/$n.md" | tail -n +2) \
       <(rg -A999 "^## Concise Answer" playground/query_knowledge_experiment/eval_outputs/_baseline_8grounded/${n}_*.md | tail -n +3) \
    | head -20
done
```
Inspect for invented content (claims not supported by sources). The refusal-string check already guards against regressions to refusal; this diff guards against subtle fabrication. If any answer now fabricates → treat as a regression, revert, go to Task 5.

- [ ] **Step 4: Commit (Experiment A)**

```bash
git add src/grc_agent/runtime/doc_answer.py
git commit -m "fix(docs-rag): groundedness-first prompt rescues Q03/Q10 refusals"
```

Then skip Task 5 and go to Task 6.

---

## Task 5: Experiment D — two-call relevance gate (ONLY if A regressed)

> Precondition: Task 4 Step 2 reported a regression and the A prompt was reverted. This task adds a relevance gate that runs before answer generation.

**Files:**
- Modify: `src/grc_agent/runtime/doc_answer.py` (add `_relevance_gate` helper; re-apply A's prompt; gate `_generate_grounded_answer`)

- [ ] **Step 1: Write the failing test for the gate parser**

Append to `tests/test_docs_rag_eval.py` (or a new `tests/test_doc_answer_gate.py`):
```python
def test_gate_parse_yes():
    from grc_agent.runtime.doc_answer import _parse_gate_verdict

    assert _parse_gate_verdict("YES. The Sample_Rate.md covers it.") is True
    assert _parse_gate_verdict("No, none relate.") is False
    assert _parse_gate_verdict("maybe") is False  # unparseable -> safe NO
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
uv run pytest tests/test_docs_rag_eval.py::test_gate_parse_yes -v
```
Expected: FAIL — `_parse_gate_verdict` does not exist (AttributeError).

- [ ] **Step 3: Add the gate parser + gate call to doc_answer.py**

In `src/grc_agent/runtime/doc_answer.py`, add (near `_generate_grounded_answer`):
```python
def _parse_gate_verdict(raw: str) -> bool:
    """Parse a constrained YES/NO gate response. Unparseable -> False (safe refuse)."""
    first = (raw or "").strip().splitlines()[0].strip().upper().rstrip(".!?")
    return first.startswith("YES")


def _relevance_gate(agent: "GrcAgent", question: str, sources: list[dict]) -> bool:
    """Cheap constrained call: do the sources contain the answer? YES/NO."""
    listing = "\n".join(f"- {s['path']}" for s in sources)
    prompt = (
        "Do the sources below contain the answer to the question?\n"
        'Reply with ONLY "YES" or "NO" on the first line, then one short '
        "sentence of reasoning.\n\n"
        f"Question: {question}\n\nSources:\n{listing}"
    )
    response = httpx.post(
        f"{agent._llama_server_url.rstrip('/')}/api/chat",
        json={
            "model": agent._llama_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {"num_ctx": 8192, "num_predict": 64},
        },
        timeout=agent._llama_request_timeout_seconds,
    )
    response.raise_for_status()
    return _parse_gate_verdict(response.json()["message"]["content"])
```

Then re-apply A's prompt (Task 4 Step 1 string) to `_generate_grounded_answer`, and at the very start of `_generate_grounded_answer` (before building context) add:
```python
    if not _relevance_gate(agent, question, sources):
        return "The provided documentation does not cover this."
```

- [ ] **Step 4: Run the gate test to verify it passes**

Run:
```bash
uv run pytest tests/test_docs_rag_eval.py::test_gate_parse_yes -v
```
Expected: PASS.

- [ ] **Step 5: Run the eval (Experiment D verdict)**

Run:
```bash
uv run python playground/query_knowledge_experiment/eval_docs_rag.py
```
Expected: `10/10 verdicts pass`, exit code 0. If still failing, stop and report (D did not rescue — escalate: the strict bar may require a model-side or retrieval-side change outside this iteration's scope).

- [ ] **Step 6: Commit (Experiment D)**

```bash
git add src/grc_agent/runtime/doc_answer.py tests/test_docs_rag_eval.py
git commit -m "fix(docs-rag): two-call relevance gate rescues Q03/Q10 (Experiment D)"
```

---

## Task 6: Final verification

- [ ] **Step 1: Full default test gate + ruff**

Run:
```bash
uv run pytest -m "not grc_native and not gui and not llama_eval" -q
uv run ruff check src/ tests/
```
Expected: all green (the new `tests/test_docs_rag_eval.py` is included; no existing test touched `doc_answer.py`'s prompt text, so no regressions).

- [ ] **Step 2: Confirm the docs experiment reflects the fix**

Run:
```bash
uv run python playground/query_knowledge_experiment/run_10_queries.py
```
Expected: 10 `OK` lines; inspect `03_*.md` and `10_*.md` to confirm grounded answers (no refusal string).

- [ ] **Step 3: Commit regenerated docs outputs**

```bash
git add playground/query_knowledge_experiment/results/
git commit -m "docs(rag): regenerate docs experiment outputs (Q03/Q10 grounded)"
```

---

## Notes for the implementer

- **Never edit retrieval, the index, `ask_grc_docs`, or the wrapper.** Only `_generate_grounded_answer` (and, in D, the gate helpers).
- **The eval is the ground truth.** Single Ollama runs have minor nondeterminism; if a verdict is borderline, re-run the eval once before concluding a regression.
- **The 60-char floor and topic tokens** (`sample rate` / `band-pass|bandpass|filter`) are tunable per the spec — adjust only if the eval is clearly miscalibrated (e.g. a real grounded answer is being marked `empty`).
- **Ollama must be running** with `gemma4:e4b-it-qat-120k` + `embeddinggemma:latest` pulled, and the docs index built (Task 1 Step 1 builds it if absent).
