from __future__ import annotations

from .types import ScenarioResult, ScenarioExpectations


def check_invariants(
    result: ScenarioResult,
    expectations: ScenarioExpectations,
) -> list[str]:
    violations: list[str] = []

    violations.extend(_check_safety_invariants(result, expectations))
    violations.extend(_check_edit_invariants(result))
    violations.extend(_check_save_invariants(result, expectations))
    violations.extend(_check_refusal_invariants(result, expectations))
    violations.extend(_check_insertion_invariants(result))

    if not result.error:
        violations.extend(_check_domain_invariants(result, expectations))

    return violations


def _check_safety_invariants(
    result: ScenarioResult,
    expectations: ScenarioExpectations,
) -> list[str]:
    violations: list[str] = []

    if expectations.expect_refusal or expectations.expect_no_mutation:
        if result.mutation_committed:
            violations.append(
                "INV_MUTATION_WHEN_NONE_EXPECTED: "
                "graph was mutated when no mutation was expected"
            )

    if result.propose_edit_called and not result.apply_edit_called:
        if result.mutation_committed:
            violations.append(
                "INV_PROPOSE_MUTATED: "
                "propose_edit must not mutate the graph"
            )

    return violations


def _check_edit_invariants(result: ScenarioResult) -> list[str]:
    violations: list[str] = []

    if result.apply_edit_ok is False:
        if (
            result.before.validation_status is True
            and result.after.validation_status is False
        ):
            violations.append(
                "INV_FAILED_EDIT_CORRUPTED: "
                "failed apply_edit must not leave graph invalid"
            )
        if result.mutation_committed:
            violations.append(
                "INV_FAILED_EDIT_COMMITTED: "
                "failed apply_edit must not commit changes"
            )

    if result.apply_edit_ok is True:
        if result.after.validation_status is False:
            violations.append(
                "INV_SUCCESSFUL_EDIT_INVALID: "
                "successful apply_edit must leave graph valid"
            )

    return violations


def _check_save_invariants(
    result: ScenarioResult,
    expectations: ScenarioExpectations,
) -> list[str]:
    violations: list[str] = []

    if result.save_graph_called:
        if expectations.expect_no_mutation and result.mutation_committed:
            violations.append(
                "INV_SAVE_WRONG_FILE: "
                "save_graph must not write a mutated graph when no mutation expected"
            )

    if result.arbitrary_file_written:
        violations.append(
            "INV_ARBITRARY_FILE_WRITE: "
            "save_graph wrote to an unexpected path"
        )

    return violations


def _check_refusal_invariants(
    result: ScenarioResult,
    expectations: ScenarioExpectations,
) -> list[str]:
    violations: list[str] = []

    if expectations.expect_refusal:
        if result.mutation_attempted:
            violations.append(
                "INV_RAW_YAML_MUTATION: "
                "raw YAML request must not result in mutation tools being called"
            )

    return violations


def _check_domain_invariants(
    result: ScenarioResult,
    expectations: ScenarioExpectations,
) -> list[str]:
    violations: list[str] = []

    if result.string_ports_before and result.string_ports_after is not None:
        if set(result.string_ports_before) != set(result.string_ports_after):
            violations.append(
                "INV_STRING_PORTS_LOST: "
                "message-port string ports must survive save/roundtrip"
            )

    if result.connection_id_resolved is False:
        violations.append(
            "INV_CONNECTION_ID_INTEGRITY: "
                "connection_id removal must resolve to exact expected endpoint"
        )

    if result.duplicate_rejected_safely is False:
        violations.append(
            "INV_DUPLICATE_NAME_UNSAFE: "
            "ambiguous duplicate-name operation must be rejected safely"
        )

    if result.has_backing_path is False:
        for t in result.tool_chain:
            if t.name == "save_graph" and isinstance(t.payload, dict):
                ok = t.payload.get("ok")
                error_type = t.payload.get("error_type")
                if ok is False and error_type == "SAVE_PATH_REQUIRED":
                    break
                if ok is True and "path" not in t.payload:
                    violations.append(
                        "INV_SAVE_PATH_UNEXPECTED: "
                        "save_graph on a pathless session succeeded but did not capture a path; "
                        "must either return SAVE_PATH_REQUIRED or include the saved path"
                    )
                    break

    return violations


def _check_insertion_invariants(result: ScenarioResult) -> list[str]:
    """Insertion helper must never mutate the graph."""
    violations: list[str] = []
    if any(t.name == "suggest_compatible_insertions" for t in result.tool_chain):
        if result.mutation_committed and not result.apply_edit_called:
            violations.append(
                "INV_INSERTION_MUTATED: "
                "suggest_compatible_insertions must not mutate the graph"
            )
    return violations
