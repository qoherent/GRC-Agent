"""Deterministic engine-core tests for the agent-flow scenarios.

Each test applies a scenario's INTENDED ``change_graph`` batch directly (no
model) and asserts the scenario's ``expect`` predicate on the resulting native
graph. This isolates ENGINE capability (can the tool surface reach the goal
state?) from MODEL variance (will a stochastic local model choose the right
batch?). Marked ``grc_native`` — needs GNU Radio, never a live model.

The expect predicates mirror ``run_agent_flow._extract_metrics`` (blocks_present
/ blocks_absent / states / params / valid) but drop the model-only fields
(final_text, safety_ceiling, read-mode). Graph truth comes from
``_graph_state`` (a fresh native ``validate()`` on a reloaded fixture) — never
from a tool's ``ok`` flag.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest
from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.grc_native_adapter import load_flow_graph, render_flow_graph, validate
from grc_agent.runtime.change_graph import dispatch_flat_change_graph_batch

pytestmark = pytest.mark.grc_native

FIXTURES = Path(__file__).resolve().parent / "data"


def _temp_fixture(name: str) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="grc_engine_core_")) / Path(name).name
    shutil.copy2(FIXTURES / name, tmp)
    return tmp


def _graph_state(fixture_path: Path) -> dict[str, Any]:
    fg = load_flow_graph(fixture_path)
    valid = bool(validate(fg).native_ok)
    snap = render_flow_graph(fg)
    return {
        "valid": valid,
        "instance_names": sorted(b.instance_name for b in snap.blocks),
        "params": {b.instance_name: dict(b.params) for b in snap.blocks},
        "states": {b.instance_name: b.state for b in snap.blocks},
    }


def _assert_state(state: dict[str, Any], expect: dict[str, Any]) -> None:
    inst = set(state["instance_names"])
    for blk in expect.get("blocks_present", []):
        assert blk in inst, f"missing block {blk}"
    for blk in expect.get("blocks_absent", []):
        assert blk not in inst, f"block {blk} still present"
    if "valid" in expect:
        assert state["valid"] == bool(expect["valid"]), f"graph valid={state['valid']}"
    for name, st in expect.get("states", {}).items():
        assert str(state["states"].get(name)) == str(st), (
            f"state {name}={state['states'].get(name)!r}"
        )
    for name, pv in expect.get("params", {}).items():
        actual = state["params"].get(name, {})
        for k, v in pv.items():
            assert str(actual.get(k)) == str(v), f"param {name}.{k}={actual.get(k)!r}"


def _apply(fixture: str, batch: dict[str, Any], expect: dict[str, Any]) -> None:
    tmp = _temp_fixture(fixture)
    session = FlowgraphSession()
    session.load(str(tmp))
    agent = GrcAgent(session=session)
    result = dispatch_flat_change_graph_batch(agent, **batch)
    assert result["ok"] is True, f"batch did not commit: {result}"
    _assert_state(_graph_state(tmp), expect)


# (fixture, change_graph batch kwargs, expect predicate)
CASES = [
    pytest.param(
        "dial_tone.grc",
        {
            "add_blocks": [
                {
                    "block_id": "blocks_throttle",
                    "instance_name": "mid_throttle",
                    "params": {"type": "float", "samples_per_second": "samp_rate"},
                }
            ],
            "remove_connections": ["analog_sig_source_x_0:0->blocks_add_xx:0"],
            "add_connections": [
                "analog_sig_source_x_0:0->mid_throttle:0",
                "mid_throttle:0->blocks_add_xx:0",
            ],
        },
        {"blocks_present": ["mid_throttle"], "valid": True},
        id="01_add_throttle",
    ),
    pytest.param(
        "dial_tone.grc",
        {"update_params": [{"instance_name": "samp_rate", "params": {"value": "48000"}}]},
        {"params": {"samp_rate": {"value": "48000"}}, "valid": True},
        id="02_update_sample_rate",
    ),
    pytest.param(
        "dial_tone.grc",
        {
            "add_blocks": [
                {"block_id": "variable", "instance_name": "gain_value", "params": {"value": "2.0"}}
            ],
            "update_params": [
                {"instance_name": "analog_sig_source_x_0", "params": {"amp": "gain_value"}}
            ],
        },
        {
            "blocks_present": ["gain_value"],
            "params": {"analog_sig_source_x_0": {"amp": "gain_value"}},
            "valid": True,
        },
        id="04_add_and_remove_variable",
    ),
    pytest.param(
        "dial_tone.grc",
        {
            "remove_blocks": ["analog_noise_source_x_0"],
            "add_blocks": [
                {
                    "block_id": "analog_const_source_x",
                    "instance_name": "dc_offset",
                    "params": {"type": "float", "const": "0.0"},
                }
            ],
            "add_connections": ["dc_offset:0->blocks_add_xx:2"],
        },
        {
            "blocks_absent": ["analog_noise_source_x_0"],
            "blocks_present": ["dc_offset"],
            "valid": True,
        },
        id="05_full_rewire",
    ),
    pytest.param(
        "dial_tone.grc",
        {
            "add_blocks": [
                {
                    "block_id": "blocks_multiply_xx",
                    "instance_name": "multiplier",
                    "params": {"type": "float"},
                }
            ],
            "add_connections": [
                "analog_sig_source_x_0:0->multiplier:0",
                "analog_sig_source_x_1:0->multiplier:1",
                "multiplier:0->audio_sink:0",
            ],
            "remove_blocks": ["analog_noise_source_x_0", "blocks_add_xx"],
        },
        {"blocks_present": ["multiplier"], "blocks_absent": ["blocks_add_xx"], "valid": True},
        id="06_multiply_rewire",
    ),
    pytest.param(
        "dial_tone.grc",
        {
            "update_states": [{"instance_name": "analog_sig_source_x_0", "state": "disabled"}],
            "force": True,
        },
        {"states": {"analog_sig_source_x_0": "disabled"}},
        id="07_force_disabled_connected_block",
    ),
    pytest.param(
        "fm_rx.grc",
        {
            "add_blocks": [
                {
                    "block_id": "blocks_throttle",
                    "instance_name": "audio_throttle",
                    "params": {"type": "float", "samples_per_second": "audio_rate"},
                }
            ],
            "remove_connections": ["pfb_arb_resampler_xxx_0:0->audio_sink_0:0"],
            "add_connections": [
                "pfb_arb_resampler_xxx_0:0->audio_throttle:0",
                "audio_throttle:0->audio_sink_0:0",
            ],
        },
        {"blocks_present": ["audio_throttle"], "valid": True},
        id="08_fm_rx_insert_throttle",
    ),
    pytest.param(
        "dial_tone.grc",
        {"update_states": [{"instance_name": "analog_sig_source_x_0", "state": "bypass"}]},
        {"states": {"analog_sig_source_x_0": "bypass"}, "valid": True},
        id="10_bypass_source_block",
    ),
    pytest.param(
        "dial_tone.grc",
        {
            "add_blocks": [
                {
                    "block_id": "blocks_throttle",
                    "instance_name": "pre_throttle",
                    "params": {"type": "float", "samples_per_second": "samp_rate"},
                },
                {
                    "block_id": "blocks_throttle",
                    "instance_name": "post_throttle",
                    "params": {"type": "float", "samples_per_second": "samp_rate"},
                },
            ],
            "remove_connections": ["analog_sig_source_x_0:0->blocks_add_xx:0"],
            "add_connections": [
                "analog_sig_source_x_0:0->pre_throttle:0",
                "pre_throttle:0->post_throttle:0",
                "post_throttle:0->blocks_add_xx:0",
            ],
        },
        {"blocks_present": ["pre_throttle", "post_throttle"], "valid": True},
        id="12_multiblock_batch_chain",
    ),
    pytest.param(
        "empty.grc",
        {
            "add_blocks": [
                {
                    "block_id": "analog_sig_source_x",
                    "instance_name": "sig",
                    "params": {
                        "type": "float",
                        "freq": "1000",
                        "amp": "0.5",
                        "samp_rate": "samp_rate",
                    },
                },
                {
                    "block_id": "blocks_throttle",
                    "instance_name": "throttle",
                    "params": {"type": "float", "samples_per_second": "samp_rate"},
                },
                {
                    "block_id": "blocks_null_sink",
                    "instance_name": "sink",
                    "params": {"type": "float"},
                },
            ],
            "add_connections": ["sig:0->throttle:0", "throttle:0->sink:0"],
        },
        {"blocks_present": ["sig", "throttle", "sink"], "valid": True},
        id="14_build_chain_from_scratch",
    ),
    pytest.param(
        "broken_unconnected_sink.grc",
        {"add_connections": ["blocks_throttle_0:0->audio_sink:0"]},
        {"valid": True},
        id="15_broken_graph_diagnose_fix",
    ),
    pytest.param(
        "dial_tone.grc",
        {
            "add_blocks": [
                {
                    "block_id": "analog_sig_source_x",
                    "instance_name": "third_tone",
                    "params": {
                        "type": "float",
                        "freq": "550",
                        "amp": "ampl",
                        "samp_rate": "samp_rate",
                    },
                }
            ],
            "update_params": [{"instance_name": "blocks_add_xx", "params": {"num_inputs": "4"}}],
            "add_connections": ["third_tone:0->blocks_add_xx:3"],
        },
        {
            "blocks_present": ["third_tone"],
            "params": {"blocks_add_xx": {"num_inputs": "4"}},
            "valid": True,
        },
        id="16_expand_adder_input",
    ),
    pytest.param(
        "dial_tone.grc",
        {
            "add_blocks": [
                {
                    "block_id": "variable",
                    "instance_name": "base_freq",
                    "params": {"value": "220.0"},
                },
                {
                    "block_id": "variable",
                    "instance_name": "fifth",
                    "params": {"value": "base_freq * 1.5"},
                },
            ],
            "update_params": [
                {"instance_name": "analog_sig_source_x_0", "params": {"freq": "base_freq"}},
                {"instance_name": "analog_sig_source_x_1", "params": {"freq": "fifth"}},
            ],
        },
        {
            "blocks_present": ["base_freq", "fifth"],
            "params": {
                "analog_sig_source_x_0": {"freq": "base_freq"},
                "analog_sig_source_x_1": {"freq": "fifth"},
            },
            "valid": True,
        },
        id="17_expression_variables_chain",
    ),
    pytest.param(
        "fm_rx.grc",
        {
            "add_blocks": [
                {
                    "block_id": "qtgui_time_sink_x",
                    "instance_name": "demod_probe",
                    "params": {"type": "float", "srate": "in_rate"},
                }
            ],
            "add_connections": ["analog_quadrature_demod_cf_0:0->demod_probe:0"],
        },
        {"blocks_present": ["demod_probe"], "valid": True},
        id="19_fm_rx_add_demod_probe",
    ),
    pytest.param(
        "fm_rx.grc",
        {
            "remove_connections": [
                "analog_fm_deemph_0:0->pfb_arb_resampler_xxx_0:0",
                "analog_fm_deemph_0:0->qtgui_freq_sink_x_0:0",
                "analog_fm_deemph_0:0->qtgui_time_sink_x_0_0_0:0",
            ],
            "add_connections": [
                "analog_quadrature_demod_cf_0:0->pfb_arb_resampler_xxx_0:0",
                "analog_quadrature_demod_cf_0:0->qtgui_freq_sink_x_0:0",
                "analog_quadrature_demod_cf_0:0->qtgui_time_sink_x_0_0_0:0",
            ],
            "remove_blocks": ["analog_fm_deemph_0"],
        },
        {"blocks_absent": ["analog_fm_deemph_0"], "valid": True},
        id="18_fm_rx_bypass_deemph_stage",
    ),
    pytest.param(
        "dial_tone.grc",
        {"update_params": [{"instance_name": "samp_rate", "params": {"value": "96000"}}]},
        {"params": {"samp_rate": {"value": "96000"}}, "valid": True},
        id="11_scoped_inspect_param_update",
    ),
    pytest.param(
        "resampler_demo.grc",
        {
            "add_blocks": [
                {
                    "block_id": "blocks_float_to_complex",
                    "instance_name": "float_to_complex_converter",
                },
                {
                    "block_id": "analog_const_source_x",
                    "instance_name": "zero_imag",
                    "params": {"type": "float", "const": "0.0"},
                },
                {"block_id": "blocks_conjugate_cc", "instance_name": "signal_conjugate"},
            ],
            "remove_connections": [
                "pfb_arb_resampler_xxx_0:0->qtgui_freq_sink_x_0_0:0",
                "analog_frequency_modulator_fc_0:0->qtgui_freq_sink_x_0:0",
                "analog_frequency_modulator_fc_0:0->pfb_arb_resampler_xxx_0:0",
                "throttle:0->analog_frequency_modulator_fc_0:0",
            ],
            "add_connections": [
                "throttle:0->float_to_complex_converter:0",
                "zero_imag:0->float_to_complex_converter:1",
                "float_to_complex_converter:0->pfb_arb_resampler_xxx_0:0",
                "float_to_complex_converter:0->qtgui_freq_sink_x_0:0",
                "pfb_arb_resampler_xxx_0:0->signal_conjugate:0",
                "signal_conjugate:0->qtgui_freq_sink_x_0_0:0",
            ],
            "remove_blocks": ["analog_frequency_modulator_fc_0"],
        },
        {
            "blocks_present": ["float_to_complex_converter", "zero_imag", "signal_conjugate"],
            "blocks_absent": ["analog_frequency_modulator_fc_0"],
            "valid": True,
        },
        id="21_type_conversion_and_conjugate",
    ),
]


@pytest.mark.parametrize("fixture,batch,expect", CASES)
def test_engine_reaches_scenario_expect(
    fixture: str, batch: dict[str, Any], expect: dict[str, Any]
) -> None:
    """The engine (tool surface) can reach each scenario's goal state when the
    correct batch is applied — independent of model stochasticity."""
    _apply(fixture, batch, expect)
