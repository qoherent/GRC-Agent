"""Deterministic per-block scaffold graph builder for Tier 1 scenario collection.

Every Tier 1 scenario that requires its target block to *already exist* in the
flowgraph (param-retune, topology/state, guardrail-recovery, non-mutation) runs
on a scaffold: a minimal, headless-valid ``.grc`` containing exactly one
instance of the target block, with every stream port wired to a
``blocks_null_source`` / ``blocks_null_sink`` of a matching type.

The builder applies ONE uniform rule to every catalog block (no per-block
special cases):

1. Create a fresh flowgraph and one instance of the target block.
2. Give the instance a deterministic id; give any parameter that has no
   default a deterministic value (enum -> first option; numeric -> 1,
   except ``samp_rate`` -> 32000; ``type`` params -> float if available).
3. Rewrite (evaluates port templates), then wire every stream input port to a
   fresh ``blocks_null_source`` and every stream output port to a fresh
   ``blocks_null_sink`` whose ``type`` matches the resolved port dtype.
   Ports whose resolved dtype is not wireable with null blocks (``fc32``,
   ``sc16``, ``message``, unresolved templates, ...) make the seed
   INELIGIBLE — uniformly.
4. Re-validate. Errors of the form ``name 'X' is not defined`` are fixed by
   adding a ``variable`` block ``X`` (value 32000 for ``samp_rate``, else 1),
   up to 3 passes.
5. Export via GRC's own ``grc_yaml.dump(fg.export_data())`` (the exact
   serialization the production save path uses), atomically.
6. Reload the file and require ``inspect_graph`` validation status
   ``'valid'`` — the same ground truth the harness uses. Only then is the
   seed eligible, and the recorded instance names become part of the
   scenario prompt.

Scaffolds are cached: an existing scaffold file that still validates is
reused, so repeated runs never rebuild.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grc_agent.adapter import get_platform, inspect_graph, load_flow_graph

#: Port dtypes wireable by blocks_null_source / blocks_null_sink (their
#: ``type`` param options) and the mapping onto those options.
NULL_WIREABLE_DTYPES = {
    "float": "float",
    "complex": "complex",
    "int": "int",
    "short": "short",
    "byte": "byte",
}

#: Canonical type preference order for block ``type`` params missing a value.
TYPE_PREF = ("float", "complex", "int", "short", "byte")

UNDEFINED_NAME_RE = re.compile(r"name '([^']+)' is not defined")


@dataclass
class ScaffoldResult:
    block_id: str
    eligible: bool
    fixture_path: str | None = None
    target_instance: str | None = None
    instance_names: list[str] = field(default_factory=list)
    validation: dict[str, Any] | None = None
    reason: str | None = None
    upstream: tuple[str, str] | None = None  # (block, port) feeding target port 0
    downstream: tuple[str, str] | None = None  # (block, port) consuming target port 0
    input_ports: list[dict[str, str]] = field(default_factory=list)
    output_ports: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "eligible": self.eligible,
            "fixture_path": self.fixture_path,
            "target_instance": self.target_instance,
            "instance_names": self.instance_names,
            "validation": self.validation,
            "reason": self.reason,
            "upstream": self.upstream,
            "downstream": self.downstream,
            "input_ports": self.input_ports,
            "output_ports": self.output_ports,
        }


def _snapshot_ports(snap: dict[str, Any], target: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Resolved input/output port descriptors of the target block from an inspect_graph snapshot."""
    for b in snap.get("blocks", []):
        if b.get("instance_name") == target:
            ins = [
                {"port": str(p["port_id"]), "dtype": str(p.get("dtype") or ""), "optional": str(p.get("optional", False))}
                for p in b.get("inputs", [])
            ]
            outs = [
                {"port": str(p["port_id"]), "dtype": str(p.get("dtype") or ""), "optional": str(p.get("optional", False))}
                for p in b.get("outputs", [])
            ]
            return ins, outs
    return [], []


def _neighbor_pairs(snap: dict[str, Any], target: str) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
    """Find (upstream, downstream) neighbours of the target block's port 0 via inspect_graph connections."""
    upstream: tuple[str, str] | None = None
    downstream: tuple[str, str] | None = None
    for conn in snap.get("connections", []):
        try:
            src, dst = conn.split("->", 1)
            sblk, sport = src.rsplit(":", 1)
            dblk, dport = dst.rsplit(":", 1)
        except ValueError:
            continue
        if dblk == target and dport == "0" and upstream is None:
            upstream = (sblk, sport)
        if sblk == target and sport == "0" and downstream is None:
            downstream = (dblk, dport)
    return upstream, downstream


def _slug(block_id: str) -> str:
    """Filesystem-safe slug for a GRC block key (e.g. low_pass_filter -> low_pass_filter)."""
    return re.sub(r"[^0-9A-Za-z_]+", "_", block_id).strip("_") or "block"


def _resolved_dtype(port: Any) -> str:
    """Best concrete dtype of a live port after evaluation."""
    dt = getattr(port, "dtype", "") or ""
    dt = str(dt).strip()
    if not dt or dt.startswith("${") or dt.lower() in ("auto", "none", "null"):
        return ""
    return dt


def _first_option(options: list[Any], pref: tuple[str, ...] | None) -> str | None:
    opts = [str(o) for o in options]
    if pref:
        for p in pref:
            if p in opts:
                return p
    return opts[0] if opts else None


def _param_needs_value(param: Any) -> bool:
    try:
        v = param.value
    except Exception:
        return False
    return v is None or str(v).strip() == ""


def _set_default_params(block: Any) -> None:
    """Give every visible, value-less, non-raw parameter a deterministic value.

    Also resolves ``type`` params stuck at ``auto`` to a concrete dtype
    (preferring float) so port templates can evaluate against null-block wiring.
    """
    for key in sorted(block.params):
        if key in ("id", "comment", "alias", "affinity"):
            continue
        param = block.params[key]
        options = list(param.options or [])
        try:
            current = param.value
        except Exception:
            continue
        if key.lower() == "type" and options and str(current).strip().lower() in ("auto", ""):
            val = _first_option(options, TYPE_PREF)
            if val is not None:
                param.set_value(val)
            continue
        if not _param_needs_value(param):
            continue
        if options:
            val = _first_option(options, TYPE_PREF if "type" in key.lower() else None)
            if val is not None:
                param.set_value(val)
            continue
        dtype = str(param.dtype or "")
        if key == "samp_rate" or dtype in ("real", "complex", "int", "float", "byte", "short", "unsigned"):
            param.set_value("32000" if key == "samp_rate" else "1")


def _undefined_names(errors: list[str]) -> list[str]:
    names: list[str] = []
    for err in errors:
        m = UNDEFINED_NAME_RE.search(err)
        if m:
            names.append(m.group(1))
    return sorted(set(names))


def build_scaffold(  # noqa: C901
    block_id: str,
    out_dir: str | Path,
    target_name: str | None = None,
) -> ScaffoldResult:
    """Build, persist, and headless-validate a scaffold for one catalog block."""
    platform = get_platform()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fixture = out / f"t1_{_slug(block_id)}.grc"
    inst_target = target_name or f"{_slug(block_id)}_0"

    def ineligible(reason: str) -> ScaffoldResult:
        return ScaffoldResult(block_id=block_id, eligible=False, reason=reason)

    try:
        fg = platform.make_flow_graph()
    except Exception as e:  # noqa: BLE001
        return ineligible(f"make_flow_graph failed: {e}")

    # A fresh flowgraph's options block has an empty id/title, which fails
    # validation. Give it a deterministic identity (uniform for every scaffold).
    try:
        fg.options_block.params["id"].set_value(f"{inst_target}_options")
        fg.options_block.params["title"].set_value(f"Tier1 scaffold: {block_id}")
    except Exception:  # noqa: BLE001
        pass

    target = fg.new_block(block_id)
    if target is None:
        return ineligible(f"block {block_id!r} not constructible via new_block")

    try:
        target.params["id"].set_value(inst_target)
        _set_default_params(target)
        fg.rewrite()  # resolve target port templates before reading active ports
    except Exception as e:  # noqa: BLE001
        return ineligible(f"param setup failed: {e}")

    sources: list[Any] = []
    sinks: list[Any] = []
    try:
        for port in list(target.active_sinks):
            if getattr(port, "domain", "stream") != "stream":
                continue
            dtype = _resolved_dtype(port)
            if dtype not in NULL_WIREABLE_DTYPES:
                return ineligible(
                    f"input port {port.key!r} dtype {dtype or '<unresolved>'!r} not wireable with null blocks"
                )
            ns = fg.new_block("blocks_null_source")
            ns.params["id"].set_value(f"src_{port.key}_{len(sources)}")
            ns.params["type"].set_value(NULL_WIREABLE_DTYPES[dtype])
            ns.params["vlen"].set_value(str(getattr(port, "vlen", 1) or 1))
            sources.append((port, ns))
        for port in list(target.active_sources):
            if getattr(port, "domain", "stream") != "stream":
                continue
            dtype = _resolved_dtype(port)
            if dtype not in NULL_WIREABLE_DTYPES:
                return ineligible(
                    f"output port {port.key!r} dtype {dtype or '<unresolved>'!r} not wireable with null blocks"
                )
            nk = fg.new_block("blocks_null_sink")
            nk.params["id"].set_value(f"snk_{port.key}_{len(sinks)}")
            nk.params["type"].set_value(NULL_WIREABLE_DTYPES[dtype])
            nk.params["vlen"].set_value(str(getattr(port, "vlen", 1) or 1))
            sinks.append((port, nk))
        fg.rewrite()  # evaluate port templates on target AND null blocks before wiring
        for port, ns in sources:
            fg.connect(ns.active_sources[0], port)
        for port, nk in sinks:
            fg.connect(port, nk.active_sinks[0])
        fg.rewrite()
    except Exception as e:  # noqa: BLE001
        return ineligible(f"wiring failed: {e}")

    # Validate, fixing undefined-name errors by adding variable blocks (<= 3 passes).
    for _pass in range(3):
        try:
            fg.validate()
            errors = [str(e) for e in fg.get_error_messages()]
        except Exception as e:  # noqa: BLE001
            return ineligible(f"validate failed: {e}")
        if fg.is_valid() and not errors:
            break
        undefined = _undefined_names(errors)
        if not undefined:
            return ineligible(f"invalid scaffold: {errors[:3]}")
        for name in undefined:
            try:
                if fg.get_block(name) is not None:
                    continue
            except KeyError:
                pass
            var = fg.new_block("variable")
            var.params["id"].set_value(name)
            var.params["value"].set_value("32000" if name == "samp_rate" else "1")
        fg.rewrite()
    else:
        return ineligible(f"still invalid after variable fixes: {errors[:3]}")

    # Persist with GRC's own serialization (same as the production save path).
    try:
        from gnuradio.grc.core.io import yaml as grc_yaml  # lazy: circular-import safe

        data = grc_yaml.dump(fg.export_data())
    except Exception as e:  # noqa: BLE001
        return ineligible(f"export failed: {e}")

    fd, tmp_name = tempfile.mkstemp(dir=str(out), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_name, fixture)
    except Exception:
        os.unlink(tmp_name)
        raise

    # Ground truth: reload + the same inspect_graph validation the harness uses.
    try:
        fg2 = load_flow_graph(str(fixture))
        snap = inspect_graph(fg2).get("graph", {})
        validation = snap.get("validation", {})
        names = [b["instance_name"] for b in snap.get("blocks", [])]
    except Exception as e:  # noqa: BLE001
        fixture.unlink(missing_ok=True)
        return ineligible(f"reload failed: {e}")

    if validation.get("status") != "valid":
        fixture.unlink(missing_ok=True)
        return ineligible(f"reloaded scaffold not valid: {validation.get('errors', [])[:3]}")

    target_instance = None
    for b in snap.get("blocks", []):
        if b.get("block_id") == block_id:
            target_instance = b.get("instance_name")
            break
    if not target_instance:
        fixture.unlink(missing_ok=True)
        return ineligible("target block missing from reloaded graph")

    upstream, downstream = _neighbor_pairs(snap, target_instance)
    input_ports, output_ports = _snapshot_ports(snap, target_instance)
    return ScaffoldResult(
        block_id=block_id,
        eligible=True,
        fixture_path=str(fixture),
        target_instance=target_instance,
        instance_names=names,
        validation=validation,
        upstream=upstream,
        downstream=downstream,
        input_ports=input_ports,
        output_ports=output_ports,
    )


def build_empty_scaffold(out_dir: str | Path) -> ScaffoldResult:
    """Build/reuse the bare options-only canvas used as the add/wire family fixture."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fixture = out / "t1_empty.grc"

    def ok(names: list[str], validation: dict[str, Any]) -> ScaffoldResult:
        return ScaffoldResult(
            block_id="empty",
            eligible=True,
            fixture_path=str(fixture),
            target_instance=None,
            instance_names=names,
            validation=validation,
        )

    if fixture.is_file():
        try:
            fg = load_flow_graph(str(fixture))
            snap = inspect_graph(fg).get("graph", {})
            validation = snap.get("validation", {})
            if validation.get("status") == "valid":
                return ok([b["instance_name"] for b in snap.get("blocks", [])], validation)
        except Exception:  # noqa: BLE001
            pass  # cached file unreadable: rebuild below

    platform = get_platform()
    try:
        fg = platform.make_flow_graph()
        fg.options_block.params["id"].set_value("t1_empty_options")
        fg.options_block.params["title"].set_value("Tier1 empty canvas")
        fg.rewrite()
        from gnuradio.grc.core.io import yaml as grc_yaml  # lazy: circular-import safe

        fd, tmp_name = tempfile.mkstemp(dir=str(out), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(grc_yaml.dump(fg.export_data()))
            os.replace(tmp_name, fixture)
        except Exception:
            os.unlink(tmp_name)
            raise
        fg2 = load_flow_graph(str(fixture))
        snap = inspect_graph(fg2).get("graph", {})
        validation = snap.get("validation", {})
        if validation.get("status") != "valid":
            fixture.unlink(missing_ok=True)
            return ScaffoldResult(block_id="empty", eligible=False, reason=f"empty canvas invalid: {validation}")
        return ok([b["instance_name"] for b in snap.get("blocks", [])], validation)
    except Exception as e:  # noqa: BLE001
        return ScaffoldResult(block_id="empty", eligible=False, reason=f"empty canvas build failed: {e}")


def load_existing_scaffold(block_id: str, out_dir: str | Path) -> ScaffoldResult | None:
    """Cheap reuse: reload a cached scaffold and re-validate it (no rebuild)."""
    out = Path(out_dir)
    fixture = out / f"t1_{_slug(block_id)}.grc"
    if not fixture.is_file():
        return None
    try:
        fg = load_flow_graph(str(fixture))
        snap = inspect_graph(fg).get("graph", {})
        validation = snap.get("validation", {})
        target_instance = None
        for b in snap.get("blocks", []):
            if b.get("block_id") == block_id:
                target_instance = b.get("instance_name")
                break
        if validation.get("status") != "valid" or not target_instance:
            return None
        upstream, downstream = _neighbor_pairs(snap, target_instance)
        input_ports, output_ports = _snapshot_ports(snap, target_instance)
        return ScaffoldResult(
            block_id=block_id,
            eligible=True,
            fixture_path=str(fixture),
            target_instance=target_instance,
            instance_names=[b["instance_name"] for b in snap.get("blocks", [])],
            validation=validation,
            upstream=upstream,
            downstream=downstream,
            input_ports=input_ports,
            output_ports=output_ports,
        )
    except Exception:  # noqa: BLE001
        return None


def _quarantine_dir(out_dir: str | Path) -> Path:
    return Path(out_dir) / "ineligible"


def is_quarantined(block_id: str, out_dir: str | Path) -> bool:
    """A block scaffold in the `ineligible/` subdirectory stays excluded forever:
    quarantine is a persistent decision (e.g. a scaffold that passes validation
    but crashes the GRC C++ layer at run time), and the suite builder must never
    rebuild it."""
    return (_quarantine_dir(out_dir) / f"t1_{_slug(block_id)}.grc").is_file()


def build_all_scaffolds(
    block_ids: list[str],
    out_dir: str | Path,
    skip_existing: bool = True,
) -> dict[str, ScaffoldResult]:
    """Build (or reuse) scaffolds for many blocks. Returns {block_id: ScaffoldResult}."""
    results: dict[str, ScaffoldResult] = {}
    for block_id in block_ids:
        if is_quarantined(block_id, out_dir):
            continue  # persistent exclusion — never rebuild
        if skip_existing:
            cached = load_existing_scaffold(block_id, out_dir)
            if cached is not None:
                results[block_id] = cached
                continue
        results[block_id] = build_scaffold(block_id, out_dir)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Tier 1 scaffold graphs for catalog blocks.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("experiments/atomic_dataset/fixtures/tier1"),
        help="Directory to write scaffold .grc files into",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional JSON report path (default: <out-dir>/scaffold_report.json)",
    )
    parser.add_argument("--block", action="append", default=None, help="Only build these block ids (repeatable)")
    args = parser.parse_args()

    from experiments.atomic_dataset.taxonomy import extract_catalog_taxonomy

    if args.block:
        wanted = set(args.block)
        block_ids = sorted(wanted)
    else:
        block_ids = [r["block_id"] for r in extract_catalog_taxonomy() if r["block_id"] != "options"]

    # Persistent quarantine: blocks under fixtures/tier1/ineligible/ are excluded
    # from rebuilds (and from the report's eligible set).
    block_ids = [b for b in block_ids if not is_quarantined(b, args.out_dir)]
    quarantined = [b for b in ([x.strip() for x in args.block] if args.block else sorted({r["block_id"] for r in extract_catalog_taxonomy()}))
                    if is_quarantined(b, args.out_dir)]

    results = build_all_scaffolds(block_ids, args.out_dir)
    eligible = [r for r in results.values() if r.eligible]
    ineligible = [r for r in results.values() if not r.eligible]

    report = {
        "total": len(results),
        "eligible": len(eligible),
        "ineligible": len(ineligible),
        "quarantined": quarantined,
        "ineligible_details": [
            {"block_id": r.block_id, "reason": r.reason} for r in sorted(ineligible, key=lambda r: r.block_id)
        ],
    }
    report_path = args.report or (args.out_dir / "scaffold_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"Scaffolds: {len(eligible)}/{len(results)} eligible -> {args.out_dir}")
    if ineligible:
        print(f"Ineligible ({len(ineligible)}):")
        for r in sorted(ineligible, key=lambda r: r.block_id):
            print(f"  {r.block_id}: {r.reason}")
    print(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
