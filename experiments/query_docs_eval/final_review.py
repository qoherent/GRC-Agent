"""Stage 3: synthesize all Stage 2 reviewer reports into a single
findings-and-causes-only report, via the generic experiments/llm_review
pipeline plugged with this experiment's own prompts (prompts.py).

Usage: uv run python experiments/query_docs_eval/final_review.py
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "experiments"))

from llm_review.pipeline import run_planner  # noqa: E402
from prompts import planner_prompt  # noqa: E402

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
FINAL_PATH = Path(__file__).resolve().parent / "final_findings_and_causes.md"


def main() -> None:
    out_path = run_planner(REPORTS_DIR, FINAL_PATH, planner_prompt)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
