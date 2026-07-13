"""Generic 2-stage LLM review pipeline, reusable across experiments: call a
reviewer once per input .md file, then call one planner over all reviewer
outputs. This module only owns the looping/IO — bring your own prompt-building
functions to reuse it for any "review N things, then synthesize" task.
"""

from collections.abc import Callable
from pathlib import Path

from .ollama_client import chat_completion

ReviewerPromptFn = Callable[[Path, str], str]
PlannerPromptFn = Callable[[list[tuple[Path, str]]], str]


def run_reviewers(
    input_dir: Path, out_dir: Path, prompt_fn: ReviewerPromptFn, suffix: str = "_report"
) -> list[Path]:
    """For each .md file in input_dir, build a prompt via prompt_fn(path, content),
    call the reviewer, and write the raw response to out_dir/<stem><suffix>.md."""
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs = sorted(input_dir.glob("*.md"))
    if not inputs:
        raise RuntimeError(f"No .md files found under {input_dir}")

    written = []
    for path in inputs:
        content = path.read_text(encoding="utf-8")
        result = chat_completion(prompt_fn(path, content))
        out_path = out_dir / f"{path.stem}{suffix}.md"
        out_path.write_text(result, encoding="utf-8")
        written.append(out_path)
    return written


def run_planner(reports_dir: Path, out_path: Path, prompt_fn: PlannerPromptFn) -> Path:
    """Read every .md report in reports_dir, build one synthesis prompt via
    prompt_fn(list[(path, content)]), call the planner, and write the raw
    response to out_path."""
    reports = sorted(reports_dir.glob("*.md"))
    if not reports:
        raise RuntimeError(f"No reports found under {reports_dir}")

    items = [(p, p.read_text(encoding="utf-8")) for p in reports]
    result = chat_completion(prompt_fn(items))
    out_path.write_text(result, encoding="utf-8")
    return out_path
