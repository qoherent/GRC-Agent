"""Harness-owned campaign manifest (plan U3, KTD10).

The app's session DB records the conversation; the manifest records everything
the app does not persist: campaign identity, frozen tool schemas, per-turn
provenance (simulator vs synthetic auto-fix vs system), stop verdicts,
acceptance results, redaction counts, and containment-check outcomes.

Plain JSONL files under the campaign directory (fresh, isolated per campaign
via GRC_AGENT_ENV — plan KTD3). The auditor (U4) cross-checks every collected
session against these records; the exporter (U5) reads verdicts and provenance
from here.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

Provenance = Literal["simulator", "autofix", "system"]

VERDICTS = (
    "completed",
    "failed_acceptance",
    "agent_gave_up",
    "budget_exhausted",
    "simulator_aborted",
    "agent_error",
    "infrastructure_abort",
)


@dataclass
class CampaignMeta:
    campaign_id: str
    started_at: str
    teacher: dict[str, Any]  # provider/model/base_url from the live agent
    approval_mode: str
    shell_denied_commands_sha: str  # hash, not the list — the list is config
    shell_timeout: float
    tool_freeze_ok: bool  # False => names-only fallback was recorded
    task_ids: list[str]
    git_head: str = ""


@dataclass
class TurnRecord:
    task_id: str
    turn_index: int  # simulator-turn numbering; autofix/system turns get -1
    provenance: Provenance
    message: str
    reply: str = ""
    stop: bool = False
    reason: str = ""
    session_id: int | None = None
    simulator_raw: dict[str, Any] | None = None
    redactions: int = 0
    repetition_flagged: bool = False
    is_tracking_at_end: bool = False
    ts: float = field(default_factory=time.time)


@dataclass
class TaskRecord:
    task_id: str
    persona_id: str
    category: str
    goal: str
    generate_options: str
    seed_grc: str | None
    session_id: int | None = None
    verdict: str = ""
    stop_reason: str = ""
    simulator_turns: int = 0
    autofix_turns: int = 0
    acceptance_kind: str = ""
    acceptance_passed: bool | None = None
    acceptance_detail: str = ""
    containment_ok: bool | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None


class Manifest:
    """Append-only JSONL manifest: meta.json + tasks.jsonl + turns.jsonl."""

    def __init__(self, campaign_dir: Path) -> None:
        self.dir = campaign_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tasks_path = self.dir / "tasks.jsonl"
        self.turns_path = self.dir / "turns.jsonl"
        self.meta_path = self.dir / "meta.json"
        self.campaign_id = uuid.uuid4().hex[:12]

    def write_meta(self, meta: CampaignMeta) -> None:
        self.meta_path.write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")

    def record_task(self, rec: TaskRecord) -> None:
        with self.tasks_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(rec)) + "\n")

    def record_turn(self, rec: TurnRecord) -> None:
        with self.turns_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(rec)) + "\n")

    def load_meta(self) -> CampaignMeta:
        return CampaignMeta(**json.loads(self.meta_path.read_text(encoding="utf-8")))

    def load_tasks(self) -> list[TaskRecord]:
        if not self.tasks_path.exists():
            return []
        return [
            TaskRecord(**json.loads(line))
            for line in self.tasks_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def load_turns(self) -> list[TurnRecord]:
        if not self.turns_path.exists():
            return []
        return [
            TurnRecord(**json.loads(line))
            for line in self.turns_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
