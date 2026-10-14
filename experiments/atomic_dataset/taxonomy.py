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
