from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StateSnapshot:
    graph_hash: str | None = None
    validation_status: bool | None = None
    dirty: bool = False
    saved_path: str | None = None
    state_revision: int | None = None
    has_backing_path: bool | None = None


@dataclass
class ToolRecord:
    name: str
    ok: bool | None = None
    payload: dict[str, Any] | None = None


@dataclass
class ScenarioExpectations:
    expect_mutation: bool = False
    expect_validate: bool = False
    expect_save: bool = False
    expect_no_mutation: bool = False
    expect_refusal: bool = False
    expect_propose_only: bool = False
    expect_separate_validate_report: bool = False
    scenario_family: str = ""
    prompt: str = ""


@dataclass
class ScenarioResult:
    scenario_id: str = ""
    scenario_family: str = ""
    prompt: str = ""

    before: StateSnapshot = field(default_factory=StateSnapshot)
    after: StateSnapshot = field(default_factory=StateSnapshot)

    tool_chain: list[ToolRecord] = field(default_factory=list)
    assistant_text: str = ""
    error: str | None = None
    elapsed_seconds: float = 0.0

    apply_edit_called: bool = False
    apply_edit_ok: bool | None = None
    propose_edit_called: bool = False
    propose_edit_ok: bool | None = None
    validate_graph_called: bool = False
    save_graph_called: bool = False
    mutation_attempted: bool = False
    mutation_committed: bool = False

    insert_primitive_used: bool = False
    suggest_compatible_insertions_called: bool = False

    failure_category: str = "PASS"
    invariant_violations: list[str] = field(default_factory=list)
    notes: str = ""

    has_backing_path: bool = False
    string_ports_before: list[str] = field(default_factory=list)
    string_ports_after: list[str] = field(default_factory=list)
    connection_id_resolved: bool | None = None
    duplicate_rejected_safely: bool | None = None
    arbitrary_file_written: bool | None = None

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tool_chain]


FAILURE_CATEGORIES = frozenset({
    "PASS",
    "INFRA_FAIL",
    "MODEL_ROUTING",
    "MODEL_REASONING",
    "MODEL_KNOWLEDGE_LIMIT",
    "TOOL_CAPABILITY_GAP",
    "VALIDATION_GAP",
    "PROMPT_AMBIGUITY",
    "EVAL_EXPECTATION_TOO_STRICT",
    "GRADING_ISSUE",
    "SAVE_PATH_GAP",
    "UNSAFE_BEHAVIOR",
    "RAW_YAML_GUARD_FAIL",
    "GRAPH_LOAD_FAIL",
    "STOP_THE_LINE",
})
