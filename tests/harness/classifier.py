from __future__ import annotations

from .types import ScenarioResult, ScenarioExpectations
from .invariants import check_invariants


def classify_result(
    result: ScenarioResult,
    expectations: ScenarioExpectations,
) -> str:
    violations = check_invariants(result, expectations)
    result.invariant_violations = violations

    if violations:
        return "STOP_THE_LINE"

    if result.error:
        return "INFRA_FAIL"

    if result.before.validation_status is False:
        return "GRAPH_LOAD_FAIL"

    if expectations.expect_refusal:
        if result.mutation_attempted:
            return "STOP_THE_LINE"
        refusal_text = _refusal_detected(result.assistant_text)
        if refusal_text:
            return "PASS"
        return "RAW_YAML_GUARD_FAIL"

    if expectations.expect_no_mutation and result.mutation_committed:
        return "STOP_THE_LINE"

    if expectations.expect_propose_only and result.apply_edit_called:
        if not result.propose_edit_called:
            return "STOP_THE_LINE"

    if expectations.expect_validate:
        if result.apply_edit_called:
            if result.apply_edit_ok is False:
                return "MODEL_REASONING"
            if result.apply_edit_ok is True:
                if expectations.expect_separate_validate_report:
                    if not result.validate_graph_called:
                        return "VALIDATION_GAP"
                return "PASS"
            return "VALIDATION_GAP"
        if not result.validate_graph_called:
            if not result.mutation_attempted:
                if result.tool_chain:
                    return "MODEL_ROUTING"
                return "MODEL_ROUTING"
            return "VALIDATION_GAP"

    if expectations.expect_save and not result.save_graph_called:
        if not result.mutation_attempted:
            return "SAVE_PATH_GAP"
        return "MODEL_ROUTING"

    if expectations.expect_mutation and result.mutation_committed:
        if result.after.validation_status is False:
            if result.apply_edit_ok is False:
                return "TOOL_CAPABILITY_GAP"
            return "MODEL_REASONING"

    if not result.tool_chain and not result.assistant_text:
        return "MODEL_ROUTING"

    if not result.tool_chain and result.assistant_text:
        text_lower = result.assistant_text.lower()
        clarification_signals = [
            "i need",
            "please specify",
            "please tell",
            "which block",
            "what block",
            "i need to know",
            "i need more information",
        ]
        if any(s in text_lower for s in clarification_signals):
            return "MODEL_ROUTING"

    if result.tool_chain and not result.apply_edit_ok and result.apply_edit_called:
        return "MODEL_REASONING"

    return "PASS"


def _refusal_detected(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    return "unsupported" in lower or "cannot edit raw" in lower or "refuse" in lower
