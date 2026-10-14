"""Realistic one-shot scenario specifications for GRC Agent trajectory collection.

Each scenario defines:
- `id`: Deterministic scenario identifier
- `category`: Operational category (block_addition, param_update, catalog_grounding, guardrail_recovery, multi_step_rewire)
- `name`: Human-readable scenario title
- `fixture`: Initial `.grc` flowgraph path (or None for empty canvas)
- `prompt`: Natural single-turn user instruction
- `expected_tools`: Expected intermediate/target tools exercised in the trajectory
- `expects_guardrail`: Whether the scenario is designed to exercise a guardrail (`ModelRetry` or `ToolFailed`)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScenarioSpec:
    id: str
    category: str
    name: str
    fixture: str | None
    prompt: str
    expected_tools: tuple[str, ...]
    expects_guardrail: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "name": self.name,
            "fixture": self.fixture,
            "prompt": self.prompt,
            "expected_tools": list(self.expected_tools),
            "expects_guardrail": self.expects_guardrail,
        }


BENCHMARK_SCENARIOS: tuple[ScenarioSpec, ...] = (
    ScenarioSpec(
        id="01_add_cosine_source",
        category="block_addition",
        name="Add Cosine Signal Source with Frequency & Amplitude and Mix into Adder",
        fixture="tests/data/dial_tone.grc",
        prompt=(
            "Add a float cosine signal source block named `tone_880` with frequency `880` "
            "and amplitude `0.2` running at `samp_rate`, and connect it as an additional "
            "input to `blocks_add_xx` so it mixes with the existing tones."
        ),
        expected_tools=("inspect_graph", "change_graph"),
    ),
    ScenarioSpec(
        id="02_change_block_param",
        category="param_update",
        name="Update Filter Cutoff/Transition Parameters and Variable Value",
        fixture="tests/data/dial_tone.grc",
        prompt=(
            "Change the cutoff frequency parameter of the low-pass filter block `lpf_0` "
            "to `1200` and its transition width to `250`, and set the variable `freq_440` "
            "value to `523.25`."
        ),
        expected_tools=("inspect_graph", "change_graph"),
    ),
    ScenarioSpec(
        id="03_lookup_and_add_agc",
        category="catalog_grounding",
        name="Catalog Lookup and Insertion of AGC2 Block Inline",
        fixture="tests/data/dial_tone.grc",
        prompt=(
            "Look up the AGC2 block in the catalog to verify its parameter keys, then insert "
            "a float AGC2 block named `level_agc` with attack rate `1e-1`, decay rate `1e-2`, "
            "and reference `0.8` inline between `blocks_add_xx` and `audio_sink`."
        ),
        expected_tools=("inspect_graph", "query_knowledge", "change_graph"),
    ),
    ScenarioSpec(
        id="04_guardrail_error_recovery",
        category="guardrail_recovery",
        name="Guardrail Error Recovery (Port Mismatch ModelRetry & Headless Run ToolFailed)",
        fixture="tests/data/dial_tone.grc",
        prompt=(
            "First, call `change_graph` immediately (before inspecting) to connect "
            "`analog_sig_source_x_0:0` to port `3` of `blocks_add_xx` (`blocks_add_xx:3`). "
            "When the guardrail reports why that port connection failed, inspect the graph, "
            "update `blocks_add_xx` so port `3` exists and wire `analog_sig_source_x_0:0` to it, "
            "and then try starting the flowgraph with `run_flowgraph`."
        ),
        expected_tools=("change_graph", "inspect_graph", "run_flowgraph"),
        expects_guardrail=True,
    ),
    ScenarioSpec(
        id="05_rewire_and_generate_python",
        category="multi_step_rewire",
        name="Noise Path Removal, Adder Input Adjustment, Validation, and Python Code Preview",
        fixture="tests/data/dial_tone.grc",
        prompt=(
            "Disconnect `analog_noise_source_x_0` from `blocks_add_xx`, disable "
            "`analog_noise_source_x_0` so the dial tone is noise-free, update `blocks_add_xx` "
            "so it has no unconnected input ports, verify the graph is valid, and generate "
            "the Python code preview for the updated flowgraph."
        ),
        expected_tools=("inspect_graph", "change_graph", "generate_python"),
    ),
)
