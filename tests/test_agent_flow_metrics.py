"""Deterministic (no-model) test for the agent-flow metrics writer.

Exercises the shared ``write_metrics_outputs`` helper — the single source of
truth for ``metrics.json`` / ``METRICS.md`` — against fake scenario records so
the format/location is verified in the default CI gate without a live model or
``GRC_AGENT_LIVE_MODEL``.
"""

from __future__ import annotations

import json
from pathlib import Path


def _fake_recs() -> list[dict]:
    return [
        {
            "name": "fake_read",
            "title": "Fake read scenario",
            "model": "fake-model",
            "events": [
                {"event": "model_message", "role": "assistant_model"},
                {
                    "event": "model_message",
                    "role": "tool_model",
                    "tool_called": {"name": "query_knowledge", "args": {}},
                },
                {"event": "final", "result": {"assistant_text": "A stream tag is ..."}},
            ],
            "expect": {"mode": "read"},
            "graph_state": {"valid": True, "instance_names": [], "params": {}, "states": {}},
        },
        {
            "name": "fake_edit",
            "title": "Fake edit scenario",
            "model": "fake-model",
            "events": [
                {"event": "model_message", "role": "assistant_model"},
                {
                    "event": "model_message",
                    "role": "tool_model",
                    "tool_called": {"name": "inspect_graph", "args": {}},
                },
                {
                    "event": "model_message",
                    "role": "tool_model",
                    "tool_called": {"name": "change_graph", "args": {}},
                    "payload": {"content": [{"tool_call_result": '{"ok": true}'}]},
                },
                {"event": "final", "result": {"assistant_text": "added throttle"}},
            ],
            "expect": {"mode": "edit", "valid": True},
            "graph_state": {
                "valid": True,
                "instance_names": ["mid_throttle"],
                "params": {},
                "states": {},
            },
        },
    ]


def test_write_metrics_outputs_writes_both_files(tmp_path: Path) -> None:
    from tests.agent_flow.run_agent_flow import _extract_metrics, write_metrics_outputs

    metrics = [_extract_metrics(rec) for rec in _fake_recs()]
    assert len(metrics) == 2
    for m in metrics:
        assert m["semantic_success"]

    summary = write_metrics_outputs(metrics, tmp_path)

    metrics_path = tmp_path / "metrics.json"
    md_path = tmp_path / "METRICS.md"
    assert metrics_path.is_file()
    assert md_path.is_file()

    written = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert isinstance(written, list)
    assert len(written) == 2
    assert {m["scenario"] for m in written} == {"fake_read", "fake_edit"}
    assert written[0]["model"] == "fake-model"

    md = md_path.read_text(encoding="utf-8")
    assert isinstance(summary, str) and summary
    assert summary == md
    assert "## Expect-Based Metrics Summary" in md
    assert "fake_read" in md
    assert "fake_edit" in md
    assert "**Expect-based success:** 2/2" in md


def test_write_metrics_outputs_pass_rates_append(tmp_path: Path) -> None:
    from tests.agent_flow.run_agent_flow import _extract_metrics, write_metrics_outputs

    metrics = [_extract_metrics(rec) for rec in _fake_recs()]
    write_metrics_outputs(
        metrics, tmp_path, pass_rates={"fake_read": "3/3", "fake_edit": "2/3"}
    )
    md = (tmp_path / "METRICS.md").read_text(encoding="utf-8")
    assert "**Pass-rate (k/N):**" in md
    assert "fake_read: 3/3" in md
    assert "fake_edit: 2/3" in md


def _fake_fail_rec() -> dict:
    """An edit scenario whose goal was NOT reached (expected block absent)."""
    return {
        "name": "fake_fail",
        "title": "Fake failing edit",
        "model": "fake-model",
        "events": [
            {"event": "model_message", "role": "assistant_model"},
            {
                "event": "model_message",
                "role": "tool_model",
                "tool_called": {"name": "change_graph", "args": {}},
            },
            {"event": "final", "result": {"assistant_text": "done"}},
        ],
        "expect": {"mode": "edit", "blocks_present": ["missing_block"], "valid": True},
        "graph_state": {"valid": True, "instance_names": ["other"], "params": {}, "states": {}},
    }


def test_write_metrics_outputs_renders_failures(tmp_path: Path) -> None:
    """A FAILED scenario must appear in METRICS.md as ✗ and count in the
    denominator — never be silently dropped. Regression for the collection
    bug where an assertion raise skipped the metrics append, hiding failures."""
    from tests.agent_flow.run_agent_flow import _extract_metrics, write_metrics_outputs

    metrics = [_extract_metrics(_fake_fail_rec())]
    assert metrics[0]["semantic_success"] is False
    assert "missing block missing_block" in metrics[0]["expect_reason"]

    write_metrics_outputs(metrics, tmp_path)
    md = (tmp_path / "METRICS.md").read_text(encoding="utf-8")
    assert "fake_fail" in md
    assert "✗" in md
    assert "**Expect-based success:** 0/1" in md
