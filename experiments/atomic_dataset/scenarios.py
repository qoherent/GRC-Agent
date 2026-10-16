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

import datetime
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from experiments.atomic_dataset.scaffolds import NULL_WIREABLE_DTYPES
from experiments.atomic_dataset.taxonomy import (
    build_subcategory_index,
    extract_catalog_taxonomy,
    largest_remainder_alloc,
)


@dataclass(frozen=True)
class ScenarioSpec:
    id: str
    category: str
    name: str
    fixture: str | None
    prompt: str
    expected_tools: tuple[str, ...]
    expects_guardrail: bool = False
    family: str = "benchmark"
    subcategory: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "name": self.name,
            "fixture": self.fixture,
            "prompt": self.prompt,
            "expected_tools": list(self.expected_tools),
            "expects_guardrail": self.expects_guardrail,
            "family": self.family,
            "subcategory": self.subcategory,
        }


#: Tier 1 suite targets (wide-coverage atomic dataset collection plan).
TIER1_TARGETS: dict[str, int] = {
    "block_add_wire": 572,
    "param_retune": 525,
    "topology_state": 180,
    "guardrail_recovery": 120,
    "non_mutation_multi_tool": 120,
}

FAMILY_FIXTURE = "empty"  # fixture marker for the bare-canvas add/wire scenarios


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


# ==============================================================================
# Tier 1 wide-coverage suite
# ==============================================================================

#: Tier 1 targets from the wide-coverage plan. Actual counts are data-driven:
#: a seed exists only when its prerequisites are empirically satisfiable
#: (scaffold validates headless), so actuals may fall short of targets and are
#: reported in the manifest.
_TIER1_SCAFFOLD_DIR_DEFAULT = "experiments/atomic_dataset/fixtures/tier1"
_EMPTY_FIXTURE = "experiments/atomic_dataset/fixtures/tier1/t1_empty.grc"

FAMILY_ORDER = ("block_add_wire", "param_retune", "topology_state", "guardrail_recovery", "non_mutation_multi_tool")


def _port_number(port: str) -> int:
    try:
        return int(port)
    except ValueError:
        return 10_000


def _slug(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]+", "_", name).strip("_") or "x"


def _wireable_ports(record: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [p for p in record.get(kind, []) if p["domain"] == "stream" and p["dtype"] in NULL_WIREABLE_DTYPES]


def _first_numeric_param(record: dict[str, Any]) -> dict[str, Any] | None:
    for p in record["params"]:
        if p["dtype"] in ("float", "int"):
            return p
    return None


def _num(val: Any) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _retune_value(current: Any) -> str:
    """Deterministic next value for a numeric param: float -> 2x, int -> +1, else 1."""
    n = _num(current)
    if n is None:
        return "1"
    s = str(current).strip()
    if s != str(int(n)):
        return f"{n * 2:g}"
    return str(int(n) + 1)


def _inst_name(block_id: str) -> str:
    return f"{_slug(block_id)}_0"


def _add_wire_seeds(scaffold: Any) -> list[tuple[str, dict[str, str]]]:
    """Seeds: (block, input port) for every input port; (block, output port) per output port when the block has no inputs."""
    seeds: list[tuple[str, dict[str, str]]] = []
    ins = sorted(scaffold.input_ports, key=lambda p: _port_number(p["port"]))
    outs = sorted(scaffold.output_ports, key=lambda p: _port_number(p["port"]))
    if ins:
        for port in ins:
            seeds.append(("in", port))
    else:
        for port in outs:
            seeds.append(("out", port))
    return seeds


def _build_family_specs(
    subcategory: str,
    rows: list[dict[str, Any]],
    scaffolds: dict[str, Any],
    alloc_add: int,
    alloc_retune: int,
) -> list[ScenarioSpec]:
    """Build the deterministic Tier 1 ScenarioSpecs for one subcategory."""
    specs: list[ScenarioSpec] = []
    eligible_rows = [r for r in rows if r["block_id"] in scaffolds]

    # ---- block_add_wire (fixture: empty canvas; the model adds the block) ----
    add_seeds: list[tuple[dict[str, Any], tuple[str, dict[str, str]]]] = []
    for r in eligible_rows:
        for kind, port in _add_wire_seeds(scaffolds[r["block_id"]]):
            add_seeds.append((r, (kind, port)))
    for r, (kind, port) in add_seeds[:alloc_add]:
        dtype = port["dtype"]
        inst = _inst_name(r["block_id"])
        if kind == "in":
            prompt = (
                f"Add a {r['label']} block named `{inst}` to the graph. Connect input port "
                f"`{port['port']}` to a fresh {dtype} null source, wire every other input port to its "
                f"own null source of matching type, and connect every output port to a null sink of "
                f"matching type, so the flowgraph stays valid."
            )
        else:
            prompt = (
                f"Add a {r['label']} block named `{inst}` to the graph and connect its output port "
                f"`{port['port']}` to a fresh {dtype} null sink (and any remaining output ports to null "
                f"sinks of matching type), so the flowgraph stays valid."
            )
        specs.append(
            ScenarioSpec(
                id=f"t1_addwire_{_slug(r['block_id'])}_{kind}{port['port']}",
                category=subcategory,
                name=f"Add & wire {r['label']} ({kind} port {port['port']})",
                fixture=_EMPTY_FIXTURE,
                prompt=prompt,
                expected_tools=("inspect_graph", "change_graph"),
                family="block_add_wire",
                subcategory=subcategory,
            )
        )

    # ---- param_retune (fixture: block scaffold) ----
    retune_rows = [r for r in eligible_rows if _first_numeric_param(r)]
    for r in retune_rows[:alloc_retune]:
        p = _first_numeric_param(r)
        newval = _retune_value(p["default"])
        inst = scaffolds[r["block_id"]].target_instance
        specs.append(
            ScenarioSpec(
                id=f"t1_retune_{_slug(r['block_id'])}",
                category=subcategory,
                name=f"Retune {p['label']} of {r['label']}",
                fixture=scaffolds[r["block_id"]].fixture_path,
                prompt=(
                    f"Change the {p['label']} parameter (`{p['id']}`) of the {r['label']} block `{inst}` "
                    f"to `{newval}`."
                ),
                expected_tools=("inspect_graph", "change_graph"),
                family="param_retune",
                subcategory=subcategory,
            )
        )

    # ---- topology_state: 3 per subcategory (disable / remove-rewire / rename) ----
    ranked = sorted(eligible_rows, key=lambda r: (-len(r["inputs"]) - len(r["outputs"]), r["block_id"]))
    for rank, r in enumerate(ranked[:3]):
        scf = scaffolds[r["block_id"]]
        inst = scf.target_instance or _inst_name(r["block_id"])
        if rank == 0:
            op, name_suffix = "disable", "disable"
            prompt = f"Disable the {r['label']} block `{inst}` so its path is bypassed, leaving every other block and connection unchanged."
        elif rank == 1 and scf.upstream and scf.downstream and len(scf.input_ports) == 1 and len(scf.output_ports) == 1:
            op, name_suffix = "remove", "remove_rewire"
            up, upk = scf.upstream
            dn, dnk = scf.downstream
            prompt = (
                f"Remove the {r['label']} block `{inst}` from the graph and reconnect its upstream "
                f"neighbour `{up}` (port {upk}) directly to its downstream neighbour `{dn}` (port {dnk})."
            )
        else:
            op, name_suffix = "rename", "rename"
            prompt = f"Rename the {r['label']} block `{inst}` to `{inst}_renamed`, keeping its parameters and connections intact."
        specs.append(
            ScenarioSpec(
                id=f"t1_topo_{_slug(r['block_id'])}_{name_suffix}",
                category=subcategory,
                name=f"Topology: {name_suffix} {r['label']}",
                fixture=scf.fixture_path,
                prompt=prompt,
                expected_tools=("inspect_graph", "change_graph"),
                family="topology_state",
                subcategory=subcategory,
            )
        )

    # ---- guardrail_recovery: 2 per subcategory (wrong port / bad param) ----
    guard_rows = [r for r in eligible_rows if scaffolds[r["block_id"]].input_ports or _first_numeric_param(r)][:2]
    for r in guard_rows:
        scf = scaffolds[r["block_id"]]
        inst = scf.target_instance or _inst_name(r["block_id"])
        p = _first_numeric_param(r)
        if scf.input_ports:
            kmax = max(_port_number(x["port"]) for x in scf.input_ports)
            op, name_suffix = "port", "wrong_port"
            prompt = (
                f"Add a new null source and connect it to input port `{kmax + 1}` of `{inst}`. That port "
                f"does not exist; when the guardrail reports the failure, inspect the graph, and either "
                f"connect the new source to a valid unconnected input port of `{inst}` if one exists, or "
                f"remove the new source, so the graph remains valid."
            )
        elif p is not None:
            op, name_suffix = "param", "bad_param"
            prompt = (
                f"Set the parameter `{p['id']}` of `{inst}` to `not_a_number`. When the guardrail reports "
                f"the validation failure, inspect the block's current value for `{p['id']}` and set it to a "
                f"valid numeric value so the edit succeeds."
            )
        else:
            continue
        specs.append(
            ScenarioSpec(
                id=f"t1_guard_{_slug(r['block_id'])}_{op}",
                category=subcategory,
                name=f"Guardrail recovery: {op} on {r['label']}",
                fixture=scf.fixture_path,
                prompt=prompt,
                expected_tools=("change_graph", "inspect_graph"),
                expects_guardrail=True,
                family="guardrail_recovery",
                subcategory=subcategory,
            )
        )

    # ---- non_mutation_multi_tool: 2 per subcategory, any catalog block (no scaffold needed) ----
    for i, r in enumerate(rows[:2]):
        if i == 0:
            prompt = (
                f"Look up `{r['block_id']}` in the block knowledge base and report its human-readable "
                f"name, every parameter key with its type and default value, and its input and output "
                f"ports. Do not modify the flowgraph."
            )
            sid, name = f"t1_nmm_{_slug(r['block_id'])}_query", f"Knowledge lookup: {r['label']}"
            tools = ("query_knowledge",)
        else:
            prompt = (
                "Generate the Python code preview for the current flowgraph, then report how many blocks "
                "it contains and whether the graph is valid. Do not modify the flowgraph."
            )
            sid, name = f"t1_nmm_{_slug(r['block_id'])}_preview", f"Inspect & preview around {r['label']}"
            tools = ("inspect_graph", "generate_python")
        specs.append(
            ScenarioSpec(
                id=sid,
                category=subcategory,
                name=name,
                fixture=_EMPTY_FIXTURE,
                prompt=prompt,
                expected_tools=tools,
                family="non_mutation_multi_tool",
                subcategory=subcategory,
            )
        )

    return specs


def build_tier1_scenarios(
    scaffold_dir: str | Path | None = None,
    taxonomy: list[dict[str, Any]] | None = None,
) -> tuple[list[ScenarioSpec], dict[str, Any]]:
    """Generate the deterministic Tier 1 wide-coverage suite and a manifest.

    Uniform rules (no per-block special cases):
    - Seeds exist only when empirically satisfiable: the block scaffold must
      validate headless (``scaffolds.py``), so hardware/PDU/environment-bound
      blocks are excluded and the shortfall is reported, not faked.
    - block_add_wire / param_retune: proportional allocation of the plan target
      across subcategories (D'Hondt highest averages, capped at available seeds).
    - topology_state / guardrail_recovery / non_mutation: fixed per-subcategory
      counts (3 / 2 / 2) — the targets divide exactly by the 60 subcategories.
    - non_mutation scenarios need no scaffold (no mutation), so they cover all
      60 subcategories even where no block is headless-wireable.
    """
    from experiments.atomic_dataset.scaffolds import (
        ScaffoldResult,
        build_all_scaffolds,
        build_empty_scaffold,
    )

    out_dir = Path(scaffold_dir) if scaffold_dir else Path(_TIER1_SCAFFOLD_DIR_DEFAULT)
    tax = taxonomy if taxonomy is not None else extract_catalog_taxonomy()
    index = build_subcategory_index(tax)

    block_ids = sorted(r["block_id"] for rows in index.values() for r in rows)
    built = build_all_scaffolds(block_ids, out_dir)
    scaffolds = {bid: res for bid, res in built.items() if isinstance(res, ScaffoldResult) and res.eligible}
    empty = build_empty_scaffold(out_dir)
    if not empty.eligible:
        raise RuntimeError(f"empty canvas scaffold failed: {empty.reason}")

    # Proportional allocation of the two variable-target families.
    add_weights: dict[str, float] = {}
    retune_weights: dict[str, float] = {}
    for sc, rows in index.items():
        n_add = sum(len(_add_wire_seeds(scaffolds[r["block_id"]])) for r in rows if r["block_id"] in scaffolds)
        n_retune = sum(1 for r in rows if r["block_id"] in scaffolds and _first_numeric_param(r))
        add_weights[sc] = n_add
        retune_weights[sc] = n_retune
    alloc_add = largest_remainder_alloc(TIER1_TARGETS["block_add_wire"], add_weights)
    alloc_retune = largest_remainder_alloc(TIER1_TARGETS["param_retune"], retune_weights)

    all_specs: list[ScenarioSpec] = []
    per_family: dict[str, int] = {f: 0 for f in FAMILY_ORDER}
    subcat_covered: set[str] = set()
    for sc, rows in index.items():
        specs = _build_family_specs(sc, rows, scaffolds, alloc_add.get(sc, 0), alloc_retune.get(sc, 0))
        for s in specs:
            per_family[s.family] += 1
            subcat_covered.add(s.subcategory)
        all_specs.extend(specs)

    manifest = {
        "generated_at": datetime.datetime.now().isoformat(),
        "targets": TIER1_TARGETS,
        "actuals": per_family,
        "total": len(all_specs),
        "subcategories_total": len(index),
        "subcategories_covered": len(subcat_covered),
        "scaffold_dir": str(out_dir),
        "blocks_eligible": len(scaffolds),
        "blocks_total": len(block_ids),
        "blocks_ineligible": sorted(set(block_ids) - set(scaffolds)),
    }
    return all_specs, manifest
