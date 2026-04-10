"""Load repo-backed runtime configuration for the local CLI paths."""

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any


CONFIG_FILE_NAME = "grc_agent.toml"


class ConfigError(RuntimeError):
    """Raised when the repo config file is missing or invalid."""


@dataclass(frozen=True)
class LlamaConfig:
    """Configurable defaults for the local llama.cpp runtime path."""

    server_url: str
    model: str
    max_steps: int
    max_tokens: int
    temperature: float
    enable_thinking: bool
    request_timeout_seconds: float


@dataclass(frozen=True)
class AppConfig:
    """Top-level repo config loaded from the workspace root."""

    llama: LlamaConfig


def default_config_path() -> Path:
    """Return the repo-level config path used by the CLI."""
    return Path(__file__).resolve().parents[2] / CONFIG_FILE_NAME


def load_app_config(config_path: str | Path | None = None) -> AppConfig:
    """Read the repo config and return validated runtime settings."""
    resolved_path = default_config_path() if config_path is None else Path(config_path).expanduser()

    try:
        with resolved_path.open("rb") as config_file:
            payload = tomllib.load(config_file)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {resolved_path}") from exc

    if not isinstance(payload, dict):
        raise ConfigError(f"Config file {resolved_path} must contain a top-level TOML table.")

    llama_table = _require_table(payload, "llama", context="root")
    return AppConfig(
        llama=LlamaConfig(
            server_url=_require_non_empty_string(llama_table, "server_url", context="[llama]"),
            model=_require_non_empty_string(llama_table, "model", context="[llama]"),
            max_steps=_require_positive_int(llama_table, "max_steps", context="[llama]"),
            max_tokens=_require_positive_int(llama_table, "max_tokens", context="[llama]"),
            temperature=_require_non_negative_float(
                llama_table,
                "temperature",
                context="[llama]",
            ),
            enable_thinking=_require_bool(llama_table, "enable_thinking", context="[llama]"),
            request_timeout_seconds=_require_positive_float(
                llama_table,
                "request_timeout_seconds",
                context="[llama]",
            ),
        )
    )


def _require_table(payload: dict[str, Any], key: str, *, context: str) -> dict[str, Any]:
    """Require one nested TOML table."""
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{context} must contain a [{key}] table.")
    return value


def _require_non_empty_string(payload: dict[str, Any], key: str, *, context: str) -> str:
    """Require one non-empty string config value."""
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{context}.{key} must be a non-empty string.")
    return value


def _require_positive_int(payload: dict[str, Any], key: str, *, context: str) -> int:
    """Require one strictly positive integer config value."""
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"{context}.{key} must be an integer greater than zero.")
    return value


def _require_positive_float(payload: dict[str, Any], key: str, *, context: str) -> float:
    """Require one strictly positive numeric config value."""
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ConfigError(f"{context}.{key} must be a number greater than zero.")
    return float(value)


def _require_non_negative_float(payload: dict[str, Any], key: str, *, context: str) -> float:
    """Require one non-negative numeric config value."""
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ConfigError(f"{context}.{key} must be a number greater than or equal to zero.")
    return float(value)


def _require_bool(payload: dict[str, Any], key: str, *, context: str) -> bool:
    """Require one boolean config value."""
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"{context}.{key} must be true or false.")
    return value
