"""Hermetic unit tests for the Tier 1 wide-coverage suite (no LLM calls).

Covers: proportional allocation, subcategory indexing, scaffold determinism and
validation, suite invariants (uniqueness, targets, coverage, fixtures, prompts),
and checkpoint/resume + export logic.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from experiments.atomic_dataset.run_scenarios import (
    _checkpoint_done,
    _export_jsonl,
    _load_checkpoint,
)
from experiments.atomic_dataset.scaffolds import build_scaffold, load_existing_scaffold
from experiments.atomic_dataset.scenarios import (
    TIER1_TARGETS,
    ScenarioSpec,
    build_tier1_scenarios,
)
from experiments.atomic_dataset.taxonomy import (
    build_subcategory_index,
    largest_remainder_alloc,
    subcategory_of,
)


def _suite() -> tuple[list[ScenarioSpec], dict]:
    """Build the Tier 1 suite against the repo's cached scaffold directory."""
    repo = Path(os.environ.get("GRC_REPO_ROOT", os.getcwd()))
    return build_tier1_scenarios(scaffold_dir=repo / "experiments/atomic_dataset/fixtures/tier1")


# ---------------------------------------------------------------- allocation

def test_largest_remainder_alloc_proportional_and_capped() -> None:
    # proportional, sum == target, no key exceeds its weight
    alloc = largest_remainder_alloc(10, {"a": 5, "b": 3, "c": 2})
    assert sum(alloc.values()) == 10
    assert all(alloc[k] <= int(w) for k, w in (("a", 5), ("b", 3), ("c", 2)))
    assert alloc["a"] >= alloc["b"] >= alloc["c"]
    # determinism (order-independent)
    assert alloc == largest_remainder_alloc(10, {"c": 2, "b": 3, "a": 5})
    # capped: target above total weight
    assert largest_remainder_alloc(100, {"a": 2, "b": 3}) == {"a": 2, "b": 3}
    # degenerate inputs
    assert largest_remainder_alloc(0, {"a": 5}) == {"a": 0}
    assert largest_remainder_alloc(5, {}) == {}
    assert largest_remainder_alloc(5, {"a": 0}) == {"a": 0}


def test_subcategory_index_groups_and_excludes_options() -> None:
    index = build_subcategory_index()
    assert len(index) == 60
    all_ids = [r["block_id"] for rows in index.values() for r in rows]
    assert "options" not in all_ids
    assert len(all_ids) == len(set(all_ids))
    assert subcategory_of("Core > Filters") == "Core/Filters"
    assert subcategory_of("Soapy") == "Soapy"
    assert subcategory_of("Uncategorized") == "Uncategorized"
    for rows in index.values():
        for r in rows:
            assert subcategory_of(r["category"]) in index


# ---------------------------------------------------------------- scaffolds

def test_scaffold_deterministic_and_valid(tmp_path) -> None:
    r1 = build_scaffold("low_pass_filter", tmp_path)
    r2 = build_scaffold("low_pass_filter", tmp_path)
    assert r1.eligible and r2.eligible
    b1 = (tmp_path / "t1_low_pass_filter.grc").read_bytes()
    b2 = (tmp_path / "t1_low_pass_filter.grc").read_bytes()
    assert b1 == b2
    assert r1.target_instance
    assert r1.upstream is not None and r1.downstream is not None
    assert r1.input_ports and r1.output_ports
    # cheap reuse path agrees with a fresh build
    cached = load_existing_scaffold("low_pass_filter", tmp_path)
    assert cached is not None and cached.eligible
    assert cached.target_instance == r1.target_instance
    assert cached.upstream == r1.upstream and cached.downstream == r1.downstream
    assert cached.input_ports == r1.input_ports and cached.output_ports == r1.output_ports


def test_scaffold_ineligible_for_unwireable_ports(tmp_path) -> None:
    # PDU/message-only blocks cannot be made headless-valid: uniform exclusion.
    res = build_scaffold("blocks_msg_meta_to_pair", tmp_path)
    assert not res.eligible
    assert res.reason


# ---------------------------------------------------------------- tier1 suite

def test_tier1_suite_invariants() -> None:
    specs, manifest = _suite()

    # unique, deterministic ids
    ids = [s.id for s in specs]
    assert len(ids) == len(set(ids))
    specs2, _ = _suite()
    assert [s.id for s in specs2] == ids

    # actuals never exceed targets
    for family, target in TIER1_TARGETS.items():
        actual = sum(1 for s in specs if s.family == family)
        assert actual <= target, (family, actual, target)
    assert manifest["total"] == len(specs)

    # every subcategory is covered (non-mutation scenarios cover all 60)
    assert manifest["subcategories_covered"] == manifest["subcategories_total"] == 60
    assert len({s.subcategory for s in specs}) == 60

    # fixtures exist; ids are filesystem-safe; prompts fully rendered
    for s in specs:
        assert s.fixture is None or Path(s.fixture).is_file(), s.id
        assert "/" not in s.id and " " not in s.id and len(s.id) < 120, s.id
        assert s.prompt.strip(), s.id
        assert "{" not in s.prompt and "}" not in s.prompt, s.id

    # guardrail family is flagged; every spec carries a known family
    guard = [s for s in specs if s.family == "guardrail_recovery"]
    assert guard and all(s.expects_guardrail for s in guard)
    assert all(s.family in TIER1_TARGETS for s in specs)


# ---------------------------------------------------------------- checkpointing

def test_checkpoint_resume_and_export(tmp_path) -> None:
    specs = [
        ScenarioSpec(
            id="t1_x_a",
            category="c",
            name="n",
            fixture=None,
            prompt="p",
            expected_tools=("inspect_graph",),
        ),
        ScenarioSpec(
            id="t1_x_b",
            category="c",
            name="n",
            fixture=None,
            prompt="p",
            expected_tools=("inspect_graph",),
        ),
    ]
    done_rec = {
        "scenario_id": "t1_x_a",
        "model": "m",
        "flowgraph_valid": True,
        "guardrail_events": [],
        "usage": {"input_tokens": 1, "output_tokens": 1, "reasoning_tokens": 0, "requests": 1},
        "system": "sys",
        "tools": [],
        "messages": [{"role": "user", "content": "p"}, {"role": "assistant", "content": "ok"}],
        "canonical_messages": [],
        "verification": {"ok": True, "assistant_turns": 1, "reasoning_coverage": 0.0},
    }
    (tmp_path / "t1_x_a.json").write_text(json.dumps(done_rec), encoding="utf-8")
    (tmp_path / "t1_x_b.json").write_text(
        json.dumps({**done_rec, "flowgraph_valid": False}), encoding="utf-8"
    )

    assert _checkpoint_done(_load_checkpoint(tmp_path, "t1_x_a")) is True
    assert _checkpoint_done(_load_checkpoint(tmp_path, "t1_x_b")) is False
    assert _load_checkpoint(tmp_path, "t1_x_missing") is None

    n = _export_jsonl(tmp_path, specs, "tier1.jsonl")
    assert n == 1  # only the passing checkpoint is exported
    rows = [json.loads(line) for line in (tmp_path / "tier1.jsonl").read_text().splitlines()]
    assert rows[0]["scenario_id"] == "t1_x_a"
    assert rows[0]["flowgraph_valid"] is True


def test_export_only_cli_rebuilds_jsonl(tmp_path, monkeypatch) -> None:
    """Exercise main() end-to-end without any LLM call (export-only, benchmark)."""
    import sys

    from experiments.atomic_dataset.run_scenarios import main

    repo_root = Path(os.getcwd())
    existing = repo_root / "experiments/atomic_dataset/output/01_add_cosine_source.json"
    if existing.is_file():
        (tmp_path / "01_add_cosine_source.json").write_text(existing.read_text(), encoding="utf-8")

    monkeypatch.chdir(repo_root)
    sys.argv = [
        "run_scenarios",
        "--suite", "benchmark",
        "--scenario", "01_add_cosine_source",
        "--export-only",
        "--out-dir", str(tmp_path),
    ]
    assert main() == 0
    out = tmp_path / "sft_trajectories.jsonl"
    assert out.is_file()
    if existing.is_file():
        assert len(out.read_text().strip().splitlines()) == 1
    else:
        assert out.read_text().strip() == ""
