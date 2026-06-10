"""Production-oriented health checks for the packaged CLI app."""

from __future__ import annotations

import json
import shutil
import sys
from typing import Any

from grc_agent.config import ConfigError, load_app_config, resolve_config_path
from grc_agent.retrieval import initialize_retrieval

EXPECTED_PYTHON = (3, 12)
EXPECTED_GNURADIO = "3.10.9.2"


def _build_check(name: str, ok: bool, detail: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "ok": ok,
        "detail": detail,
    }
    payload.update(extra)
    return payload


def _check_python() -> dict[str, Any]:
    version = sys.version_info
    ok = (version.major == 3 and version.minor >= 12)
    detail = (
        f"{version.major}.{version.minor}.{version.micro} "
        f"(expected >= 3.12)"
    )
    return _build_check("Python version", ok, detail)


def _check_grcc() -> dict[str, Any]:
    grcc_path = shutil.which("grcc")
    ok = grcc_path is not None
    detail = grcc_path if grcc_path else "grcc not found on PATH"
    return _build_check("grcc on PATH", ok, detail, path=grcc_path)


def _check_gnuradio_import() -> dict[str, Any]:
    try:
        from gnuradio import gr

        version = gr.version()
    except Exception as exc:  # pragma: no cover - depends on host GNU install.
        return _build_check("GNU Radio import/version", False, str(exc))

    ok = version == EXPECTED_GNURADIO
    detail = f"{version} (expected {EXPECTED_GNURADIO})"
    return _build_check("GNU Radio import/version", ok, detail, version=version)


def _check_config(config_path: str | None = None) -> dict[str, Any]:
    try:
        config = load_app_config(config_path)
    except ConfigError as exc:
        return _build_check(
            "App config",
            False,
            f"{exc}. Run: uv run grc-agent doctor --skip-retrieval",
        )

    resolved_path = resolve_config_path(config_path)
    source = str(resolved_path) if resolved_path is not None else "built-in defaults"
    backend_ok = config.llama.server_url and config.llama.model
    return _build_check(
        "App config",
        True,
        source,
        source=source,
        backend_server_url=config.llama.server_url,
        backend_model=config.llama.model,
        backend=config.llama.backend,
        backend_configured=backend_ok,
    )


def _check_retrieval() -> dict[str, Any]:
    readiness = initialize_retrieval()
    if not readiness["ok"]:
        return _build_check(
            "Retrieval readiness",
            False,
            f"{readiness['message']}. Check GNU Radio install and grc_agent.toml.",
        )

    return _build_check(
        "Retrieval readiness",
        True,
        str(readiness["catalog_root"]),
        catalog_root=readiness["catalog_root"],
        catalog_files=readiness["catalog_files"],
    )


def _check_llama_server(config_path: str | None = None) -> dict[str, Any]:
    backend = "ollama"
    try:
        from grc_agent.config import load_app_config as _load
        from grc_agent.startup import bootstrap_runtime

        config = _load(config_path)
        backend = config.llama.backend
        result = bootstrap_runtime(config, init_retrieval=False)

        detail = f"{result.model_alias} at {result.server_url} ({result.launch_status})"

        check_name = "Ollama server"
        if backend == "openrouter":
            check_name = "OpenRouter API"

        return _build_check(
            check_name,
            result.launch_status != "failed",
            detail,
        )
    except Exception as exc:
        check_name = "Ollama server"
        if backend == "openrouter":
            check_name = "OpenRouter API"

        help_msg = "Ensure Ollama is running and has the configured model pulled."
        if backend == "openrouter":
            help_msg = "Ensure internet connectivity and a valid OpenRouter API key config in .env."

        return _build_check(
            check_name,
            False,
            f"{exc}. {help_msg}",
        )


def run_doctor(
    *,
    config_path: str | None = None,
    check_retrieval: bool = True,
    check_llama: bool = True,
) -> dict[str, Any]:
    """Run the packaged-app health checks and return a structured report."""
    checks = [
        _check_python(),
        _check_grcc(),
        _check_gnuradio_import(),
        _check_config(config_path),
    ]
    if check_retrieval:
        checks.append(_check_retrieval())
    if check_llama:
        checks.append(_check_llama_server(config_path))

    ok = all(check["ok"] for check in checks)
    return {
        "ok": ok,
        "checks": checks,
        "summary": "Environment OK" if ok else "Environment check failed",
    }


def print_doctor_report(report: dict[str, Any], *, json_output: bool = False) -> None:
    """Print the doctor report in JSON or compact human-readable form."""
    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
        return

    print("Checking local environment...\n")
    for check in report["checks"]:
        status = "PASS" if check["ok"] else "FAIL"
        print(f"[{status}] {check['name']}: {check['detail']}")
    print()
    print(report["summary"])


__all__ = ["print_doctor_report", "run_doctor"]
