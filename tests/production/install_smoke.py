"""Clean-workspace install and ops smoke for GRC Agent.

This smoke is evidence-oriented. It records expected missing dependencies, such
as GNU Radio Python bindings or an unavailable llama.cpp server, without
claiming runtime readiness.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
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
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
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


def run_install_smoke(
    *,
    output_path: Path | None = None,
    keep_workspace: bool = False,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    parent = Path(tempfile.mkdtemp(prefix="grc_agent_install_smoke_"))
    workspace = parent / "workspace"
    shutil.copytree(ROOT, workspace, ignore=_ignore)
    steps: dict[str, dict[str, Any]] = {}
    commands = {
        "uv_sync": ["uv", "sync", "--locked"],
        "help": ["uv", "run", "grc-agent", "--help"],
        "doctor": ["uv", "run", "grc-agent", "doctor", "--json"],
        "health": ["uv", "run", "grc-agent", "health"],
        "production_tests": ["uv", "run", "python", "-m", "unittest", "tests.production"],
    }
    for name, command in commands.items():
        steps[name] = _run(command, cwd=workspace, timeout_seconds=timeout_seconds)
    expected_failures = {
        "doctor": _classify_doctor(steps["doctor"]) if steps["doctor"]["returncode"] else [],
        "health": _classify_health(steps["health"]) if steps["health"]["returncode"] else [],
    }
    result = {
        "schema_version": "2026-05-20.phase18-install-smoke-v1",
        "ok": (
            steps["uv_sync"]["returncode"] == 0
            and steps["help"]["returncode"] == 0
            and steps["production_tests"]["returncode"] == 0
        ),
        "workspace": str(workspace),
        "workspace_kept": keep_workspace,
        "steps": steps,
        "expected_failures": expected_failures,
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if not keep_workspace:
        result["workspace_removed"] = True
        shutil.rmtree(parent, ignore_errors=True)
    else:
        result["workspace_removed"] = False
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Optional JSON output path.")
    parser.add_argument("--keep-workspace", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args(argv)
    result = run_install_smoke(
        output_path=Path(args.output) if args.output else None,
        keep_workspace=args.keep_workspace,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
