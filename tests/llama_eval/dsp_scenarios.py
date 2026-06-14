"""Parameterized DSP scenario generators for the fuzzing gauntlet.

Each generator produces a ``LiveScenario`` with:
- ``fuzzed_variables`` for ``fuzz_fixture`` (ruamel.yaml round-trip safe)
- A prompt with fuzzed values baked in
- Semantic checks computed from the same fuzzed values
- Budget thresholds and lint exemptions appropriate to the scenario

Every generator isolates its PRNG via ``random.Random(seed)``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from tests.llama_eval.harness import (
    DEFAULT_FIXTURE_NAME,
    LiveScenario,
    LiveTurnSpec,
)


@dataclass(frozen=True)
class FuzzedScenario:
    """One generated scenario paired with its fuzz metadata."""

    scenario: LiveScenario
    param_seed: int
    prompt_vars: dict[str, Any] = field(default_factory=dict)


# ── Shared budgets ──────────────────────────────────────────────────────────

_R0_BUDGET = {
    "max_tool_rounds": 2,
    "max_tool_calls": 3,
    "max_prompt_tokens": 10000,
}

_R1_BUDGET = {
    "max_tool_rounds": 8,
    "max_tool_calls": 15,
    "max_prompt_tokens": 25000,
}

_DSP_BUDGET = {
    "max_tool_rounds": 8,
    "max_tool_calls": 15,
    "max_prompt_tokens": 25000,
}

_MAC_BUDGET = {
    "max_tool_rounds": 8,
    "max_tool_calls": 15,
    "max_prompt_tokens": 25000,
}

# ── Fixture lint exemptions ─────────────────────────────────────────────────

_MAC_SNIFFER_EXEMPTIONS: tuple[dict[str, Any], ...] = (
    {
        "rule": "orphan_block",
        "block": "blocks_random_pdu_0",
    },
    {
        "rule": "orphan_block",
        "block": "blocks_message_strobe_0",
    },
)


# ── 1. CW Interference Notch ────────────────────────────────────────────────

NOTCH_SAMP_RATES = (48000, 96000, 2000000)
NOTCH_CENTERS = (10000, 20000, 50000, 100000)
NOTCH_BANDWIDTHS = (2000, 5000, 10000)


def generate_notch_scenarios(
    seed: int = 0,
    count: int = 5,
) -> list[FuzzedScenario]:
    """Generate CW-notch band-reject filter insertion scenarios.

    Fuzzes ``samp_rate``, center frequency, and notch bandwidth.
    The agent must compute low/high cutoff, pick the correct dtype, and
    splice the new filter in-line without ``force=true``.
    """
    rng = random.Random(seed)
    results: list[FuzzedScenario] = []

    attempts = 0
    while len(results) < count and attempts < 1000:
        attempts += 1
        case_seed = rng.randint(0, 2**31 - 1)
        sr = rng.choice(NOTCH_SAMP_RATES)
        center = rng.choice(NOTCH_CENTERS)
        bw = rng.choice(NOTCH_BANDWIDTHS)
        if center >= sr / 2:
            continue
        low = center - bw // 2
        high = center + bw // 2

        prompt = (
            f"The signal source is running at {sr} Hz. We have a strong CW "
            f"interferer centered at {center} Hz. Insert a band reject filter "
            f"to notch out the interference from {low} Hz to {high} Hz. "
            "The signal path is complex."
        )

        scenario = LiveScenario(
            category="dsp",
            name=f"notch_sr{sr}_cf{center}_bw{bw}",
            fixture_name="notch_test.grc",
            description=(
                f"Insert band reject filter: samp_rate={sr}, "
                f"center={center}, bandwidth={bw}"
            ),
            release_profile="R3_REWIRE",
            fuzzed_variables={"samp_rate": str(sr)},
            param_seed=case_seed,
            turns=(
                LiveTurnSpec(
                    prompt=prompt,
                    accept_any_tool=True,
                    semantic_checks=(
                        {"kind": "mutation"},
                        {"kind": "saved_path_valid", "path": "{after_path}"},
                        {"kind": "dirty", "value": False},
                    ),
                    **_R1_BUDGET,
                ),
            ),
        )
        results.append(FuzzedScenario(
            scenario=scenario,
            param_seed=case_seed,
            prompt_vars={
                "samp_rate": sr,
                "center_freq": center,
                "bandwidth": bw,
                "low_cutoff": low,
                "high_cutoff": high,
            },
        ))

    return results


# ── 4. QAM/PSK Modulation Upgrade ──────────────────────────────────────────

MODULATION_ORDERS = (16, 64, 256)


def generate_qam_scenarios(
    seed: int = 0,
    count: int = 5,
) -> list[FuzzedScenario]:
    """Generate QAM modulation upgrade scenarios.

    Fuzzes the modulation order. The agent must update the constellation block
    (type, const_points, sym_map) and the random source max range without
    touching connections or adding/removing blocks.
    """
    rng = random.Random(seed)
    results: list[FuzzedScenario] = []

    for _i in range(count):
        case_seed = rng.randint(0, 2**31 - 1)
        order = rng.choice(MODULATION_ORDERS)

        prompt = (
            f"We are upgrading our digital link to support a higher data rate. "
            f"Search the docs for {order}-QAM to discover the native constellation helper function. "
            f"Then, upgrade the modulation scheme from QPSK to {order}-QAM by updating parameters (do not add/remove any blocks or connections). "
            f"Make sure to update the random data source so it generates "
            f"the correct range of byte values for a {order}-QAM alphabet."
        )

        scenario = LiveScenario(
            category="dsp",
            name=f"qam_order{order}",
            fixture_name="16qam_upgrade.grc",
            description=f"QAM upgrade: QPSK -> {order}-QAM",
            release_profile="R1_SET_PARAM_ONLY",
            param_seed=case_seed,
            turns=(
                LiveTurnSpec(
                    prompt=prompt,
                    accept_any_tool=True,
                    semantic_checks=(
                        {"kind": "mutation"},
                        {"kind": "saved_path_valid", "path": "{after_path}"},
                        {"kind": "dirty", "value": False},
                    ),
                    **_R1_BUDGET,
                ),
            ),
        )
        results.append(FuzzedScenario(
            scenario=scenario,
            param_seed=case_seed,
            prompt_vars={"modulation_order": order},
        ))

    return results


# ── 5. MAC Sniffer (Message Port Routing) ───────────────────────────────────

def generate_mac_scenarios(
    seed: int = 0,
    count: int = 3,
) -> list[FuzzedScenario]:
    """Generate MAC sniffer message-port routing scenarios.

    The fixture starts disconnected. The agent must add a ``blocks_message_debug``
    block and wire ``pdus -> print_pdu`` using string-based async port IDs,
    without connecting the message strobe.
    """
    rng = random.Random(seed)
    results: list[FuzzedScenario] = []

    for _i in range(count):
        case_seed = rng.randint(0, 2**31 - 1)
        rng.random()  # consume entropy for future fuzzing dimensions

        prompt = (
            "Add a 'Message Debug' block to act as our packet sniffer. "
            "Connect the output of the Random PDU generator to the PDU "
            "print port of the Message Debug block. "
            "Do not connect the Message Strobe. "
            "Note: These are asynchronous message ports, "
            "not standard stream ports."
        )

        scenario = LiveScenario(
            category="dsp",
            name=f"mac_sniffer_{case_seed}",
            fixture_name="mac_sniffer.grc",
            description="MAC sniffer: message port routing",
            release_profile="R3_REWIRE",
            param_seed=case_seed,
            turns=(
                LiveTurnSpec(
                    prompt=prompt,
                    accept_any_tool=True,
                    semantic_checks=(
                        {"kind": "mutation"},
                        {"kind": "saved_path_valid", "path": "{after_path}"},
                        {"kind": "dirty", "value": False},
                    ),
                    lint_expected_issues=_MAC_SNIFFER_EXEMPTIONS,
                    **_MAC_BUDGET,
                ),
            ),
        )
        results.append(FuzzedScenario(
            scenario=scenario,
            param_seed=case_seed,
            prompt_vars={},
        ))

    return results


# ── 6. Mid-Tier Freeform Scenarios ──────────────────────────────────────────

MID_TIER_SAMP_RATES = (32000, 48000, 96000, 2000000)


def generate_inline_swap_scenarios(
    seed: int = 0,
    count: int = 3,
) -> list[FuzzedScenario]:
    """Generate inline block swap scenarios.

    The agent must replace ``blocks_char_to_float_0`` with a
    ``blocks_float_to_float`` (identity) block while preserving topology.
    """
    rng = random.Random(seed)
    results: list[FuzzedScenario] = []

    for _i in range(count):
        case_seed = rng.randint(0, 2**31 - 1)

        prompt = (
            "Replace the blocks_char_to_float_0 block with a "
            "blocks_float_to_float block. Remove the old block and "
            "insert the new one, keeping the same connections."
        )

        scenario = LiveScenario(
            category="dsp",
            name=f"inline_swap_{case_seed}",
            fixture_name=DEFAULT_FIXTURE_NAME,
            description="Inline block swap: char_to_float -> float_to_float",
            release_profile="R3_REWIRE",
            param_seed=case_seed,
            turns=(
                LiveTurnSpec(
                    prompt=prompt,
                    accept_any_tool=True,
                    semantic_checks=(
                        {"kind": "mutation"},
                        {"kind": "saved_path_valid", "path": "{after_path}"},
                        {"kind": "dirty", "value": False},
                    ),
                    **_DSP_BUDGET,
                ),
            ),
        )
        results.append(FuzzedScenario(
            scenario=scenario,
            param_seed=case_seed,
            prompt_vars={},
        ))

    return results


def generate_cascade_scenarios(
    seed: int = 0,
    count: int = 3,
) -> list[FuzzedScenario]:
    """Generate parameter cascade (double sample rate) scenarios.

    The agent must double `samp_rate` and propagate the change to dependent
    block parameters.
    """
    rng = random.Random(seed)
    results: list[FuzzedScenario] = []

    for _i in range(count):
        case_seed = rng.randint(0, 2**31 - 1)
        sr = rng.choice(MID_TIER_SAMP_RATES)
        doubled = sr * 2

        prompt = (
            f"The current sample rate is {sr} Hz. Double the sample rate "
            f"to {doubled} Hz. Update all dependent block parameters."
        )

        scenario = LiveScenario(
            category="dsp",
            name=f"cascade_sr{sr}_x2",
            fixture_name=DEFAULT_FIXTURE_NAME,
            description=f"Parameter cascade: {sr} -> {doubled}",
            release_profile="R1_SET_PARAM_ONLY",
            fuzzed_variables={"samp_rate": str(sr)},
            param_seed=case_seed,
            turns=(
                LiveTurnSpec(
                    prompt=prompt,
                    accept_any_tool=True,
                    semantic_checks=(
                        {"kind": "mutation"},
                        {"kind": "saved_path_valid", "path": "{after_path}"},
                        {"kind": "dirty", "value": False},
                    ),
                    **_R1_BUDGET,
                ),
            ),
        )
        results.append(FuzzedScenario(
            scenario=scenario,
            param_seed=case_seed,
            prompt_vars={
                "samp_rate": sr,
                "doubled_rate": doubled,
            },
        ))

    return results


def generate_typo_scenarios(
    seed: int = 0,
    count: int = 2,
) -> list[FuzzedScenario]:
    """Generate typo-correction (add block by synonymous name) scenarios.

    The agent must map a colloquial block name to the correct GRC block ID.
    """
    rng = random.Random(seed)
    results: list[FuzzedScenario] = []

    for _i in range(count):
        case_seed = rng.randint(0, 2**31 - 1)

        prompt = "Add an AGC block to the flowgraph."

        scenario = LiveScenario(
            category="dsp",
            name=f"typo_agc_{case_seed}",
            fixture_name=DEFAULT_FIXTURE_NAME,
            description="Typo correction: add AGC block by colloquial name",
            release_profile="R1_SET_PARAM_ONLY",
            param_seed=case_seed,
            turns=(
                LiveTurnSpec(
                    prompt=prompt,
                    accept_any_tool=True,
                    semantic_checks=(
                        {"kind": "mutation"},
                        {"kind": "saved_path_valid", "path": "{after_path}"},
                        {"kind": "dirty", "value": False},
                    ),
                    **_R1_BUDGET,
                ),
            ),
        )
        results.append(FuzzedScenario(
            scenario=scenario,
            param_seed=case_seed,
            prompt_vars={},
        ))

    return results


# ── Aggregate generator ─────────────────────────────────────────────────────

GENERATOR_REGISTRY: dict[str, callable] = {
    "notch": generate_notch_scenarios,
    "qam": generate_qam_scenarios,
    "mac": generate_mac_scenarios,
    "inline_swap": generate_inline_swap_scenarios,
    "cascade": generate_cascade_scenarios,
    "typo": generate_typo_scenarios,
}


def generate_all(seed: int = 0) -> list[FuzzedScenario]:
    """Generate one round of scenarios from every generator."""
    results: list[FuzzedScenario] = []
    for _name, gen in GENERATOR_REGISTRY.items():
        results.extend(gen(seed=seed))
    return results


def generate_all_with_count(seed: int = 0, count: int = 5) -> list[FuzzedScenario]:
    """Generate scenarios from every generator with a specific count per type."""
    results: list[FuzzedScenario] = []
    for _name, gen in GENERATOR_REGISTRY.items():
        results.extend(gen(seed=seed, count=count))
    return results


def fuzzed_to_live(fuzzed: FuzzedScenario) -> LiveScenario:
    """Extract the ``LiveScenario`` from a ``FuzzedScenario`` wrapper."""
    return fuzzed.scenario


# ── CLI entry point ─────────────────────────────────────────────────────────

def run_dsp_gauntlet():
    """Print generated scenarios for inspection (no model needed)."""
    import argparse
    parser = argparse.ArgumentParser(description="DSP Fuzzing Gauntlet")
    parser.add_argument("--seed", type=int, default=0, help="PRNG seed")
    parser.add_argument("--count", type=int, default=3, help="Scenarios per generator")
    parser.add_argument("--category", type=str, default=None, help="Filter by generator name")
    args = parser.parse_args()

    if args.category:
        gen = GENERATOR_REGISTRY.get(args.category)
        if gen is None:
            print(f"Unknown category {args.category!r}. Available: {list(GENERATOR_REGISTRY)}")
            return
        results = gen(seed=args.seed, count=args.count)
    else:
        results = generate_all_with_count(seed=args.seed, count=args.count)

    print(f"Generated {len(results)} scenarios (seed={args.seed}):")
    print()
    for fz in results:
        s = fz.scenario
        print(f"  [{s.category}] {s.name}")
        print(f"    fixture: {s.fixture_name}")
        print(f"    prompt: {s.prompt[:100]}...")
        print(f"    fuzzed vars: {s.fuzzed_variables}")
        print(f"    turns: {len(s.turns)}, budget checks: {bool(s.turns[0].max_tool_rounds)}")
        print()


if __name__ == "__main__":
    run_dsp_gauntlet()
