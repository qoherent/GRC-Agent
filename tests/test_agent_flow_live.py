"""Live-model agent flow tests — gated behind GRC_AGENT_LIVE_MODEL=1.

Each scenario runs the full autonomous tool loop (inspect → plan → change)
against a live Ollama model and asserts the **expect-based** outcome: the
resulting graph state (native ``is_valid()`` + block presence/params/states)
must match the scenario's ``expect`` block. This is evidence-based
verification — a force-commit that leaves the graph wrong is a failure, not a
pass. Success is NEVER read from a tool's ``ok`` flag.

The scenarios + ``expect`` predicates live in
``tests/agent_flow/run_agent_flow.py`` (single source of truth); this test
imports them so the live gate and the standalone harness cannot drift apart.

These tests require:
  * Ollama running at ``GRC_AGENT_LLAMA_SERVER_URL`` (default localhost:11434).
  * The model ``GRC_AGENT_LIVE_MODEL`` pulled (defaults to ``gemma4:e4b-it-qat-120k``).

This module is **opt-in** (``GRC_AGENT_LIVE_MODEL=1``) and is NOT part of the
default CI gate — it never alters the test-gate table. Run with::

    GRC_AGENT_LIVE_MODEL=1 uv run pytest tests/test_agent_flow_live.py -v
"""

from __future__ import annotations

import logging
import os
import unittest
from pathlib import Path

LIVE = os.environ.get("GRC_AGENT_LIVE_MODEL") == "1"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

OUT_DIR = Path(__file__).resolve().parent / "output" / "agent_flow"


@unittest.skipUnless(LIVE, "set GRC_AGENT_LIVE_MODEL=1 to run live-model agent flow tests")
class AgentFlowLiveTests(unittest.TestCase):
    """End-to-end agent flow tests using a live Ollama model.

    Asserts the expect-based verdict (graph-state match via native is_valid),
    not merely that the loop ran. Uses ``subTest`` so every scenario reports
    independently and newly-added scenarios are covered automatically.
    """

    @classmethod
    def setUpClass(cls) -> None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        # The harness is a tracked module under tests/agent_flow/; the lazy
        # import keeps this module collectable even if its deps are unavailable.
        from tests.agent_flow.run_agent_flow import (
            SCENARIOS,
            _extract_metrics,
            _fresh_agent,  # noqa: F401  (re-exported for external callers)
            _render_md,
            _run_scenario,
            write_metrics_outputs,
        )

        cls.SCENARIOS = SCENARIOS
        cls._run_scenario = staticmethod(_run_scenario)  # type: ignore[assignment]
        cls._render_md = staticmethod(_render_md)  # type: ignore[assignment]
        cls._extract_metrics = staticmethod(_extract_metrics)  # type: ignore[assignment]
        cls._write_metrics_outputs = staticmethod(write_metrics_outputs)  # type: ignore[assignment]
        cls._collected_metrics: list[dict] = []

    def _save_and_assert(self, rec: dict) -> dict:
        md = type(self)._render_md(rec)
        out = OUT_DIR / f"{rec['name']}.md"
        out.write_text(md, encoding="utf-8")

        events = rec["events"]
        n_tools = sum(1 for e in events if e.get("role") == "tool_model")
        n_assistant = sum(1 for e in events if e.get("role") == "assistant_model")

        # Collect metrics BEFORE asserting. An assertion raise (a FAILED
        # scenario) must not silently drop the scenario from METRICS.md —
        # failures are the whole point of the report, so they are captured
        # first and always written by tearDownClass.
        metrics = type(self)._extract_metrics(rec)
        type(self)._collected_metrics.append(metrics)

        # Crash guard: the loop must have actually run.
        self.assertGreater(n_tools, 0, f"no tool calls: {rec['name']}")
        self.assertGreater(n_assistant, 0, f"no assistant turns: {rec['name']}")

        # The real assertion: the expect-based verdict on the resulting graph.
        self.assertTrue(
            metrics["semantic_success"],
            f"{rec['name']} FAILED expect: {metrics['expect_reason']} "
            f"(graph_valid={metrics['graph_valid']})",
        )
        logger.info(
            "Saved %s (%d turns, %d tools, valid=%s)",
            out.name,
            n_assistant,
            n_tools,
            metrics["graph_valid"],
        )
        return metrics

    def test_all_scenarios(self) -> None:
        """Run every scenario in the shared SCENARIOS list and assert each."""
        for sc in type(self).SCENARIOS:
            with self.subTest(scenario=sc["name"]):
                rec = type(self)._run_scenario(**sc)
                self._save_and_assert(rec)

    @classmethod
    def tearDownClass(cls) -> None:
        """Write METRICS.md + metrics.json so a pytest live run leaves fresh
        artifacts (parity with the standalone harness — same shared writer)."""
        if cls._collected_metrics:
            cls._write_metrics_outputs(cls._collected_metrics, OUT_DIR)


if __name__ == "__main__":
    unittest.main()
