#!/usr/bin/env python3
"""Aggregate persisted live-eval run stores into a release stability dashboard."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
from typing import Any, Iterable

from tests.llama_eval.harness import (
    MVP_RELEASE_MODEL_TOOLS,
    case_run_stability,
    load_run_store,
)

PHASE_NAMES = {
    20: "r0_r1_release",
    25: "r1_set_state",
    35: "r2_disconnect",
    50: "tier5_adversarial",
    56: "r3_rewire",
    57: "r4a_insert",
    58: "r4b_remove",
    59: "r4c_add_variable",
    71: "r7_exact_external",
    72: "r7_natural_external",
}
MANIFEST_DIR = Path(__file__).resolve().parent / "capability_manifests"


def load_capability_manifests() -> dict[str, dict[str, Any]]:
    """Load capability manifests keyed by release_profile/suite name."""
    manifests: dict[str, dict[str, Any]] = {}
    if not MANIFEST_DIR.exists():
        return manifests
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        suite = payload.get("suite")
        if isinstance(suite, str) and suite:
            manifests[suite] = payload
    return manifests


def build_release_dashboard(
    stores: Iterable[dict[str, Any]],
    *,
    required_phases: tuple[int, ...] = (20, 25, 35, 56, 57, 58, 59, 71, 72, 50),
    min_runs_per_case: int = 3,
    stability_threshold: float = 1.0,
    scope: str = "all",
) -> dict[str, Any]:
    """Build one dashboard from one or more persisted live-eval result stores."""
    grouped: dict[int, dict[tuple[str, str], list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    malformed_entries = 0
    mixed_profile_entries: list[str] = []
    raw_legacy_tool_entries: list[str] = []
    raw_tool_history_entries: list[str] = []
    manifest_missing_entries: list[str] = []
    manifest_dimension_entries: list[str] = []
    manifest_forbidden_tool_entries: list[str] = []
    manifests = load_capability_manifests()
    observed_profile_statuses: dict[str, set[str]] = defaultdict(set)
    diagnostic_case_keys: set[str] = set()

    for store in stores:
        runs = store.get("runs", [])
        if not isinstance(runs, list):
            malformed_entries += 1
            continue
        for entry in runs:
            if not isinstance(entry, dict):
                malformed_entries += 1
                continue
            parsed = _run_from_store_entry(entry)
            if parsed is None:
                malformed_entries += 1
                continue
            phase, category, case_name, run, metadata = parsed
            if phase in required_phases:
                if not isinstance(metadata, dict):
                    mixed_profile_entries.append(
                        f"{phase_name_for(phase)}/{category}/{case_name}#run{run.get('run_index', '?')}:missing_metadata"
                    )
                else:
                    if metadata.get("mvp_tool_profile") is not True:
                        mixed_profile_entries.append(
                            f"{phase_name_for(phase)}/{category}/{case_name}#run{run.get('run_index', '?')}:mvp_tool_profile=false"
                        )
                    declared_tools = metadata.get("model_tool_names")
                    if isinstance(declared_tools, list):
                        declared = {str(name) for name in declared_tools}
                        if declared != MVP_RELEASE_MODEL_TOOLS:
                            mixed_profile_entries.append(
                                f"{phase_name_for(phase)}/{category}/{case_name}#run{run.get('run_index', '?')}:tool_surface_mismatch"
                            )
                    release_profile = str(
                        metadata.get("release_profile", "BETA_COMPLEX_MUTATION")
                    )
                    manifest = manifests.get(release_profile)
                    qualified = (
                        f"{phase_name_for(phase)}/{category}/{case_name}"
                        f"#run{run.get('run_index', '?')}"
                    )
                    if metadata.get("mvp_tool_profile") is True:
                        raw_errors = _raw_tool_history_errors(
                            run,
                        )
                        for error in raw_errors:
                            if error == "raw_legacy_tools":
                                raw_legacy_tool_entries.append(f"{qualified}:{error}")
                            else:
                                raw_tool_history_entries.append(f"{qualified}:{error}")
                    if (
                        manifest is None
                        and release_profile.startswith("R")
                        and phase not in {71, 72}
                    ):
                        manifest_missing_entries.append(f"{qualified}:missing_manifest")
                    elif manifest is not None:
                        status = manifest.get("status")
                        if isinstance(status, str) and status:
                            observed_profile_statuses[status].add(release_profile)
                            if status.startswith("diagnostic-"):
                                diagnostic_case_keys.add(
                                    f"{phase_name_for(phase)}/{category}/{case_name}"
                                )
                        if manifest.get("release_gating") is False:
                            observed_profile_statuses["not release-gating"].add(
                                release_profile
                            )
                        required_dims = manifest.get("required_dimensions", [])
                        if isinstance(required_dims, list):
                            for dim in required_dims:
                                if not isinstance(run.get(str(dim)), bool):
                                    manifest_dimension_entries.append(
                                        f"{qualified}:missing_dimension:{dim}"
                                    )
                        forbidden_errors = _forbidden_raw_tool_errors(
                            run,
                            manifest.get("forbidden_raw_tools", []),
                        )
                        manifest_forbidden_tool_entries.extend(
                            f"{qualified}:{error}" for error in forbidden_errors
                        )
                    if not _scope_matches(metadata, scope):
                        continue
            grouped[phase][(category, case_name)].append(run)

    phase_reports: dict[str, Any] = {}
    unstable_cases: list[str] = []
    short_run_cases: list[str] = []
    diagnostic_unstable_cases: list[str] = []
    diagnostic_short_run_cases: list[str] = []
    total_model_attempts = 0
    total_model_passes = 0
    total_infra_failures = 0
    total_scheduled_runs = 0
    release_gating_infra_failures = 0
    release_gating_total_scheduled_runs = 0

    for phase in sorted(grouped):
        phase_name = phase_name_for(phase)
        case_reports: dict[str, Any] = {}
        phase_unstable_cases: list[str] = []
        phase_short_run_cases: list[str] = []
        phase_release_gating_unstable_cases: list[str] = []
        phase_release_gating_short_run_cases: list[str] = []
        phase_diagnostic_unstable_cases: list[str] = []
        phase_diagnostic_short_run_cases: list[str] = []
        phase_model_attempts = 0
        phase_model_passes = 0
        phase_infra_failures = 0
        phase_total_scheduled = 0
        phase_release_gating_infra_failures = 0
        phase_release_gating_total_scheduled = 0

        for (category, case_name), runs in sorted(grouped[phase].items()):
            runs = sorted(runs, key=lambda run: int(run.get("run_index", 0)))
            stability = case_run_stability(
                runs,
                threshold=stability_threshold,
            )
            case_key = f"{category}/{case_name}"
            qualified_key = f"{phase_name}/{case_key}"
            run_count_ok = stability["total_scheduled_runs"] >= min_runs_per_case
            stable = bool(stability["stable"] and run_count_ok)
            report = {
                **stability,
                "run_count_ok": run_count_ok,
                "stable": stable,
            }
            case_reports[case_key] = report
            diagnostic_case = qualified_key in diagnostic_case_keys

            phase_model_attempts += int(stability["model_attempts"])
            phase_model_passes += int(stability["model_passes"])
            phase_infra_failures += int(stability["infra_failures"])
            phase_total_scheduled += int(stability["total_scheduled_runs"])
            if not diagnostic_case:
                phase_release_gating_infra_failures += int(stability["infra_failures"])
                phase_release_gating_total_scheduled += int(
                    stability["total_scheduled_runs"]
                )
            if not stability["stable"]:
                phase_unstable_cases.append(case_key)
                if diagnostic_case:
                    phase_diagnostic_unstable_cases.append(case_key)
                    diagnostic_unstable_cases.append(qualified_key)
                else:
                    phase_release_gating_unstable_cases.append(case_key)
                    unstable_cases.append(qualified_key)
            if not run_count_ok:
                phase_short_run_cases.append(case_key)
                if diagnostic_case:
                    phase_diagnostic_short_run_cases.append(case_key)
                    diagnostic_short_run_cases.append(qualified_key)
                else:
                    phase_release_gating_short_run_cases.append(case_key)
                    short_run_cases.append(qualified_key)

        phase_release_ready = (
            bool(case_reports)
            and not phase_release_gating_unstable_cases
            and not phase_release_gating_short_run_cases
        )
        phase_reports[str(phase)] = {
            "phase": phase,
            "name": phase_name,
            "release_ready": phase_release_ready,
            "case_count": len(case_reports),
            "model_attempts": phase_model_attempts,
            "model_passes": phase_model_passes,
            "infra_failures": phase_infra_failures,
            "total_scheduled_runs": phase_total_scheduled,
            "model_pass_rate": _ratio(phase_model_passes, phase_model_attempts),
            "unstable_cases": phase_unstable_cases,
            "short_run_cases": phase_short_run_cases,
            "release_gating_unstable_cases": phase_release_gating_unstable_cases,
            "release_gating_short_run_cases": phase_release_gating_short_run_cases,
            "diagnostic_unstable_cases": phase_diagnostic_unstable_cases,
            "diagnostic_short_run_cases": phase_diagnostic_short_run_cases,
            "cases": case_reports,
        }
        total_model_attempts += phase_model_attempts
        total_model_passes += phase_model_passes
        total_infra_failures += phase_infra_failures
        total_scheduled_runs += phase_total_scheduled
        release_gating_infra_failures += phase_release_gating_infra_failures
        release_gating_total_scheduled_runs += phase_release_gating_total_scheduled

    missing_required_phases = [
        phase for phase in required_phases if phase not in grouped
    ]
    release_ready = (
        malformed_entries == 0
        and not mixed_profile_entries
        and not raw_legacy_tool_entries
        and not raw_tool_history_entries
        and not manifest_missing_entries
        and not manifest_dimension_entries
        and not manifest_forbidden_tool_entries
        and not missing_required_phases
        and not unstable_cases
        and not short_run_cases
        and release_gating_infra_failures == 0
        and (
            release_gating_total_scheduled_runs > 0
            or total_scheduled_runs > 0
        )
    )

    return {
        "release_ready": release_ready,
        "required_phases": list(required_phases),
        "missing_required_phases": missing_required_phases,
        "min_runs_per_case": min_runs_per_case,
        "stability_threshold": stability_threshold,
        "model_attempts": total_model_attempts,
        "model_passes": total_model_passes,
        "infra_failures": total_infra_failures,
        "total_scheduled_runs": total_scheduled_runs,
        "release_gating_infra_failures": release_gating_infra_failures,
        "release_gating_total_scheduled_runs": release_gating_total_scheduled_runs,
        "model_pass_rate": _ratio(total_model_passes, total_model_attempts),
        "unstable_cases": unstable_cases,
        "short_run_cases": short_run_cases,
        "diagnostic_unstable_cases": diagnostic_unstable_cases,
        "diagnostic_short_run_cases": diagnostic_short_run_cases,
        "malformed_entries": malformed_entries,
        "mixed_profile_entries": mixed_profile_entries,
        "raw_legacy_tool_entries": raw_legacy_tool_entries,
        "raw_tool_history_entries": raw_tool_history_entries,
        "manifest_missing_entries": manifest_missing_entries,
        "manifest_dimension_entries": manifest_dimension_entries,
        "manifest_forbidden_tool_entries": manifest_forbidden_tool_entries,
        "capability_statuses": {
            status: sorted(profiles)
            for status, profiles in sorted(observed_profile_statuses.items())
        },
        "phases": phase_reports,
    }


def _raw_tool_history_errors(
    run: dict[str, Any],
    *,
    forbidden_raw_tools: Any = None,
) -> list[str]:
    """Return fail-closed raw tool history errors for one persisted run."""
    errors: list[str] = []
    raw_tools: set[str] = set()
    turn_results = run.get("turn_results")
    if not isinstance(turn_results, list):
        return ["missing_turn_results"]
    for turn_index, turn in enumerate(turn_results):
        if not isinstance(turn, dict):
            errors.append(f"turn{turn_index}:malformed_turn")
            continue
        for field in ("requested_tool_calls_raw", "executed_tool_calls_raw"):
            calls = turn.get(field)
            if not isinstance(calls, list):
                errors.append(f"turn{turn_index}:{field}:missing_or_malformed")
                continue
            for call_index, call in enumerate(calls):
                name = _raw_call_name(call)
                if name is None:
                    errors.append(f"turn{turn_index}:{field}[{call_index}]:malformed_call")
                    continue
                raw_tools.add(name)
                if name not in MVP_RELEASE_MODEL_TOOLS:
                    errors.append("raw_legacy_tools")
        trace = turn.get("trace")
        if isinstance(trace, dict):
            trace_calls = trace.get("raw_requested_tool_calls")
            if trace_calls is not None:
                if not isinstance(trace_calls, list):
                    errors.append(f"turn{turn_index}:trace.raw_requested_tool_calls:malformed")
                else:
                    for call_index, call in enumerate(trace_calls):
                        name = _raw_call_name(call)
                        if name is None:
                            errors.append(
                                f"turn{turn_index}:trace.raw_requested_tool_calls[{call_index}]:malformed_call"
                            )
                            continue
                        raw_tools.add(name)
                        if name not in MVP_RELEASE_MODEL_TOOLS:
                            errors.append("raw_legacy_tools")
            executed_tools = trace.get("executed_tools")
            if executed_tools is not None:
                if not isinstance(executed_tools, list) or not all(
                    isinstance(name, str) for name in executed_tools
                ):
                    errors.append(f"turn{turn_index}:trace.executed_tools:malformed")
                else:
                    for name in executed_tools:
                        raw_tools.add(name)
                        if name not in MVP_RELEASE_MODEL_TOOLS:
                            errors.append("raw_legacy_tools")
    forbidden = (
        {str(tool) for tool in forbidden_raw_tools}
        if isinstance(forbidden_raw_tools, list)
        else set()
    )
    overlap = sorted(raw_tools & forbidden)
    if overlap:
        errors.append(f"forbidden_raw_tools:{','.join(overlap)}")
    return sorted(set(errors))


def _forbidden_raw_tool_errors(
    run: dict[str, Any],
    forbidden_raw_tools: Any,
) -> list[str]:
    return [
        error
        for error in _raw_tool_history_errors(
            run,
            forbidden_raw_tools=forbidden_raw_tools,
        )
        if error.startswith("forbidden_raw_tools:")
    ]


def _raw_call_name(call: Any) -> str | None:
    if not isinstance(call, dict):
        return None
    name = call.get("name")
    return name if isinstance(name, str) and name else None


def _scope_matches(metadata: dict[str, Any] | None, scope: str) -> bool:
    if scope == "all" or scope == "beta":
        return True
    profile = metadata.get("release_profile", "BETA_COMPLEX_MUTATION") if isinstance(metadata, dict) else "BETA_COMPLEX_MUTATION"
    if scope == "r0":
        return profile == "R0_READ_ONLY"
    if scope == "r1":
        return profile in {"R0_READ_ONLY", "R1_SET_PARAM_ONLY"}
    return True


def phase_name_for(phase: int) -> str:
    return PHASE_NAMES.get(phase, f"phase_{phase}")


def _run_from_store_entry(
    entry: dict[str, Any],
) -> tuple[int, str, str, dict[str, Any], dict[str, Any] | None] | None:
    phase = entry.get("phase")
    category = entry.get("category")
    case_name = entry.get("case_name")
    run_index = entry.get("run_index")
    if not isinstance(phase, int):
        return None
    if not isinstance(category, str) or not category:
        return None
    if not isinstance(case_name, str) or not case_name:
        return None
    if not isinstance(run_index, int):
        return None

    raw_run = entry.get("run_result")
    run = dict(raw_run) if isinstance(raw_run, dict) else {}
    status = entry.get("status") or run.get("status")
    if isinstance(status, str):
        run["status"] = status
    run["run_index"] = run_index
    metadata = entry.get("release_metadata")
    return phase, category, case_name, run, metadata if isinstance(metadata, dict) else None


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _load_stores(paths: list[str]) -> list[dict[str, Any]]:
    return [load_run_store(path) for path in paths]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a persisted live-eval release stability dashboard."
    )
    parser.add_argument(
        "--results-path",
        action="append",
        required=True,
        help="Persisted run-store JSON path. May be supplied multiple times.",
    )
    parser.add_argument(
        "--required-phase",
        type=int,
        action="append",
        default=None,
        help="Required live-eval phase number. May be supplied multiple times.",
    )
    parser.add_argument(
        "--min-runs-per-case",
        type=int,
        default=3,
        help="Minimum persisted runs required for each case. Default: 3.",
    )
    parser.add_argument(
        "--stability-threshold",
        type=float,
        default=1.0,
        help="Required per-case model pass rate. Default: 1.0.",
    )
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="Always exit 0 after printing the dashboard.",
    )
    parser.add_argument(
        "--scope",
        choices=("r0", "r1", "beta", "all"),
        default="all",
        help="Release scope filter. r0=read-only, r1=R0+simple-edit, beta=all, all=no filter. Default: all.",
    )
    args = parser.parse_args(argv)

    if args.min_runs_per_case < 1:
        parser.error("--min-runs-per-case must be >= 1")
    if not 0 < args.stability_threshold <= 1:
        parser.error("--stability-threshold must be in the range (0, 1].")

    if args.required_phase is not None:
        effective_required_phases = tuple(args.required_phase)
    elif args.scope in ("r0", "r1"):
        effective_required_phases = (20,)
    else:
        effective_required_phases = (20, 25, 35, 56, 57, 58, 59, 71, 72, 50)

    dashboard = build_release_dashboard(
        _load_stores(args.results_path),
        required_phases=effective_required_phases,
        min_runs_per_case=args.min_runs_per_case,
        stability_threshold=args.stability_threshold,
        scope=args.scope,
    )
    print(json.dumps(dashboard, indent=2, sort_keys=False))
    if args.no_fail or dashboard["release_ready"]:
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
