"""Live-model agent flow tests — gated behind GRC_AGENT_LIVE_MODEL=1.

Each scenario:
  1. Loads a GRC fixture into a temp copy.
  2. Sends a user task to the agent.
  3. Lets the model run its tool loop autonomously (inspect → plan → change).
  4. Asserts minimum expectations (at least one tool call, no crash).
  5. Saves a full MD transcript to ``tests/output/agent_flow/``.

These tests require:
  * Ollama running at ``GRC_AGENT_LLAMA_SERVER_URL`` (default localhost:11434).
  * The model ``GRC_AGENT_LIVE_MODEL`` pulled (defaults to ``gemma4:e4b-it-qat``).

Run::

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
_FIXTURES = Path(__file__).resolve().parent / "data"
_FM_RX_FIXTURE = str(_FIXTURES / "fm_rx.grc")

_SCENARIOS: list[dict[str, str]] = [
    {
        "name": "01_add_throttle",
        "title": "Add a throttle block inline",
        "prompt": (
            "Inspect the current flowgraph, then add a `blocks_throttle` block"
            " between `analog_sig_source_x_0` and `blocks_add_xx`."
            " Name the new block `mid_throttle`, set `type` to `float`,"
            " and use `samp_rate` for `samples_per_second`."
            " Re-wire the connections so the throttle sits inline."
            " After the changes, inspect the result to confirm."
        ),
    },
    {
        "name": "02_update_sample_rate",
        "title": "Update the sample rate variable",
        "prompt": (
            "Inspect the current flowgraph. Then update the `samp_rate`"
            " variable to `48000`. Confirm the change by inspecting again."
        ),
    },
    {
        "name": "03_disable_and_enable",
        "title": "Disable a block, inspect, then re-enable it",
        "prompt": (
            "Inspect the current flowgraph. Then disable the block"
            " `analog_noise_source_x_0` and inspect the result."
            " Finally, re-enable it and confirm."
        ),
    },
    {
        "name": "04_add_and_remove_variable",
        "title": "Add a variable, use it, then remove it",
        "prompt": (
            "Inspect the current flowgraph. Add a new `variable` block"
            " named `gain_value` with `value` set to `2.0`."
            " Then update the `analog_sig_source_x_0` block's `amp`"
            " parameter to use `gain_value`."
            " Finally, inspect the result to confirm both changes."
        ),
    },
    {
        "name": "05_full_rewire",
        "title": "Remove a block and rewire around it",
        "prompt": (
            "Inspect the current flowgraph. Remove the `analog_noise_source_x_0`"
            " block. Then add a new `analog_const_source_x` block named"
            " `dc_offset` with `const` set to `0.0`. Connect `dc_offset`"
            " port 0 to `blocks_add_xx` port 2 (replacing the noise path)."
            " Inspect the final result to confirm the changes."
        ),
    },
    {
        "name": "06_query_knowledge_multiply",
        "title": "Discover an unknown block via query_knowledge (multiply)",
        "prompt": (
            "Inspect the current flowgraph. I want to multiply the two"
            " sinusoid sources together instead of adding them. The exact"
            " GNU Radio block_id for a signal multiplier is not something"
            " to guess: use query_knowledge (domain catalog) to look it up"
            " first, then add the block named `multiplier` with `type` set"
            " to `float`. Connect the two existing `analog_sig_source_x`"
            " outputs into the multiplier, remove the old `blocks_add_xx`,"
            " and inspect the result to confirm."
        ),
    },
    {
        "name": "07_force_disabled_connected_block",
        "title": "Disable a connected block and force-commit if invalid",
        "prompt": (
            "Inspect the current flowgraph. Disable the block"
            " `analog_sig_source_x_0`, which is connected into the adder."
            " If disabling a connected block makes the graph fail"
            " validation, use force to commit the change anyway. Then"
            " inspect the result to confirm the block is disabled."
        ),
    },
    {
        "name": "08_fm_rx_insert_throttle",
        "title": "Insert a throttle on a larger FM receiver graph",
        "fixture": _FM_RX_FIXTURE,
        "prompt": (
            "Inspect the current flowgraph (this is an FM receiver). Add a"
            " `blocks_throttle` block named `audio_throttle` with `type` set"
            " to `float` and `samples_per_second` set to `audio_rate`."
            " Insert it inline on the connection from"
            " `pfb_arb_resampler_xxx_0` to `audio_sink_0`: remove that"
            " connection, then route the resampler output through the"
            " throttle into the audio sink. Inspect the result to confirm."
        ),
    },
]


def _maybe_skip() -> None:
    if not LIVE:
        return
    # Sanity-check: can we import the Ollama-dependent modules?
    try:
        from grc_agent.agent import GrcAgent  # noqa: F401
    except ImportError as exc:
        raise unittest.SkipTest(f"grc_agent not importable: {exc}") from exc


@unittest.skipUnless(LIVE, "set GRC_AGENT_LIVE_MODEL=1 to run live-model agent flow tests")
class AgentFlowLiveTests(unittest.TestCase):
    """End-to-end agent flow tests using a live Ollama model."""

    @classmethod
    def setUpClass(cls) -> None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        from playground.agent_flow_experiment.run_agent_flow import _fresh_agent, _run_scenario

        cls._fresh_agent = staticmethod(_fresh_agent)  # type: ignore[assignment]
        cls._run_scenario = staticmethod(_run_scenario)  # type: ignore[assignment]

    def _save_and_assert(self, rec: dict) -> None:
        from playground.agent_flow_experiment.run_agent_flow import _render_md

        md = _render_md(rec)
        out = OUT_DIR / f"{rec['name']}.md"
        out.write_text(md, encoding="utf-8")

        events = rec["events"]
        n_tools = sum(1 for e in events if e.get("role") == "tool_model")
        n_assistant = sum(1 for e in events if e.get("role") == "assistant_model")

        self.assertGreater(
            n_tools, 0, f"Expected at least 1 tool call, got {n_tools}: {rec['name']}"
        )
        self.assertGreater(
            n_assistant, 0, f"Expected at least 1 assistant turn, got {n_assistant}: {rec['name']}"
        )

        logger.info("Saved %s (%d turns, %d tools)", out.name, n_assistant, n_tools)

    def test_01_add_throttle(self) -> None:
        rec = self._run_scenario(**_SCENARIOS[0])  # type: ignore[arg-type]
        self._save_and_assert(rec)

    def test_02_update_sample_rate(self) -> None:
        rec = self._run_scenario(**_SCENARIOS[1])  # type: ignore[arg-type]
        self._save_and_assert(rec)

    def test_03_disable_and_enable(self) -> None:
        rec = self._run_scenario(**_SCENARIOS[2])  # type: ignore[arg-type]
        self._save_and_assert(rec)

    def test_04_add_and_remove_variable(self) -> None:
        rec = self._run_scenario(**_SCENARIOS[3])  # type: ignore[arg-type]
        self._save_and_assert(rec)

    def test_05_full_rewire(self) -> None:
        rec = self._run_scenario(**_SCENARIOS[4])  # type: ignore[arg-type]
        self._save_and_assert(rec)

    def test_06_query_knowledge_multiply(self) -> None:
        rec = self._run_scenario(**_SCENARIOS[5])  # type: ignore[arg-type]
        self._save_and_assert(rec)

    def test_07_force_disabled_connected_block(self) -> None:
        rec = self._run_scenario(**_SCENARIOS[6])  # type: ignore[arg-type]
        self._save_and_assert(rec)

    def test_08_fm_rx_insert_throttle(self) -> None:
        rec = self._run_scenario(**_SCENARIOS[7])  # type: ignore[arg-type]
        self._save_and_assert(rec)
