"""Load app runtime configuration with built-in defaults and optional overrides."""

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib
from typing import Any


CONFIG_FILE_NAME = "grc_agent.toml"
CONFIG_ENV_VAR = "GRC_AGENT_CONFIG"
USER_CONFIG_FILE_NAME = "config.toml"
USER_CONFIG_DIR_NAME = "grc_agent"


class ConfigError(RuntimeError):
    """Raised when an explicit or discovered config file is invalid."""


@dataclass(frozen=True)
class LlamaConfig:
    """Configurable defaults for the local llama.cpp runtime path."""

    server_url: str
    model: str
    hf_model: str
    startup_timeout_seconds: float
    max_tokens: int
    temperature: float
    enable_thinking: bool
    request_timeout_seconds: float


@dataclass(frozen=True)
class AppConfig:
    """Top-level runtime config."""

    llama: LlamaConfig


def default_config_path() -> Path:
    """Return the repo-level config path used in the source workspace."""
    return Path(__file__).resolve().parents[2] / CONFIG_FILE_NAME


def user_config_path() -> Path:
    """Return the default per-user config path for installed app use."""
    return Path.home() / ".config" / USER_CONFIG_DIR_NAME / USER_CONFIG_FILE_NAME


def default_app_config() -> AppConfig:
    """Return the built-in runtime defaults used when no config file exists."""
    return AppConfig(
        llama=LlamaConfig(
            server_url="http://127.0.0.1:8080",
            model="unsloth/gemma-4-E2B-it-GGUF",
            hf_model="unsloth/gemma-4-E2B-it-GGUF:Q4_K_M",
            startup_timeout_seconds=300.0,
            max_tokens=12000,
            temperature=0.0,
            enable_thinking=False,
            request_timeout_seconds=60.0,
        )
    )


def resolve_config_path(config_path: str | Path | None = None) -> Path | None:
    """Resolve the config override path, if one exists."""
    if config_path is not None:
        return Path(config_path).expanduser()

    env_path = os.environ.get(CONFIG_ENV_VAR)
    if env_path:
        return Path(env_path).expanduser()

    repo_config = default_config_path()
    if repo_config.is_file():
        return repo_config

    installed_user_config = user_config_path()
    if installed_user_config.is_file():
        return installed_user_config

    return None


def load_app_config(config_path: str | Path | None = None) -> AppConfig:
    """Read the resolved config file or return built-in defaults."""
    resolved_path = resolve_config_path(config_path)
    if resolved_path is None:
        return default_app_config()

    try:
        with resolved_path.open("rb") as config_file:
            payload = tomllib.load(config_file)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {resolved_path}") from exc

    if not isinstance(payload, dict):
        raise ConfigError(
            f"Config file {resolved_path} must contain a top-level TOML table."
        )

    llama_table = _require_table(payload, "llama", context="root")
    return AppConfig(
        llama=LlamaConfig(
            server_url=_require_non_empty_string(
                llama_table, "server_url", context="[llama]"
            ),
            model=_require_non_empty_string(llama_table, "model", context="[llama]"),
            hf_model=_require_non_empty_string(
                llama_table, "hf_model", context="[llama]"
            ),
            startup_timeout_seconds=_require_positive_float(
                llama_table,
                "startup_timeout_seconds",
                context="[llama]",
            ),
            max_tokens=_require_positive_int(
                llama_table, "max_tokens", context="[llama]"
            ),
            temperature=_require_non_negative_float(
                llama_table,
                "temperature",
                context="[llama]",
            ),
            enable_thinking=_require_bool(
                llama_table, "enable_thinking", context="[llama]"
            ),
            request_timeout_seconds=_require_positive_float(
                llama_table,
                "request_timeout_seconds",
                context="[llama]",
            ),
        )
    )


def _require_table(
    payload: dict[str, Any], key: str, *, context: str
) -> dict[str, Any]:
    """Require one nested TOML table."""
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{context} must contain a [{key}] table.")
    return value


def _require_non_empty_string(
    payload: dict[str, Any], key: str, *, context: str
) -> str:
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


def _require_positive_float(
    payload: dict[str, Any], key: str, *, context: str
) -> float:
    """Require one strictly positive numeric config value."""
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ConfigError(f"{context}.{key} must be a number greater than zero.")
    return float(value)


def _require_non_negative_float(
    payload: dict[str, Any], key: str, *, context: str
) -> float:
    """Require one non-negative numeric config value."""
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ConfigError(
            f"{context}.{key} must be a number greater than or equal to zero."
        )
    return float(value)


def _require_bool(payload: dict[str, Any], key: str, *, context: str) -> bool:
    """Require one boolean config value."""
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"{context}.{key} must be true or false.")
    return value


__all__ = [
    "AppConfig",
    "CONFIG_ENV_VAR",
    "CONFIG_FILE_NAME",
    "ConfigError",
    "LlamaConfig",
    "default_app_config",
    "default_config_path",
    "load_app_config",
    "resolve_config_path",
    "user_config_path",
]
