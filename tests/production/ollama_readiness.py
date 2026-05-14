"""Secret-safe Ollama Cloud readiness helpers for Phase 2 evidence harnesses."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request

OLLAMA_DOTENV_KEY = "ollama_key"
OLLAMA_ENV_KEY = "OLLAMA_API_KEY"
OLLAMA_CLOUD_TAGS_URL = "https://ollama.com/api/tags"


def _read_dotenv_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        cleaned = value.strip().strip("'\"")
        return cleaned or None
    return None


def prepare_ollama_cloud_environment(
    *,
    env_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Map the repo-local Ollama key to the official env name in process only."""
    target_env = os.environ if environ is None else environ
    dotenv = env_path or Path.cwd() / ".env"
    value = _read_dotenv_value(dotenv, OLLAMA_DOTENV_KEY)
    present = bool(value)
    mapped = False
    if value and not target_env.get(OLLAMA_ENV_KEY):
        target_env[OLLAMA_ENV_KEY] = value
        mapped = True
    return {
        "cloud_key_present": present,
        "mapped_in_process": mapped,
        "network_checked": False,
    }


def check_ollama_cloud_reachable(
    *,
    environ: dict[str, str] | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Optionally check Cloud reachability without exposing credentials."""
    target_env = os.environ if environ is None else environ
    token = target_env.get(OLLAMA_ENV_KEY)
    if not token:
        return {
            "network_checked": True,
            "reachable": False,
            "error_type": "missing_key",
        }
    req = request.Request(
        OLLAMA_CLOUD_TAGS_URL,
        headers={"Authorization": "Bearer " + token},
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            return {
                "network_checked": True,
                "reachable": 200 <= int(response.status) < 500,
                "status_code": int(response.status),
            }
    except error.HTTPError as exc:
        return {
            "network_checked": True,
            "reachable": False,
            "status_code": int(exc.code),
            "error_type": "http_error",
        }
    except OSError:
        return {
            "network_checked": True,
            "reachable": False,
            "error_type": "network_error",
        }


def readiness_report(
    *,
    env_path: Path | None = None,
    check_cloud: bool = False,
) -> dict[str, Any]:
    """Return a redacted Ollama readiness report."""
    status = prepare_ollama_cloud_environment(env_path=env_path)
    if check_cloud:
        status.update(check_ollama_cloud_reachable())
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-path", type=Path, default=Path.cwd() / ".env")
    parser.add_argument(
        "--check-ollama-cloud",
        action="store_true",
        help="Perform an explicit network reachability check. Disabled by default.",
    )
    args = parser.parse_args(argv)
    print(
        json.dumps(
            readiness_report(
                env_path=args.env_path,
                check_cloud=bool(args.check_ollama_cloud),
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
