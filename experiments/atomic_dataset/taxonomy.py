"""GNU Radio Companion catalog taxonomy extractor and seed generator.

Introspects all non-private catalog blocks via `grc_agent.adapter.graph.get_platform()`
to produce structured block schemas and deterministic scenario seeds for scaling
one-shot harness trajectory collection across the full GRC catalog.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from grc_agent.adapter import get_platform

#: Structural block that exists exactly once per flowgraph; never a scenario seed.
NON_SEED_BLOCKS = {"options"}


def subcategory_of(category: str) -> str:
    """Uniform subcategory key: last two levels of the GRC category path.

    'Core/Filters' -> 'Core/Filters'; 'Soapy' -> 'Soapy'; 'Uncategorized' -> 'Uncategorized'.
    """
    parts = [p.strip() for p in category.split(" > ") if p.strip() and p.strip() != "Uncategorized"]
    if not parts:
        return "Uncategorized"
    return "/".join(parts[-2:]) if len(parts) >= 2 else parts[0]


def build_subcategory_index(taxonomy: list[dict[str, Any]] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Group catalog records by subcategory, sorted by block_id within each group."""
    if taxonomy is None:
        taxonomy = extract_catalog_taxonomy()
    index: dict[str, list[dict[str, Any]]] = {}
    for record in taxonomy:
        if record["block_id"] in NON_SEED_BLOCKS:
            continue
        index.setdefault(subcategory_of(record["category"]), []).append(record)
    for rows in index.values():
        rows.sort(key=lambda r: r["block_id"])
    return dict(sorted(index.items()))


def largest_remainder_alloc(target: int, weights: dict[str, float]) -> dict[str, int]:
    """Deterministic proportional allocation of `target` items across weighted keys.

    Uses the highest-averages (D'Hondt) rule: repeatedly give one item to the key
    maximizing weight[k] / (already[k] + 1), ties broken by key name. Allocation is
    capped at each key's integer weight, so no key ever receives more seeds than it
    actually has. Every key with positive weight receives >= 1 while items remain
    and caps allow.
    """
    keys = sorted(weights)
    alloc = {k: 0 for k in keys}
    caps = {k: int(weights[k]) for k in keys}
    remaining = target
    while remaining > 0:
        candidates = [k for k in keys if weights[k] > 0 and alloc[k] < caps[k]]
        if not candidates:
            break
        best = sorted(candidates, key=lambda k: (-weights[k] / (alloc[k] + 1), k))[0]
        alloc[best] += 1
        remaining -= 1
    return alloc


def extract_catalog_taxonomy() -> list[dict[str, Any]]:
    """Introspect all non-private GNU Radio Companion catalog blocks."""
    platform = get_platform()
    records: list[dict[str, Any]] = []

    for block_id, block_cls in sorted(platform.blocks.items()):
        if block_id.startswith("_"):
            continue
        category_raw = getattr(block_cls, "category", None) or []
        category = " > ".join(str(c) for c in category_raw) if category_raw else "Uncategorized"
        label = str(getattr(block_cls, "label", None) or block_id)

        params_data = getattr(block_cls, "parameters_data", None) or []
        params: list[dict[str, Any]] = []
        for p in params_data:
            if not isinstance(p, dict):
                continue
            p_id = str(p.get("id") or "")
            if not p_id or p.get("hide") == "all":
                continue
            params.append({
                "id": p_id,
                "label": str(p.get("label") or p_id),
                "dtype": str(p.get("dtype") or "raw"),
                "default": p.get("default"),
                "options": list(p.get("options") or []),
            })

        inputs_data = getattr(block_cls, "inputs_data", None) or []
        outputs_data = getattr(block_cls, "outputs_data", None) or []
        inputs = [
            {
                "domain": str(port.get("domain") or "stream"),
                "dtype": str(port.get("dtype") or ""),
                "optional": bool(port.get("optional", False)),
            }
            for port in inputs_data
            if isinstance(port, dict)
        ]
        outputs = [
            {
                "domain": str(port.get("domain") or "stream"),
                "dtype": str(port.get("dtype") or ""),
                "optional": bool(port.get("optional", False)),
            }
            for port in outputs_data
            if isinstance(port, dict)
        ]

        records.append({
            "block_id": block_id,
            "label": label,
            "category": category,
            "params": params,
            "inputs": inputs,
            "outputs": outputs,
        })

    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract GNU Radio catalog taxonomy.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiments/atomic_dataset/output/catalog_taxonomy.jsonl"),
        help="Output JSONL path",
    )
    args = parser.parse_args()

    taxonomy = extract_catalog_taxonomy()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for row in taxonomy:
            f.write(json.dumps(row, default=str) + "\n")

    print(f"Extracted {len(taxonomy)} catalog blocks -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
