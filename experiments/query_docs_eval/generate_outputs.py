"""Stage 1: run a fixed set of realistic GNU Radio questions through
adapter.query_docs() and save each raw JSON result as a .md file.

Usage: uv run python experiments/query_docs_eval/generate_outputs.py
"""

import json
import re
from pathlib import Path

from grc_agent.adapter import query_docs

OUT_DIR = Path(__file__).resolve().parent / "raw_outputs"

# Golden-path conceptual/how-to questions plus deliberate edge cases: a very
# broad question, and a question with no real match in the docs corpus.
QUERIES = [
    "What is a stream tag and how is it different from a regular parameter?",
    "How do I design a low pass filter and choose appropriate filter taps?",
    "What is the difference between streams and vectors in GNU Radio?",
    "How do I create my first out-of-tree (OOT) block in Python using gr-modtool?",
    "How does message passing between blocks work, as opposed to stream connections?",
    "How does sample rate change (resampling) work in a flowgraph?",
    "What are hierarchical blocks and how do their parameters work?",
    "How do I get started building my first flowgraph in GNU Radio?",
    "What is a tagged stream block and why would I use one?",
    "How do I configure a Costas loop for QPSK carrier phase recovery?",
]


def slugify(query: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")[:60]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for query in QUERIES:
        result = query_docs(query)
        out_path = OUT_DIR / f"{slugify(query)}.md"
        out_path.write_text(
            f"# query_docs query: {query!r}\n\n"
            f"## Result JSON\n\n```json\n{json.dumps(result, indent=2)}\n```\n",
            encoding="utf-8",
        )
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
