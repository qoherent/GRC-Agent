"""Clean-workspace install and ops smoke for GRC Agent.

This smoke is evidence-oriented. It records expected missing dependencies, such
as GNU Radio Python bindings or an unavailable llama.cpp server, without
claiming runtime readiness.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UV_MODE = "default-uv"
SYSTEM_SITE_VENV_MODE = "system-site-venv"
INSTALL_SMOKE_SCHEMA_VERSION = "2026-05-21.phase20-install-smoke-v4"
DEFAULT_IGNORE = {
    ".git",
    ".venv",
    ".grc_agent",
    ".llama_eval",
    ".ruff_cache",
    ".cache",
    "reports",
    "tmp",
    "__pycache__",
}


def _ignore(_directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in DEFAULT_IGNORE}
    ignored.update(name for name in names if name.endswith((".pyc", ".pyo")))
    return ignored


def _run(command: list[str], *, cwd: Path, timeout_seconds: int) -> dict[str, Any]:
    started = time.monotonic()
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returncode": None,
            "timeout": True,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
        }
    return {
        "command": command,
        "returncode": completed.returncode,
        "timeout": False,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def _json_from_stdout(step: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return json.loads(str(step.get("stdout_tail") or ""))
    except json.JSONDecodeError:
        return None


def _classify_doctor(step: dict[str, Any]) -> list[str]:
    payload = _json_from_stdout(step)
    if not isinstance(payload, dict):
        return ["doctor_output_not_json"]
    failures: list[str] = []
    for check in payload.get("checks", []):
        if not isinstance(check, dict) or check.get("ok") is True:
            continue
        name = str(check.get("name", "")).lower()
        if "gnu radio" in name:
            failures.append("missing_gnuradio_python_bindings")
        elif "grcc" in name:
            failures.append("missing_grcc")
        elif "retrieval" in name:
            failures.append("missing_retrieval_catalog")
        else:
            failures.append("doctor_check_failed")
    return failures


def _classify_health(step: dict[str, Any]) -> list[str]:
    payload = _json_from_stdout(step)
    if not isinstance(payload, dict):
        return ["health_output_not_json"]
    reasons = payload.get("status_reasons")
    if isinstance(reasons, list):
        return [str(reason) for reason in reasons]
    return []


def _doctor_check(payload: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    for check in payload.get("checks", []):
        if isinstance(check, dict) and str(check.get("name")) == name:
            return check
    return None


def _build_readiness_summary(steps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    doctor_payload = _json_from_stdout(steps.get("doctor", {}))
    health_payload = _json_from_stdout(steps.get("health", {}))
    gnu_radio_check = _doctor_check(doctor_payload, "GNU Radio import/version")
    grcc_check = _doctor_check(doctor_payload, "grcc on PATH")
    retrieval_check = _doctor_check(doctor_payload, "Retrieval readiness")
    package_ready = (
        steps.get("uv_sync", {}).get("returncode") == 0
        and steps.get("help", {}).get("returncode") == 0
        and steps.get("production_tests", {}).get("returncode") == 0
    )
    gnu_radio_ready = bool(gnu_radio_check and gnu_radio_check.get("ok") is True)
    grcc_ready = bool(grcc_check and grcc_check.get("ok") is True)
    retrieval_ready = bool(retrieval_check and retrieval_check.get("ok") is True)
    health_status = (
        health_payload.get("status") if isinstance(health_payload, dict) else None
    )
    llama_ready = health_status == "ok"
    context_verified = bool(
        isinstance(health_payload, dict)
        and health_payload.get("context_verified") is True
    )
    vector_stats = steps.get("vector_stats", {})
    vector_index_ready = vector_stats.get("returncode") == 0
    retrieval_catalog_ready = retrieval_ready
    model_runtime_ready = llama_ready and context_verified
    end_to_end_ready = (
        package_ready
        and gnu_radio_ready
        and grcc_ready
        and retrieval_catalog_ready
        and vector_index_ready
        and model_runtime_ready
    )
    if (
        package_ready
        and gnu_radio_ready
        and grcc_ready
        and retrieval_ready
        and vector_index_ready
        and model_runtime_ready
    ):
        classification = "runtime_ready"
    elif package_ready and gnu_radio_ready and grcc_ready and retrieval_ready:
        classification = "package_ready_runtime_not_ready"
    elif package_ready:
        classification = "package_ready_missing_runtime_dependencies"
    else:
        classification = "package_not_ready"
    return {
        "package_ready": package_ready,
        "gnu_radio_ready": gnu_radio_ready,
        "gnu_radio_detail": gnu_radio_check.get("detail") if gnu_radio_check else None,
        "grcc_ready": grcc_ready,
        "grcc_path": grcc_check.get("path") if grcc_check else None,
        "retrieval_ready": retrieval_ready,
        "retrieval_catalog_ready": retrieval_catalog_ready,
        "vector_index_ready": vector_index_ready,
        "vector_index_state": "ready" if vector_index_ready else "missing_or_unavailable",
        "llama_ready": llama_ready,
        "health_status": health_status,
        "context_verified": context_verified,
        "model_runtime_ready": model_runtime_ready,
        "end_to_end_ready": end_to_end_ready,
        "overall_environment_classification": classification,
    }


def _system_python(default: str | None) -> str:
    if default:
        return default
    for candidate in ("/usr/bin/python3", shutil.which("python3"), sys.executable):
        if candidate and Path(candidate).exists():
            return candidate
    return sys.executable


def _smoke_ok(
    *,
    steps: dict[str, dict[str, Any]],
    mode: str,
    readiness: dict[str, Any],
    require_vector_index: bool,
    require_llama: bool,
) -> bool:
    return (
        steps["uv_sync"]["returncode"] == 0
        and steps["help"]["returncode"] == 0
        and steps["production_tests"]["returncode"] == 0
        and (
            mode != SYSTEM_SITE_VENV_MODE
            or steps["create_system_site_venv"]["returncode"] == 0
        )
        and (not require_vector_index or readiness["vector_index_ready"])
        and (not require_llama or readiness["model_runtime_ready"])
    )


def run_install_smoke(
    *,
    mode: str = DEFAULT_UV_MODE,
    python_executable: str | None = None,
    output_path: Path | None = None,
    keep_workspace: bool = False,
    timeout_seconds: int = 180,
    build_vector_index: bool = False,
    require_vector_index: bool = False,
    require_llama: bool = False,
) -> dict[str, Any]:
    if mode not in {DEFAULT_UV_MODE, SYSTEM_SITE_VENV_MODE}:
        raise ValueError(f"unsupported install smoke mode: {mode}")
    parent = Path(tempfile.mkdtemp(prefix="grc_agent_install_smoke_"))
    workspace = parent / "workspace"
    shutil.copytree(ROOT, workspace, ignore=_ignore)
    steps: dict[str, dict[str, Any]] = {}
    selected_python = _system_python(python_executable)
    if mode == SYSTEM_SITE_VENV_MODE:
        steps["create_system_site_venv"] = _run(
            [
                "uv",
                "venv",
                "--system-site-packages",
                "--python",
                selected_python,
            ],
            cwd=workspace,
            timeout_seconds=timeout_seconds,
        )
        uv_sync = ["uv", "sync", "--locked", "--python", ".venv/bin/python"]
    else:
        selected_python = None
        uv_sync = ["uv", "sync", "--locked"]
    commands = {
        "uv_sync": uv_sync,
        "help": ["uv", "run", "grc-agent", "--help"],
        "doctor": ["uv", "run", "grc-agent", "doctor", "--json"],
        "health": ["uv", "run", "grc-agent", "health"],
    }
    if build_vector_index:
        commands["vector_build"] = [
            "uv",
            "run",
            "grc-agent",
            "vector",
            "build",
            "--json",
        ]
    commands["vector_stats"] = [
        "uv",
        "run",
        "grc-agent",
        "vector",
        "stats",
        "--json",
    ]
    commands["production_tests"] = [
        "uv",
        "run",
        "python",
        "-m",
        "unittest",
        "tests.production",
    ]
    for name, command in commands.items():
        steps[name] = _run(command, cwd=workspace, timeout_seconds=timeout_seconds)
    expected_failures = {
        "doctor": _classify_doctor(steps["doctor"]) if steps["doctor"]["returncode"] else [],
        "health": _classify_health(steps["health"]) if steps["health"]["returncode"] else [],
        "vector_stats": (
            ["missing_vector_index"]
            if steps["vector_stats"]["returncode"]
            else []
        ),
    }
    readiness = _build_readiness_summary(steps)
    result = {
        "schema_version": INSTALL_SMOKE_SCHEMA_VERSION,
        "mode": mode,
        "selected_python": selected_python,
        "build_vector_index": build_vector_index,
        "require_vector_index": require_vector_index,
        "require_llama": require_llama,
        "ok": _smoke_ok(
            steps=steps,
            mode=mode,
            readiness=readiness,
            require_vector_index=require_vector_index,
            require_llama=require_llama,
        ),
        "workspace": str(workspace),
        "workspace_kept": keep_workspace,
        "steps": steps,
        "readiness": readiness,
        "expected_failures": expected_failures,
    }
    if not keep_workspace:
        result["workspace_removed"] = True
        shutil.rmtree(parent, ignore_errors=True)
    else:
        result["workspace_removed"] = False
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=[DEFAULT_UV_MODE, SYSTEM_SITE_VENV_MODE],
        default=DEFAULT_UV_MODE,
        help="Environment mode to smoke-test.",
    )
    parser.add_argument(
        "--python",
        dest="python_executable",
        help="Python executable for --mode system-site-venv. Defaults to python3 on PATH.",
    )
    parser.add_argument(
        "--build-vector-index",
        action="store_true",
        help="Also run vector index build. Off by default to avoid downloads/long runs.",
    )
    parser.add_argument(
        "--require-vector-index",
        action="store_true",
        help="Fail the smoke when vector stats reports a missing/unavailable index.",
    )
    parser.add_argument(
        "--require-llama",
        action="store_true",
        help=(
            "Fail the smoke unless grc-agent health reports llama reachable "
            "and actual context verified."
        ),
    )
    parser.add_argument("--output", help="Optional JSON output path.")
    parser.add_argument("--keep-workspace", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args(argv)
    result = run_install_smoke(
        mode=args.mode,
        python_executable=args.python_executable,
        output_path=Path(args.output) if args.output else None,
        keep_workspace=args.keep_workspace,
        timeout_seconds=args.timeout_seconds,
        build_vector_index=args.build_vector_index,
        require_vector_index=args.require_vector_index,
        require_llama=args.require_llama,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
