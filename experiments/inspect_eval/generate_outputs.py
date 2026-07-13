"""Stage 1: run inspect_graph over every tests/data/*.grc fixture and dump
the source .grc alongside the tool's JSON output, one .md per fixture.

Usage: uv run python experiments/inspect_eval/generate_outputs.py
"""

import json
from pathlib import Path

from grc_agent.adapter import inspect_graph, load_flow_graph

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "data"
OUT_DIR = Path(__file__).resolve().parent / "raw_outputs"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fixtures = sorted(FIXTURES_DIR.glob("*.grc"))
    if not fixtures:
        raise RuntimeError(f"No .grc fixtures found under {FIXTURES_DIR}")

    for grc_path in fixtures:
        fg = load_flow_graph(str(grc_path))
        result = inspect_graph(fg)
        out_path = OUT_DIR / f"{grc_path.stem}.md"
        out_path.write_text(
            f"# inspect_graph output: {grc_path.name}\n\n"
            f"## Source (.grc)\n\n```yaml\n{grc_path.read_text(encoding='utf-8')}\n```\n\n"
            f"## inspect_graph JSON\n\n```json\n{json.dumps(result, indent=2)}\n```\n",
            encoding="utf-8",
        )
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
