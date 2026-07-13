"""Stage 1: run a fixed set of realistic search queries through
adapter.query_catalog() and save each raw JSON result as a .md file.

Usage: uv run python experiments/query_catalog_eval/generate_outputs.py
"""

import json
import re
from pathlib import Path

from grc_agent.adapter import query_catalog

OUT_DIR = Path(__file__).resolve().parent / "raw_outputs"

# Golden-path block lookups plus deliberate edge cases: a single-word/vague
# query, and a nonsense query with no real GNU Radio match.
QUERIES = [
    "sine wave source",
    "low pass filter block",
    "block that adds Gaussian noise to a signal",
    "resample a signal to a different sample rate",
    "FFT plot for frequency domain visualization",
    "convert a PDU to a tagged stream",
    "QAM modulator block",
    "convolutional encoder for forward error correction",
    "throttle block to limit CPU usage in simulation",
    "block that orders a pizza",
]


def slugify(query: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for query in QUERIES:
        result = query_catalog(query)
        out_path = OUT_DIR / f"{slugify(query)}.md"
        out_path.write_text(
            f"# query_catalog query: {query!r}\n\n"
            f"## Result JSON\n\n```json\n{json.dumps(result, indent=2)}\n```\n",
            encoding="utf-8",
        )
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
