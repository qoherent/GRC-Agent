"""Stage 2: send each Stage 1 output (.grc source + inspect_graph JSON) to
an Ollama Cloud reviewer model, via the generic experiments/llm_review
pipeline plugged with this experiment's own prompts (prompts.py).

Usage: uv run python experiments/inspect_eval/review_outputs.py
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "experiments"))

from llm_review.pipeline import run_reviewers  # noqa: E402
from prompts import reviewer_prompt  # noqa: E402

RAW_DIR = Path(__file__).resolve().parent / "raw_outputs"
REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def main() -> None:
    written = run_reviewers(RAW_DIR, REPORTS_DIR, reviewer_prompt)
    for path in written:
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
