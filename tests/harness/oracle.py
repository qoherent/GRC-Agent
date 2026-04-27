"""Test-only oracle: analyze catalog metadata to suggest compatible insertion candidates.

No production runtime changes. No hardcoded recipes. No blacklists.
"""

from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any

CATALOG_ROOT = Path("/usr/share/gnuradio/grc/blocks")


def load_block_meta(block_type: str) -> dict[str, Any] | None:
    f = CATALOG_ROOT / f"{block_type}.block.yml"
    if not f.exists():
        return None
    try:
        return yaml.safe_load(f.read_text())
    except Exception:
        return None


def port_meta(meta: dict) -> list[dict]:
    return meta.get("inputs", []) + meta.get("outputs", [])


def _is_source(meta: dict) -> bool:
    return not meta.get("inputs") and bool(meta.get("outputs"))


def _is_sink(meta: dict) -> bool:
    return not meta.get("outputs") and bool(meta.get("inputs"))


def _is_middle(meta: dict) -> bool:
    return bool(meta.get("inputs")) and bool(meta.get("outputs"))


def _port_matches(port: dict, domain: str | None = None, dtype: str | None = None, vlen: str | None = None) -> bool:
    if domain and port.get("domain") != domain:
        return False
    ptype = str(port.get("dtype", ""))
    if dtype and "${" in ptype:
        # template param like '${ type }' — cannot match concrete dtype, only stream domain
        return True  # assume compatible if domain matches and user will fill template
    if dtype and ptype != dtype:
        return False
    pvlen = str(port.get("vlen", "1"))
    if vlen and "${" in pvlen:
        return True  # template vlen
    if vlen and pvlen != vlen:
        return False
    return True


def _has_required_params_with_defaults(meta: dict) -> tuple[bool, list[str]]:
    params = meta.get("parameters", [])
    # "type" is the most common required param; it usually comes from catalog default filling
    # We only flag truly missing required params that will cause preflight failure
    required_missing = []
    for p in params:
        pid = p.get("id", "")
        default_v = p.get("default")
        options = p.get("options")
        if pid == "type" and default_v is None:
            # type is typically required but filled from catalog defaults by add_block tool
            pass
        elif default_v is None and not options:
            required_missing.append(pid)
    return (not required_missing, required_missing)


def find_compatible_middle_candidates(
    target_domain: str = "stream",
    target_dtype: str | None = None,
    target_vlen: str | None = None,
    exclude_hardware: bool = True,
    k: int = 10,
) -> list[dict[str, Any]]:
    """Return catalog blocks that can be inserted between two existing stream ports."""
    candidates: list[dict] = []
    for fpath in sorted(CATALOG_ROOT.glob("*.block.yml")):
        meta = yaml.safe_load(fpath.read_text())
        if not meta:
            continue
        bid = meta.get("id", fpath.stem)
        inputs = meta.get("inputs", [])
        outputs = meta.get("outputs", [])
        if not (inputs and outputs):
            continue
        if any(inp.get("domain") == "ghost" or out.get("domain") == "ghost" for inp in inputs for out in outputs):
            continue
        if exclude_hardware:
            domains = {p.get("domain", "stream") for p in inputs + outputs}
            if any(d not in ("stream", "message") for d in domains):
                continue
        if len(inputs) == 1 and len(outputs) == 1:
            inp = inputs[0]
            out = outputs[0]
            if not _port_matches(inp, domain=target_domain, dtype=target_dtype, vlen=target_vlen):
                continue
            if not _port_matches(out, domain=target_domain, dtype=target_dtype, vlen=target_vlen):
                continue
            has_defaults, missing = _has_required_params_with_defaults(meta)
            candidates.append({
                "block_type": bid,
                "summary": f"1 input, 1 output; domain={target_domain}; params with defaults OK={has_defaults}; missing={missing}",
                "reason": f"single stream middle block matching domain={target_domain}",
                "input_domain": inp.get("domain"),
                "output_domain": out.get("domain"),
                "can_default_fill": has_defaults,
                "missing_params": missing,
            })
    # Sort by simplicity: no missing params first, then alphabetically
    candidates.sort(key=lambda c: (not c["can_default_fill"], c["block_type"]))
    return candidates[:k]


def analyze_insertion_case(target_graph_path: str, target_connection: str) -> dict[str, Any]:
    """Given a graph and target connection (simple heuristic), return oracle data."""
    return {
        "target_graph": target_graph_path,
        "target_connection": target_connection,
        "note": "oracle analysis requires loaded graph metadata; use ScenarioResult.tool_chain to inspect actual selected block",
    }
