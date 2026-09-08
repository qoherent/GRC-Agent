"""Task-spec schema, loader, and validator for the dataset-collection harness.

Owns the three data shapes a campaign runs on (see
docs/plans/2026-09-08-1627-feat-finetuning-dataset-collection-harness-plan.md U1):

- ``TaskSpec`` — one simulated user task: persona, goal, seed graph, run-mode
  stratum, machine-checkable acceptance predicate, turn budgets.
- ``Persona`` — the simulator's role-play axes (occupation, proficiency,
  personality, patience) plus style *parameters* (never message exemplars —
  PersonaForge found exemplars collapse diversity).
- fixture preparation — per-task isolated copies of the seed graph, validated
  headlessly with GRC's own parser before any campaign boots.

``generate_options`` carries the run-mode stratum: a ``no_gui`` task's turns
are excluded from the primary SFT files (plan KTD7) because GRC runs those
graphs in an external terminal whose console output the exec monitor cannot
capture.

Usage:
    uv run python experiments/dataset_collection/task_spec.py --validate
    uv run python experiments/dataset_collection/task_spec.py --prepare-fixtures
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TASKS_DIR = Path(__file__).resolve().parent / "tasks"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# R1's task-type taxonomy — the coverage matrix the validator prints.
CATEGORIES = (
    "build",
    "repair",
    "param",
    "epy",
    "run_verify",
    "knowledge",
    "save_block",
    "file_ops",
    "modify",
)


class PersonaStyle(BaseModel):
    """Abstract style parameters (never utterance exemplars)."""

    max_words: int = Field(default=25, ge=5, le=60, description="Soft cap per simulator message.")
    formality: Literal["casual", "neutral", "formal"] = "casual"


class Persona(BaseModel):
    id: str
    occupation: str
    technical_proficiency: Literal["beginner", "intermediate", "expert"]
    personality: str
    patience: Literal["low", "medium", "high"]
    style: PersonaStyle = Field(default_factory=PersonaStyle)


class Acceptance(BaseModel):
    """Machine-checkable completion predicate (plan KTD7).

    ``completed`` requires the simulator's STOP decision AND this predicate
    passing at teardown; the simulator judges user satisfaction, this judges
    mechanical correctness.
    """

    kind: Literal[
        "graph_valid",  # headless parse + native flowgraph validation
        "graph_unchanged_valid",  # knowledge/Q&A tasks: seed stays valid, unchanged
        "run_succeeded",  # last recorded flowgraph run reported success
        "log_contains",  # last run log matches `pattern`
        "file_exists",  # `path` (fixture-relative) exists
        "hier_block_saved",  # `pattern` names a file under the GRC hier-block dir
    ]
    pattern: str | None = None
    path: str | None = None
    description: str


class TaskSpec(BaseModel):
    id: str
    category: Literal[*CATEGORIES]
    persona_id: str
    goal: str = Field(min_length=10, description="Natural language, solution-free.")
    seed_grc: str | None = Field(
        default=None, description="Repo-relative path under playground/, or null for a new graph."
    )
    generate_options: Literal["qt_gui", "no_gui"] = "qt_gui"
    acceptance: Acceptance
    max_simulator_turns: int = Field(ge=5, le=60)
    give_up_after_unproductive: int = Field(ge=3, le=15)
    expected_capabilities: list[str] = Field(default_factory=list)


def load_personas(tasks_dir: Path = TASKS_DIR) -> dict[str, Persona]:
    raw = json.loads((tasks_dir / "personas.json").read_text(encoding="utf-8"))
    people = [Persona.model_validate(p) for p in raw["personas"]]
    return {p.id: p for p in people}


def load_tasks(tasks_dir: Path = TASKS_DIR) -> list[TaskSpec]:
    specs = []
    for path in sorted(tasks_dir.glob("*.json")):
        if path.name == "personas.json":
            continue
        specs.append(TaskSpec.model_validate(json.loads(path.read_text(encoding="utf-8"))))
    return specs


def _task_errors(t: TaskSpec, personas: dict[str, Persona]) -> list[str]:
    """Per-task reference and predicate checks (one uniform rule per task)."""
    errors: list[str] = []
    where = f"task {t.id}"
    if t.persona_id not in personas:
        errors.append(f"{where}: unknown persona_id {t.persona_id!r}")
    if t.seed_grc is not None:
        seed = REPO_ROOT / t.seed_grc
        if not seed.is_file():
            errors.append(f"{where}: seed_grc not found: {t.seed_grc}")
        elif not _parses_headless(seed):
            errors.append(f"{where}: seed_grc fails headless parse: {t.seed_grc}")
    if t.acceptance.kind == "log_contains":
        if not t.acceptance.pattern:
            errors.append(f"{where}: log_contains acceptance needs a pattern")
        else:
            try:
                import re

                re.compile(t.acceptance.pattern)
            except re.error as e:
                errors.append(f"{where}: log_contains pattern does not compile: {e}")
    if t.acceptance.kind == "file_exists" and not t.acceptance.path:
        errors.append(f"{where}: file_exists acceptance needs a path")
    return errors


def validate_library(tasks_dir: Path = TASKS_DIR) -> list[str]:
    """Validate every task spec and its persona/seed references.

    Returns the list of error strings (empty = valid). Seed parsing is done
    headlessly with GRC's own ``load_flow_graph`` so a campaign can never boot
    on a graph GRC itself rejects (plan C7).
    """
    errors: list[str] = []
    try:
        personas = load_personas(tasks_dir)
    except Exception as e:  # noqa: BLE001 - report, don't crash the summary
        return [f"personas.json invalid: {e}"]

    tasks: list[TaskSpec] = []
    for path in sorted(tasks_dir.glob("*.json")):
        if path.name == "personas.json":
            continue
        try:
            tasks.append(TaskSpec.model_validate(json.loads(path.read_text(encoding="utf-8"))))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{path.name}: {e}")
    if errors:
        return errors

    seen_ids: set[str] = set()
    for t in tasks:
        if t.id in seen_ids:
            errors.append(f"task {t.id}: duplicate id")
        seen_ids.add(t.id)
        errors.extend(_task_errors(t, personas))
    return errors


def coverage_matrix(tasks: list[TaskSpec]) -> str:
    by_cat: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    for t in tasks:
        by_cat[t.category].append(t.id)
    lines = ["Category      Tasks"]
    for cat in CATEGORIES:
        lines.append(f"{cat:<13} {len(by_cat[cat])}  {', '.join(by_cat[cat]) or '-'}")
    return "\n".join(lines)


def prepare_fixtures(out_dir: Path = FIXTURES_DIR, tasks_dir: Path = TASKS_DIR) -> list[Path]:
    """Copy each seed graph into its own isolated fixture directory.

    The fixture dir (``<out>/<task_id>/<seed>.grc``) is the task's project
    directory for the campaign: fs/shell tools sandbox to it, and the live
    ``playground/`` tree is never touched (plan KTD3). Returns the fixture
    paths; ``file_path`` identity assertions in the runner use these.
    """
    made: list[Path] = []
    for t in load_tasks(tasks_dir):
        if t.seed_grc is None:
            continue
        task_dir = out_dir / t.id
        task_dir.mkdir(parents=True, exist_ok=True)
        dest = task_dir / Path(t.seed_grc).name
        shutil.copy2(REPO_ROOT / t.seed_grc, dest)
        made.append(dest)
    return made


def _parses_headless(seed: Path) -> bool:
    try:
        from grc_agent.adapter.graph import load_flow_graph

        load_flow_graph(str(seed))
        return True
    except Exception:  # noqa: BLE001 - any parse failure fails validation
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate", action="store_true")
    mode.add_argument("--prepare-fixtures", action="store_true")
    args = parser.parse_args(argv)

    if args.prepare_fixtures:
        for path in prepare_fixtures():
            print(f"fixture: {path}")
        return 0

    errors = validate_library()
    if errors:
        print("INVALID task library:")
        for e in errors:
            print(f"  - {e}")
        return 1
    tasks = load_tasks()
    personas = load_personas()
    print(f"OK: {len(tasks)} tasks, {len(personas)} personas")
    print(coverage_matrix(tasks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
