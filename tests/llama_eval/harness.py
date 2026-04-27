"""Shared helpers for live llama eval runners."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from grc_agent.config import load_app_config
from grc_agent.llama_launcher import LlamaLauncherError, LlamaServerLauncher
from grc_agent.llama_server import LlamaServerClient
from grc_agent.session_ops import parse_connection_id

DEFAULT_FIXTURE_NAME = "random_bit_generator.grc"
RUN_STATUS_PASS = "PASS"
RUN_STATUS_FAIL = "FAIL"
RUN_STATUS_INFRA_FAIL = "INFRA_FAIL"

CaseRunner = Callable[[LlamaServerClient, str, Any], dict[str, Any]]
CaseReportBuilder = Callable[[Any, list[dict[str, Any]], int, float], dict[str, Any]]
StatusRenderer = Callable[[Any, dict[str, Any]], str]
SummaryBuilder = Callable[[list[dict[str, Any]], int], dict[str, Any]]


def fixture_path(name: str = DEFAULT_FIXTURE_NAME) -> Path:
    return Path(__file__).resolve().parents[1] / "data" / name


@contextmanager
def isolated_fixture_workspace(
    *fixture_names: str | None,
) -> Iterator[tuple[Path, dict[str, Path]]]:
    """Copy one or more fixtures into a temporary workspace and clean it up."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        copied: dict[str, Path] = {}
        for fixture_name in fixture_names:
            if not fixture_name or fixture_name in copied:
                continue
            src = fixture_path(fixture_name)
            dst = workspace / src.name
            shutil.copy2(src, dst)
            copied[fixture_name] = dst
        yield workspace, copied


def ensure_llama_server(
    server_url: str | None = None,
    model: str | None = None,
) -> tuple[str, str, LlamaServerClient]:
    """Ensure the llama.cpp server is reachable, starting it if necessary.

    Returns (server_url, model_alias, client).
    """
    config = load_app_config()
    resolved_url = (server_url or config.llama.server_url).rstrip("/")
    resolved_model = model or config.llama.model

    launcher = LlamaServerLauncher(
        config.llama,
        server_url=resolved_url,
        model_alias=resolved_model,
    )
    try:
        result = launcher.ensure_server_ready()
        print(
            f"{result.status.capitalize()} llama.cpp server at {result.server_url} (pid={result.pid})"
        )
        return result.server_url, result.model_alias, result.client
    except LlamaLauncherError as exc:
        print(f"Failed to start llama.cpp server: {exc}")
        raise


def restart_llama_server(
    server_url: str | None = None,
    model: str | None = None,
) -> tuple[str, str, LlamaServerClient]:
    """Force a fresh llama.cpp server instance and return a new client."""
    config = load_app_config()
    resolved_url = (server_url or config.llama.server_url).rstrip("/")
    resolved_model = model or config.llama.model

    launcher = LlamaServerLauncher(
        config.llama,
        server_url=resolved_url,
        model_alias=resolved_model,
    )

    result = launcher.restart_server_ready()
    print(f"Restarted llama.cpp server at {result.server_url} (pid={result.pid})")
    return (result.server_url, result.model_alias, result.client)


def build_phase_parser(
    description: str,
    *,
    default_n_runs: int,
    server_help: str,
    model_help: str,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--server-url",
        default=os.environ.get("GRC_AGENT_LIVE_LLAMA_URL"),
        help=server_help,
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GRC_AGENT_LIVE_LLAMA_MODEL"),
        help=model_help,
    )
    parser.add_argument(
        "--n-runs",
        type=int,
        default=default_n_runs,
        help=f"Number of runs per case. Default: {default_n_runs}.",
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Run only cases in this category.",
    )
    parser.add_argument(
        "--case",
        type=str,
        default=None,
        help="Run only the case with this name.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick check: force n_runs=1.",
    )
    return parser


def select_cases(
    all_cases: list[Any],
    *,
    category: str | None,
    case_name: str | None,
) -> list[Any]:
    cases = list(all_cases)
    if category:
        cases = [case for case in cases if case.category == category]
    if case_name:
        cases = [case for case in cases if case.name == case_name]
    return cases


def majority_passed(pass_count: int, n_runs: int, threshold: float) -> bool:
    return pass_count > n_runs * threshold


def summarize_by_category(results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    by_category: dict[str, dict[str, int]] = {}
    for result in results:
        category = result["category"]
        if category not in by_category:
            by_category[category] = {"passed": 0, "total": 0}
        by_category[category]["total"] += 1
        if result["passed"]:
            by_category[category]["passed"] += 1
    return by_category


def default_phase_summary(
    results: list[dict[str, Any]],
    total_cases: int,
) -> dict[str, Any]:
    total_passed = sum(1 for result in results if result["passed"])
    run_outcomes = summarize_run_outcomes(results)
    return {
        "total": total_cases,
        "passed": total_passed,
        "pass_rate": round(total_passed / total_cases, 4) if total_cases else 0,
        "by_category": summarize_by_category(results),
        **run_outcomes,
    }


def is_llama_timeout(error_message: Any) -> bool:
    return isinstance(error_message, str) and "Timed out connecting to llama.cpp server" in error_message


def is_infra_error_message(error_message: Any) -> bool:
    if not isinstance(error_message, str):
        return False
    lowered = error_message.lower()
    return any(
        fragment in lowered
        for fragment in (
            "timed out connecting to llama.cpp server",
            "failed to start llama.cpp server",
            "endpoint unavailable",
            "connection refused",
            "server disconnected",
            "connection reset",
            "remote end closed connection",
            "service unavailable",
        )
    )


_INFRA_ERROR_TYPES = frozenset(
    {"connect_timeout", "backend_startup_failure", "endpoint_unavailable", "server_disconnect"}
)


def classify_infra_error(error_message: Any) -> str | None:
    if not isinstance(error_message, str):
        return None
    lowered = error_message.lower()
    if "timed out connecting to llama.cpp server" in lowered:
        return "connect_timeout"
    if "failed to start llama.cpp server" in lowered:
        return "backend_startup_failure"
    if "endpoint unavailable" in lowered or "service unavailable" in lowered:
        return "endpoint_unavailable"
    if "connection refused" in lowered:
        return "endpoint_unavailable"
    if "server disconnected" in lowered or "connection reset" in lowered:
        return "server_disconnect"
    if "remote end closed connection" in lowered:
        return "server_disconnect"
    return None


def run_result_is_infra_failure(run_result: dict[str, Any]) -> bool:
    if is_infra_error_message(run_result.get("error")):
        return True

    if run_result.get("error_type") in _INFRA_ERROR_TYPES:
        return True

    tools_called = run_result.get("tools_called")
    requested_tool_calls = run_result.get("requested_tool_calls")
    executed_tool_calls = run_result.get("executed_tool_calls")
    if tools_called or requested_tool_calls or executed_tool_calls:
        return False

    for turn_result in run_result.get("turn_results", []):
        if turn_result.get("tools_called") or turn_result.get("requested_tool_calls"):
            continue
        if is_infra_error_message(turn_result.get("error")):
            return True
    return False


def derive_run_status(run_result: dict[str, Any]) -> str:
    if run_result_is_infra_failure(run_result):
        return RUN_STATUS_INFRA_FAIL
    if "matched" in run_result:
        return RUN_STATUS_PASS if run_result["matched"] else RUN_STATUS_FAIL
    if "sequence_matched" in run_result:
        return RUN_STATUS_PASS if run_result["sequence_matched"] else RUN_STATUS_FAIL
    if "all_turns_passed" in run_result:
        return RUN_STATUS_PASS if run_result["all_turns_passed"] else RUN_STATUS_FAIL
    if "passed" in run_result:
        return RUN_STATUS_PASS if run_result["passed"] else RUN_STATUS_FAIL
    return RUN_STATUS_FAIL


def summarize_run_outcomes(results: list[dict[str, Any]]) -> dict[str, Any]:
    total_scheduled_runs = 0
    model_attempts = 0
    model_passes = 0
    infra_failures = 0

    for result in results:
        for run in result.get("runs", []):
            total_scheduled_runs += 1
            status = run.get("status") or derive_run_status(run)
            if status == RUN_STATUS_INFRA_FAIL:
                infra_failures += 1
                continue
            model_attempts += 1
            if status == RUN_STATUS_PASS:
                model_passes += 1

    return {
        "model_attempts": model_attempts,
        "model_passes": model_passes,
        "infra_failures": infra_failures,
        "total_scheduled_runs": total_scheduled_runs,
        "model_pass_rate": round(model_passes / model_attempts, 4)
        if model_attempts
        else None,
        "complete": infra_failures == 0,
    }


def load_run_store(results_path: str | Path) -> dict[str, Any]:
    path = Path(results_path)
    if not path.exists():
        return {"version": 1, "runs": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"version": 1, "runs": []}
    runs = data.get("runs")
    if not isinstance(runs, list):
        data["runs"] = []
    return data


def write_run_store(results_path: str | Path, store: dict[str, Any]) -> None:
    path = Path(results_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    store["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(store, indent=2, sort_keys=False), encoding="utf-8")


def upsert_run_store_entry(results_path: str | Path, entry: dict[str, Any]) -> None:
    store = load_run_store(results_path)
    runs = store.setdefault("runs", [])
    for index, existing in enumerate(runs):
        if (
            existing.get("phase") == entry.get("phase")
            and existing.get("case_name") == entry.get("case_name")
            and existing.get("run_index") == entry.get("run_index")
        ):
            runs[index] = entry
            write_run_store(results_path, store)
            return
    runs.append(entry)
    write_run_store(results_path, store)


def persisted_phase_runs(
    results_path: str | Path,
    *,
    phase: int,
) -> dict[tuple[str, int], dict[str, Any]]:
    store = load_run_store(results_path)
    cached: dict[tuple[str, int], dict[str, Any]] = {}
    for entry in store.get("runs", []):
        if entry.get("phase") != phase:
            continue
        case_name = entry.get("case_name")
        run_index = entry.get("run_index")
        if isinstance(case_name, str) and isinstance(run_index, int):
            cached[(case_name, run_index)] = entry
    return cached


def build_persisted_run_entry(
    *,
    phase: int,
    case: Any,
    run_index: int,
    run_result: dict[str, Any],
    backend_restart_count: int,
) -> dict[str, Any]:
    status = run_result.get("status") or derive_run_status(run_result)
    prompt = getattr(case, "prompt", None)
    expected_chain: Any = None
    if hasattr(case, "expected_tool"):
        expected_chain = [case.expected_tool]
    elif hasattr(case, "expected_tool_sequence"):
        expected_chain = list(case.expected_tool_sequence)
    elif hasattr(case, "turns"):
        expected_chain = [
            {
                "turn_index": index,
                "prompt": turn.prompt,
                "expected_tools": list(turn.expected_tools_in_order),
            }
            for index, turn in enumerate(case.turns)
        ]

    turn_results = run_result.get("turn_results")
    return {
        "phase": phase,
        "category": case.category,
        "case_name": case.name,
        "run_index": run_index,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "error_type": run_result.get("error_type")
        or classify_infra_error(run_result.get("error")),
        "backend_restart_count": backend_restart_count,
        "prompt": prompt,
        "expected_chain": expected_chain,
        "actual_chain": run_result.get("tools_called"),
        "turn_results": turn_results,
        "run_result": run_result,
    }


def should_reuse_persisted_run(
    entry: dict[str, Any],
    *,
    resume: bool,
    rerun_failed: bool,
) -> bool:
    if not resume:
        return False
    status = entry.get("status")
    if rerun_failed:
        return status == RUN_STATUS_PASS
    return status in {RUN_STATUS_PASS, RUN_STATUS_FAIL, RUN_STATUS_INFRA_FAIL}


def run_phase_eval(
    *,
    phase: int,
    server_url: str,
    model: str,
    cases: list[Any],
    n_runs: int,
    majority_threshold: float,
    run_case: CaseRunner,
    build_case_report: CaseReportBuilder,
    render_status: StatusRenderer,
    build_summary: SummaryBuilder = default_phase_summary,
    retry_on_timeout: bool = False,
    results_path: str | Path | None = None,
    resume: bool = False,
    rerun_failed: bool = False,
) -> dict[str, Any]:
    config = load_app_config()
    resolved_url = (server_url or config.llama.server_url).rstrip("/")
    resolved_model = model or config.llama.model
    temperature = config.llama.temperature
    client: LlamaServerClient | None = None

    cached_runs = (
        persisted_phase_runs(results_path, phase=phase)
        if results_path is not None
        else {}
    )

    def safe_run_case(active_client: LlamaServerClient, active_model: str, case: Any) -> dict[str, Any]:
        try:
            return run_case(active_client, active_model, case)
        except Exception as exc:
            error_message = str(exc)
            if not is_infra_error_message(error_message):
                raise
            return {
                "tools_called": [],
                "requested_tool_calls": [],
                "executed_tool_calls": [],
                "error": error_message,
                "elapsed_seconds": None,
            }

    results = []
    total = len(cases) * n_runs
    done = 0

    for case in cases:
        runs = []
        for run_index in range(n_runs):
            done += 1
            print(
                f"[{done}/{total}] {case.category}/{case.name} run {run_index + 1}/{n_runs}",
                end="",
                flush=True,
            )
            cached_entry = cached_runs.get((case.name, run_index))
            if cached_entry is not None and should_reuse_persisted_run(
                cached_entry,
                resume=resume,
                rerun_failed=rerun_failed,
            ):
                run_result = cached_entry["run_result"]
                run_result.setdefault("status", cached_entry.get("status"))
                run_result.setdefault(
                    "backend_restart_count",
                    cached_entry.get("backend_restart_count", 0),
                )
                print(f" -> cached {run_result.get('status', derive_run_status(run_result))}")
                runs.append(run_result)
                continue

            backend_restart_count = 0

            if client is None:
                try:
                    resolved_url, resolved_model, client = ensure_llama_server(
                        resolved_url,
                        resolved_model,
                    )
                    temperature = client.temperature
                except LlamaLauncherError:
                    try:
                        resolved_url, resolved_model, client = restart_llama_server(
                            resolved_url,
                            resolved_model,
                        )
                        temperature = client.temperature
                        backend_restart_count = 1
                    except LlamaLauncherError as exc:
                        run_result = {
                            "tools_called": [],
                            "requested_tool_calls": [],
                            "executed_tool_calls": [],
                            "error": str(exc),
                            "elapsed_seconds": None,
                            "status": RUN_STATUS_INFRA_FAIL,
                            "error_type": classify_infra_error(str(exc))
                            or "backend_startup_failure",
                            "backend_restart_count": 1,
                        }
                        if results_path is not None:
                            upsert_run_store_entry(
                                results_path,
                                build_persisted_run_entry(
                                    phase=phase,
                                    case=case,
                                    run_index=run_index,
                                    run_result=run_result,
                                    backend_restart_count=1,
                                ),
                            )
                        print(" -> INFRA_FAIL")
                        runs.append(run_result)
                        continue

            run_result = safe_run_case(client, resolved_model, case)
            if (
                (retry_on_timeout and is_llama_timeout(run_result.get("error")))
                or run_result_is_infra_failure(run_result)
            ) and backend_restart_count == 0:
                try:
                    resolved_url, resolved_model, client = restart_llama_server(
                        resolved_url,
                        resolved_model,
                    )
                    temperature = client.temperature
                    backend_restart_count = 1
                    run_result = safe_run_case(client, resolved_model, case)
                except LlamaLauncherError as exc:
                    run_result = {
                        "tools_called": [],
                        "requested_tool_calls": [],
                        "executed_tool_calls": [],
                        "error": str(exc),
                        "elapsed_seconds": None,
                        "status": RUN_STATUS_INFRA_FAIL,
                        "error_type": classify_infra_error(str(exc))
                        or "backend_startup_failure",
                    }

            if run_result_is_infra_failure(run_result):
                run_result["status"] = RUN_STATUS_INFRA_FAIL
                run_result["error_type"] = classify_infra_error(run_result.get("error"))
            else:
                run_result["status"] = derive_run_status(run_result)
            run_result["backend_restart_count"] = backend_restart_count

            if results_path is not None:
                upsert_run_store_entry(
                    results_path,
                    build_persisted_run_entry(
                        phase=phase,
                        case=case,
                        run_index=run_index,
                        run_result=run_result,
                        backend_restart_count=backend_restart_count,
                    ),
                )

            if run_result.get("status") == RUN_STATUS_INFRA_FAIL:
                print(" -> INFRA_FAIL")
            else:
                print(f" -> {render_status(case, run_result)}")
            runs.append(run_result)

        results.append(build_case_report(case, runs, n_runs, majority_threshold))

    return {
        "phase": phase,
        "model": resolved_model,
        "temperature": temperature,
        "n_runs": n_runs,
        "majority_threshold": majority_threshold,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cases": results,
        "summary": build_summary(results, len(cases)),
    }



def extract_requested_tool_calls(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return normalized assistant-requested tool calls from chat history."""
    results = []
    for turn in history:
        if turn.get("role") != "assistant":
            continue
        raw_tool_calls = turn.get("tool_calls")
        if not isinstance(raw_tool_calls, list):
            continue
        for raw_call in raw_tool_calls:
            if not isinstance(raw_call, dict):
                continue
            function_payload = raw_call.get("function")
            if isinstance(function_payload, dict):
                name = function_payload.get("name")
                arguments = function_payload.get("arguments")
            else:
                name = raw_call.get("name")
                arguments = raw_call.get("arguments")
            results.append(
                {
                    "name": name,
                    "arguments": _parse_tool_arguments(arguments),
                }
            )
    return results


def extract_executed_tool_calls(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return executed tool results from chat history."""
    return [
        {
            "name": turn.get("name"),
            "arguments": turn.get("content"),
        }
        for turn in history
        if turn.get("role") == "tool"
    ]


def tools_appear_in_expected_order(
    actual_tool_names: list[str], expected_tool_names: list[str]
) -> bool:
    """Return whether expected tools appear in order without later expected tools arriving early."""
    if not expected_tool_names:
        return not actual_tool_names
    expected_index = 0
    for actual_tool_name in actual_tool_names:
        if expected_index >= len(expected_tool_names):
            break
        current_expected_tool = expected_tool_names[expected_index]
        if actual_tool_name == current_expected_tool:
            expected_index += 1
            continue
        if actual_tool_name in expected_tool_names[expected_index + 1 :]:
            return False
    return expected_index == len(expected_tool_names)


def tool_call_matches_transaction_checks(
    tool_call: dict[str, Any],
    expected_operations: list[dict[str, Any]],
    *,
    ordered: bool = True,
) -> bool:
    """Return whether the tool-call transaction matches the expected operations."""
    actual_operations = normalize_transaction_operations(tool_call.get("arguments"))
    if not actual_operations:
        return False
    if ordered:
        actual_index = 0
        for expected_operation in expected_operations:
            while actual_index < len(actual_operations):
                if _partial_match(actual_operations[actual_index], expected_operation):
                    actual_index += 1
                    break
                actual_index += 1
            else:
                return False
        return True
    return all(
        any(
            _partial_match(actual_operation, expected_operation)
            for actual_operation in actual_operations
        )
        for expected_operation in expected_operations
    )


def tool_call_matches_argument_checks(
    tool_call: dict[str, Any], expected_arguments: dict[str, Any]
) -> bool:
    """Return whether the raw tool-call arguments match a partial expectation."""
    return _partial_match(tool_call.get("arguments"), expected_arguments)


def normalize_transaction_operations(arguments: Any) -> list[dict[str, Any]]:
    """Normalize one tool-call argument payload into an ordered transaction list."""
    if not isinstance(arguments, dict):
        return []
    normalized_operations = arguments.get("normalized_operations")
    if isinstance(normalized_operations, list) and all(
        isinstance(item, dict) for item in normalized_operations
    ):
        operations = list(normalized_operations)
    else:
        transaction = arguments.get("transaction", arguments)
        if isinstance(transaction, dict):
            operations = [transaction]
        elif isinstance(transaction, list) and all(
            isinstance(item, dict) for item in transaction
        ):
            operations = list(transaction)
        else:
            return []

    normalized_operations: list[dict[str, Any]] = []
    for operation in operations:
        normalized_operation = dict(operation)
        if (
            normalized_operation.get("op_type") == "remove_connection"
            and "connection_id" in normalized_operation
        ):
            parsed = parse_connection_id(normalized_operation.get("connection_id"))
            if parsed is not None:
                src_block, src_port, dst_block, dst_port = parsed
                normalized_operation.setdefault("src_block", src_block)
                normalized_operation.setdefault("src_port", src_port)
                normalized_operation.setdefault("dst_block", dst_block)
                normalized_operation.setdefault("dst_port", dst_port)
        normalized_operations.append(normalized_operation)
    return normalized_operations


def text_contains_any(text: str, needles: list[str]) -> bool:
    """Return whether any expected lowercase fragment appears in the text."""
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def render_prompt(prompt: str, target_path: str, save_path: str) -> str:
    return prompt.format(target_path=target_path, save_path=save_path)


def render_value_templates(value: Any, *, target_path: str, save_path: str) -> Any:
    if isinstance(value, str):
        return value.format(target_path=target_path, save_path=save_path)
    if isinstance(value, dict):
        return {
            key: render_value_templates(
                nested_value, target_path=target_path, save_path=save_path
            )
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [
            render_value_templates(item, target_path=target_path, save_path=save_path)
            for item in value
        ]
    return value


def requested_tool_calls_since(
    history: list[dict[str, Any]], start_index: int
) -> list[dict[str, Any]]:
    return extract_requested_tool_calls(history[start_index:])


def executed_tool_calls_since(
    history: list[dict[str, Any]], start_index: int
) -> list[dict[str, Any]]:
    return extract_executed_tool_calls(history[start_index:])


def _parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _partial_match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            key in actual and _partial_match(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(
            _partial_match(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected)
        )
    return actual == expected or str(actual) == str(expected)
