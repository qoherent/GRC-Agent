"""CPU-only end-to-end runtime burn-in for GRC Agent.

This script is evidence tooling. It does not change runtime behavior. It starts
one CPU-only llama.cpp server per burn-in run, verifies readiness, runs the
required smoke/eval commands, writes JSON artifacts under an output directory,
and stops the server before the next run.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
from typing import Any
from urllib import error, request


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_DIR = Path("/tmp/grc_agent_phase21_burnin")
SERVER_URL = "http://127.0.0.1:8080"
MODEL_ALIAS = "unsloth/gemma-4-E2B-it-GGUF"
HF_MODEL = "unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL"
DESIRED_CONTEXT_TOKENS = 120000
CPU_LLAMA_COMMAND = [
    "llama-server",
    "-hf",
    HF_MODEL,
    "--alias",
    MODEL_ALIAS,
    "--host",
    "127.0.0.1",
    "--port",
    "8080",
    "--ctx-size",
    str(DESIRED_CONTEXT_TOKENS),
    "--device",
    "none",
    "--gpu-layers",
    "0",
    "--threads",
    "12",
    "--threads-batch",
    "12",
    "--jinja",
    "--no-mmproj",
]
SECRET_PATTERNS = {
    "ollama_key": re.compile(r"ollama_key", re.IGNORECASE),
    "OLLAMA_API_KEY": re.compile(r"OLLAMA_API_KEY"),
    "Authorization": re.compile(r"Authorization", re.IGNORECASE),
    "Bearer": re.compile(r"Bearer\s+", re.IGNORECASE),
    "sk_prefix": re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    "generic_api_key_assignment": re.compile(
        r"(?i)(api[_-]?key|token|secret)\s*[=:]\s*[A-Za-z0-9_./+=-]{16,}"
    ),
}


@dataclass(frozen=True)
class CommandSpec:
    name: str
    command: list[str]
    timeout_seconds: int
    json_output_path: Path | None = None


def _tail(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-limit:]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _run_command(spec: CommandSpec, *, run_dir: Path) -> dict[str, Any]:
    stdout_path = run_dir / f"{spec.name}.stdout"
    stderr_path = run_dir / f"{spec.name}.stderr"
    started = time.monotonic()
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    try:
        with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr_file:
            completed = subprocess.run(
                spec.command,
                cwd=ROOT,
                env=env,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                timeout=spec.timeout_seconds,
            )
        returncode: int | None = completed.returncode
        timeout = False
    except subprocess.TimeoutExpired:
        returncode = None
        timeout = True
    duration = round(time.monotonic() - started, 3)
    result: dict[str, Any] = {
        "command": spec.command,
        "returncode": returncode,
        "timeout": timeout,
        "duration_seconds": duration,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stdout_tail": _tail(stdout_path),
        "stderr_tail": _tail(stderr_path),
    }
    if spec.json_output_path is not None:
        result["json_output_path"] = str(spec.json_output_path)
        result["json_output"] = _read_json(spec.json_output_path)
    return result


def _pids_on_port(port: int = 8080) -> list[int]:
    command = ["lsof", "-ti", f"tcp:{port}"]
    if not _command_exists("lsof"):
        command = ["fuser", f"{port}/tcp"]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    pids: list[int] = []
    for token in completed.stdout.split():
        try:
            pids.append(int(token))
        except ValueError:
            continue
    return pids


def _command_exists(name: str) -> bool:
    return subprocess.run(
        ["bash", "-lc", f"command -v {name} >/dev/null 2>&1"],
        cwd=ROOT,
        check=False,
    ).returncode == 0


def _cmdline(pid: int) -> str:
    path = Path("/proc") / str(pid) / "cmdline"
    try:
        return path.read_text(encoding="utf-8", errors="replace").replace("\x00", " ")
    except OSError:
        return ""


def stop_llama_on_port() -> list[dict[str, Any]]:
    stopped: list[dict[str, Any]] = []
    for pid in _pids_on_port(8080):
        cmdline = _cmdline(pid)
        if "llama-server" not in cmdline:
            raise RuntimeError(f"non-llama process is listening on port 8080: pid={pid}")
        status = {"pid": pid, "cmdline": cmdline, "terminated": False, "killed": False}
        try:
            os.kill(pid, signal.SIGTERM)
            status["terminated"] = True
        except ProcessLookupError:
            stopped.append(status)
            continue
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if not _pid_alive(pid):
                break
            time.sleep(0.25)
        if _pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
            status["killed"] = True
        stopped.append(status)
    return stopped


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _props() -> dict[str, Any]:
    with request.urlopen(f"{SERVER_URL}/props", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("/props returned non-object JSON")
    return payload


def _actual_context(props: dict[str, Any]) -> int | None:
    settings = props.get("default_generation_settings")
    if isinstance(settings, dict):
        n_ctx = settings.get("n_ctx")
        if isinstance(n_ctx, int) and n_ctx > 0:
            return n_ctx
        params = settings.get("params")
        if isinstance(params, dict):
            n_ctx = params.get("n_ctx")
            if isinstance(n_ctx, int) and n_ctx > 0:
                return n_ctx
    settings = props.get("settings")
    if isinstance(settings, dict):
        n_ctx = settings.get("n_ctx")
        if isinstance(n_ctx, int) and n_ctx > 0:
            return n_ctx
    return None


def start_cpu_llama(*, run_dir: Path, startup_timeout_seconds: int) -> dict[str, Any]:
    log_path = run_dir / "llama_server_cpu.log"
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            CPU_LLAMA_COMMAND,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
        )
    deadline = time.monotonic() + startup_timeout_seconds
    last_error = "not polled"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "llama-server exited before readiness: "
                f"pid={process.pid}, returncode={process.returncode}, "
                f"log_tail={_tail(log_path)}"
            )
        try:
            props = _props()
        except (OSError, error.URLError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            time.sleep(2)
            continue
        actual_context = _actual_context(props)
        return {
            "pid": process.pid,
            "command": CPU_LLAMA_COMMAND,
            "log_path": str(log_path),
            "startup_seconds": round(time.monotonic() - started, 3),
            "props_path": str(run_dir / "llama_props.json"),
            "props": {
                "actual_context_tokens": actual_context,
                "desired_context_tokens": DESIRED_CONTEXT_TOKENS,
                "context_verified": (
                    actual_context is not None
                    and actual_context >= DESIRED_CONTEXT_TOKENS
                ),
                "model_path": props.get("model_path"),
            },
        }
    raise RuntimeError(
        f"timed out waiting for llama-server readiness: {last_error}; "
        f"log_tail={_tail(log_path)}"
    )


def summarize_live_eval_store(path: Path) -> dict[str, Any]:
    payload = _read_json(path) or {}
    runs = payload.get("runs")
    if not isinstance(runs, list):
        return {"ok": False, "error": "missing runs"}
    status_counts: dict[str, int] = {}
    backend_restarts = 0
    dimension_counts = {
        "runtime_safety_pass": 0,
        "model_contract_pass": 0,
        "semantic_pass": 0,
        "tool_success_pass": 0,
    }
    for entry in runs:
        if not isinstance(entry, dict):
            continue
        run = entry.get("run_result") if isinstance(entry.get("run_result"), dict) else entry
        status = str(entry.get("status") or run.get("status"))
        status_counts[status] = status_counts.get(status, 0) + 1
        backend_restarts += int(run.get("backend_restart_count") or 0)
        for key in dimension_counts:
            if run.get(key) is True:
                dimension_counts[key] += 1
    return {
        "ok": bool(runs) and set(status_counts) == {"PASS"} and backend_restarts == 0,
        "runs": len(runs),
        "status_counts": status_counts,
        "backend_restarts": backend_restarts,
        "dimensions": dimension_counts,
    }


def summarize_gameplay(path: Path) -> dict[str, Any]:
    payload = _read_json(path) or {}
    judge = payload.get("judge_result")
    if not isinstance(judge, dict):
        judge = payload.get("judge") if isinstance(payload.get("judge"), dict) else {}
    return {
        "ok": judge.get("passed") is True,
        "scenario_id": payload.get("scenario_id")
        or (payload.get("scenario") or {}).get("scenario_id"),
        "forbidden_events": payload.get("forbidden_events") or judge.get("forbidden_events") or [],
        "judge": judge,
    }


def secret_scan(paths: list[Path]) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits = [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text)]
        results[str(path)] = hits
    return results


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _command_json_stdout(step: dict[str, Any]) -> dict[str, Any] | None:
    stdout_path = step.get("stdout_path")
    if not isinstance(stdout_path, str):
        return None
    return _read_json(Path(stdout_path))


def run_one_burnin(
    *,
    run_number: int,
    artifact_dir: Path,
    startup_timeout_seconds: int,
) -> dict[str, Any]:
    run_dir = artifact_dir / f"run_{run_number:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    stopped_before = stop_llama_on_port()
    server: dict[str, Any] | None = None
    steps: dict[str, Any] = {}
    ok = False
    failure: str | None = None
    try:
        steps["doctor"] = _run_command(
            CommandSpec("doctor", ["uv", "run", "grc-agent", "doctor"], 120),
            run_dir=run_dir,
        )
        steps["vector_stats_pre"] = _run_command(
            CommandSpec(
                "vector_stats_pre",
                ["uv", "run", "grc-agent", "vector", "stats", "--json"],
                120,
            ),
            run_dir=run_dir,
        )
        server = start_cpu_llama(
            run_dir=run_dir,
            startup_timeout_seconds=startup_timeout_seconds,
        )
        props_payload = _props()
        _write_json(run_dir / "llama_props.json", props_payload)
        steps["health"] = _run_command(
            CommandSpec("health", ["uv", "run", "grc-agent", "health"], 120),
            run_dir=run_dir,
        )
        steps["release_manifest"] = _run_command(
            CommandSpec(
                "release_manifest",
                ["uv", "run", "grc-agent", "release-manifest"],
                120,
            ),
            run_dir=run_dir,
        )

        install_smoke_path = run_dir / "install_smoke_end_to_end.json"
        steps["install_smoke"] = _run_command(
            CommandSpec(
                "install_smoke",
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "tests.production.install_smoke",
                    "--mode",
                    "system-site-venv",
                    "--build-vector-index",
                    "--require-vector-index",
                    "--require-llama",
                    "--timeout-seconds",
                    "900",
                    "--output",
                    str(install_smoke_path),
                ],
                1200,
                json_output_path=install_smoke_path,
            ),
            run_dir=run_dir,
        )
        steps["vector_regression"] = _run_command(
            CommandSpec(
                "vector_regression",
                ["uv", "run", "python", "-m", "tests.retrieval_eval.vector_regression"],
                900,
            ),
            run_dir=run_dir,
        )

        r0_path = run_dir / "r0_store.json"
        steps["r0"] = _run_command(
            CommandSpec(
                "r0",
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "tests.llama_eval.run_r0_release",
                    "--n-runs",
                    "3",
                    "--max-tokens",
                    "512",
                    "--results-path",
                    str(r0_path),
                ],
                5400,
                json_output_path=r0_path,
            ),
            run_dir=run_dir,
        )
        r0_dashboard_path = run_dir / "r0_dashboard.json"
        steps["r0_dashboard"] = _run_command(
            CommandSpec(
                "r0_dashboard",
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "tests.llama_eval.release_dashboard",
                    "--scope",
                    "r0",
                    "--results-path",
                    str(r0_path),
                    "--min-runs-per-case",
                    "3",
                    "--stability-threshold",
                    "1.0",
                ],
                120,
            ),
            run_dir=run_dir,
        )
        Path(steps["r0_dashboard"]["stdout_path"]).replace(r0_dashboard_path)
        steps["r0_dashboard"]["json_output_path"] = str(r0_dashboard_path)
        steps["r0_dashboard"]["json_output"] = _read_json(r0_dashboard_path)

        r1_path = run_dir / "r1_set_param_store.json"
        steps["r1_set_param"] = _run_command(
            CommandSpec(
                "r1_set_param",
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "tests.llama_eval.run_r1_release",
                    "--n-runs",
                    "3",
                    "--max-tokens",
                    "512",
                    "--results-path",
                    str(r1_path),
                ],
                1800,
                json_output_path=r1_path,
            ),
            run_dir=run_dir,
        )
        r1_dashboard_path = run_dir / "r1_set_param_dashboard.json"
        steps["r1_set_param_dashboard"] = _run_command(
            CommandSpec(
                "r1_set_param_dashboard",
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "tests.llama_eval.release_dashboard",
                    "--scope",
                    "r1",
                    "--results-path",
                    str(r1_path),
                    "--min-runs-per-case",
                    "3",
                    "--stability-threshold",
                    "1.0",
                ],
                120,
            ),
            run_dir=run_dir,
        )
        Path(steps["r1_set_param_dashboard"]["stdout_path"]).replace(r1_dashboard_path)
        steps["r1_set_param_dashboard"]["json_output_path"] = str(r1_dashboard_path)
        steps["r1_set_param_dashboard"]["json_output"] = _read_json(r1_dashboard_path)

        gameplay_path = run_dir / "gameplay_read_only.json"
        steps["gameplay"] = _run_command(
            CommandSpec(
                "gameplay",
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "tests.production.gameplay_runner",
                    "--scenario",
                    "tests/production/scenarios/read_only_explain.json",
                    "--artifact",
                    str(gameplay_path),
                ],
                300,
                json_output_path=gameplay_path,
            ),
            run_dir=run_dir,
        )
        debug_bundle_path = run_dir / "debug_bundle.json"
        steps["debug_bundle"] = _run_command(
            CommandSpec(
                "debug_bundle",
                ["uv", "run", "grc-agent", "debug-bundle", "--output", str(debug_bundle_path)],
                120,
                json_output_path=debug_bundle_path,
            ),
            run_dir=run_dir,
        )

        scan_paths = [
            run_dir / "llama_server_cpu.log",
            install_smoke_path,
            r0_path,
            r0_dashboard_path,
            r1_path,
            r1_dashboard_path,
            gameplay_path,
            debug_bundle_path,
        ]
        scan = secret_scan(scan_paths)
        summaries = {
            "health": _command_json_stdout(steps["health"]),
            "release_manifest": _command_json_stdout(steps["release_manifest"]),
            "install_smoke": steps["install_smoke"].get("json_output"),
            "vector_regression": _command_json_stdout(steps["vector_regression"]),
            "r0": summarize_live_eval_store(r0_path),
            "r0_dashboard": steps["r0_dashboard"].get("json_output"),
            "r1_set_param": summarize_live_eval_store(r1_path),
            "r1_set_param_dashboard": steps["r1_set_param_dashboard"].get("json_output"),
            "gameplay": summarize_gameplay(gameplay_path),
            "debug_bundle": steps["debug_bundle"].get("json_output"),
            "secret_scan": scan,
        }
        failures = _run_failures(steps=steps, summaries=summaries, server=server)
        if any(scan.values()):
            failures.append("secret_scan_failed")
        ok = not failures
        failure = "; ".join(failures) if failures else None
        result = {
            "run_number": run_number,
            "ok": ok,
            "failure": failure,
            "artifact_dir": str(run_dir),
            "stopped_before": stopped_before,
            "server": server,
            "steps": steps,
            "summaries": summaries,
            "stopped_after": [],
        }
    except Exception as exc:  # noqa: BLE001 - evidence runner must record failures.
        failure = str(exc)
        result = {
            "run_number": run_number,
            "ok": False,
            "failure": failure,
            "artifact_dir": str(run_dir),
            "stopped_before": stopped_before,
            "server": server,
            "steps": steps,
            "summaries": {},
            "stopped_after": [],
        }
    finally:
        stopped_after = stop_llama_on_port()
    result["stopped_after"] = stopped_after
    _write_json(run_dir / "run_summary.json", result)
    return result


def _run_failures(
    *,
    steps: dict[str, Any],
    summaries: dict[str, Any],
    server: dict[str, Any] | None,
) -> list[str]:
    failures: list[str] = []
    for name, step in steps.items():
        if step.get("returncode") != 0:
            failures.append(f"{name}_failed")
        if step.get("timeout"):
            failures.append(f"{name}_timeout")
    if not server or not (server.get("props") or {}).get("context_verified"):
        failures.append("server_context_not_verified")
    health = summaries.get("health")
    if not isinstance(health, dict) or health.get("status") != "ok":
        failures.append("health_not_ok")
    manifest = summaries.get("release_manifest")
    if not isinstance(manifest, dict) or (manifest.get("git") or {}).get("dirty") is not False:
        failures.append("release_manifest_dirty_or_missing")
    install = summaries.get("install_smoke")
    if not isinstance(install, dict) or (install.get("readiness") or {}).get("end_to_end_ready") is not True:
        failures.append("install_smoke_not_end_to_end_ready")
    vector = summaries.get("vector_regression")
    if not isinstance(vector, dict) or vector.get("ok") is not True:
        failures.append("vector_regression_not_ok")
    r0 = summaries.get("r0")
    if not isinstance(r0, dict) or r0.get("ok") is not True:
        failures.append("r0_not_ok")
    r0_dashboard = summaries.get("r0_dashboard")
    if not isinstance(r0_dashboard, dict) or r0_dashboard.get("release_ready") is not True:
        failures.append("r0_dashboard_not_ready")
    r1 = summaries.get("r1_set_param")
    if not isinstance(r1, dict) or r1.get("ok") is not True:
        failures.append("r1_set_param_not_ok")
    r1_dashboard = summaries.get("r1_set_param_dashboard")
    if not isinstance(r1_dashboard, dict) or r1_dashboard.get("release_ready") is not True:
        failures.append("r1_set_param_dashboard_not_ready")
    gameplay = summaries.get("gameplay")
    if not isinstance(gameplay, dict) or gameplay.get("ok") is not True:
        failures.append("gameplay_not_ok")
    debug_bundle = summaries.get("debug_bundle")
    if not isinstance(debug_bundle, dict) or debug_bundle.get("ok") is not True:
        failures.append("debug_bundle_not_ok")
    return failures


def build_aggregate(runs: list[dict[str, Any]], artifact_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": "2026-05-21.phase21-cpu-burnin-v1",
        "artifact_dir": str(artifact_dir),
        "cpu_llama_command": CPU_LLAMA_COMMAND,
        "vulkan_status": "rejected_experimental_vk_device_lost",
        "total_runs": len(runs),
        "passed_runs": sum(1 for run in runs if run.get("ok") is True),
        "all_passed": bool(runs) and all(run.get("ok") is True for run in runs),
        "runs": runs,
    }


def run_burnin(
    *,
    runs: int,
    artifact_dir: Path,
    startup_timeout_seconds: int,
) -> dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for run_number in range(1, runs + 1):
        print(f"[phase21] starting CPU burn-in run {run_number}/{runs}", flush=True)
        result = run_one_burnin(
            run_number=run_number,
            artifact_dir=artifact_dir,
            startup_timeout_seconds=startup_timeout_seconds,
        )
        results.append(result)
        print(
            f"[phase21] run {run_number}/{runs}: "
            f"{'PASS' if result.get('ok') else 'FAIL'}",
            flush=True,
        )
        if not result.get("ok"):
            break
    aggregate = build_aggregate(results, artifact_dir)
    _write_json(artifact_dir / "aggregate.json", aggregate)
    return aggregate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    parser.add_argument("--startup-timeout-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be >= 1")
    aggregate = run_burnin(
        runs=args.runs,
        artifact_dir=Path(args.artifact_dir),
        startup_timeout_seconds=args.startup_timeout_seconds,
    )
    print(json.dumps(aggregate, indent=2, sort_keys=True))
    return 0 if aggregate["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
