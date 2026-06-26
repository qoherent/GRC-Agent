"""Load app runtime configuration with built-in defaults and optional overrides."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import tomllib
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_FILE_NAME = "grc_agent.toml"
CONFIG_ENV_VAR = "GRC_AGENT_CONFIG"
USER_CONFIG_FILE_NAME = "config.toml"
USER_CONFIG_DIR_NAME = "grc_agent"


def _load_dotenv() -> None:
    import dotenv

    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"]
    for candidate in candidates:
        if candidate.is_file():
            try:
                dotenv.load_dotenv(candidate)
                break
            except Exception:
                pass


_load_dotenv()


# ---------------------------------------------------------------------------
# Default backend endpoints and model — single source of truth.
# The GUI toolbar, model swap state machine, and the LlamaConfig dataclass
# default all read from these instead of inlining the literals.
# ---------------------------------------------------------------------------
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OPENROUTER_URL = "https://openrouter.ai/api"


def default_openrouter_model() -> str:
    """Resolve the OpenRouter model from the environment, with one literal fallback."""
    import os

    return os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")


class ConfigError(RuntimeError):
    """Raised when an explicit or discovered config file is invalid."""


@dataclass(frozen=True)
class LlamaConfig:
    """Configurable defaults for the model backend.

    ``model`` is required: a config file without a model would silently
    degrade every LLM call (chat completion, RAG synthesis) to a 400 from
    the backend. The GUI/CLI provider-picker can still override the value
    at runtime, but the parsed config must always carry a non-empty model.
    """

    server_url: str = DEFAULT_OLLAMA_URL
    model: str = "maxwell1500/ornith-9b:q4_K_M-120k"
    embedding_model: str = "embeddinggemma:latest"
    backend: str = "ollama"
    max_tokens: int = 4096
    max_tool_rounds: int = 8
    temperature: float = 0.0
    enable_thinking: bool = False
    request_timeout_seconds: float = 120.0


@dataclass(frozen=True)
class RetrievalConfig:
    """Configurable defaults for wrapper retrieval behavior."""

    search_blocks_default_k: int
    search_blocks_max_k: int
    ask_grc_docs_default_k: int
    ask_grc_docs_max_k: int


@dataclass(frozen=True)
class HistoryConfig:
    """Configurable defaults for checkpoint/history retention behavior."""

    checkpoint_retention: int


@dataclass(frozen=True)
class GuardrailsConfig:
    """Configurable defaults for bounded outputs and inspect limits."""

    max_tool_output_bytes: int
    max_validation_errors: int
    max_validation_stderr_chars: int
    max_compact_list_items: int
    max_context_nodes: int
    max_inspect_targets: int = 8


DEFAULT_RETRIEVAL_CONFIG = RetrievalConfig(
    search_blocks_default_k=10,
    search_blocks_max_k=12,
    ask_grc_docs_default_k=3,
    ask_grc_docs_max_k=8,
)

DEFAULT_HISTORY_CONFIG = HistoryConfig(checkpoint_retention=100)

DEFAULT_GUARDRAILS_CONFIG = GuardrailsConfig(
    max_tool_output_bytes=32768,
    max_validation_errors=8,
    max_validation_stderr_chars=1200,
    max_compact_list_items=5,
    max_context_nodes=20,
)


@dataclass(frozen=True)
class AgentConfig:
    """Configurable defaults for the GrcAgent behavior."""

    history_compact_budget: int
    max_tool_result_chars: int = 4000
    retrieval: RetrievalConfig = DEFAULT_RETRIEVAL_CONFIG
    history: HistoryConfig = DEFAULT_HISTORY_CONFIG
    guardrails: GuardrailsConfig = DEFAULT_GUARDRAILS_CONFIG


@dataclass(frozen=True)
class AppConfig:
    """Top-level runtime config."""

    llama: LlamaConfig
    agent: AgentConfig


def default_config_path() -> Path:
    """Return the repo-level config path used in the source workspace."""
    return Path(__file__).resolve().parents[2] / CONFIG_FILE_NAME


def user_config_path() -> Path:
    """Return the default per-user config path for installed app use."""
    return Path.home() / ".config" / USER_CONFIG_DIR_NAME / USER_CONFIG_FILE_NAME


def default_app_config() -> AppConfig:
    """Return the built-in runtime defaults used when no config file exists."""
    config = AppConfig(
        llama=LlamaConfig(),
        agent=AgentConfig(
            history_compact_budget=100000,
        ),
    )
    _validate_cross_field_constraints(config)
    return config


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
        raise ConfigError(f"Config file {resolved_path} must contain a top-level TOML table.")

    defaults = default_app_config()
    llama_table = _require_table(payload, "llama", context="root")
    agent_table = payload.get("agent")
    if not isinstance(agent_table, dict):
        agent_config = defaults.agent
    else:
        retrieval_table = agent_table.get("retrieval")
        history_table = agent_table.get("history")
        guardrails_table = agent_table.get("guardrails")
        agent_config = AgentConfig(
            history_compact_budget=_require_positive_int(
                agent_table, "history_compact_budget", context="[agent]"
            ),
            max_tool_result_chars=_optional_positive_int(
                agent_table,
                "max_tool_result_chars",
                default=defaults.agent.max_tool_result_chars,
                context="[agent]",
            ),
            retrieval=_retrieval_config(
                retrieval_table if isinstance(retrieval_table, dict) else {},
                defaults=defaults.agent.retrieval,
            ),
            history=_history_config(
                history_table if isinstance(history_table, dict) else {},
                defaults=defaults.agent.history,
            ),
            guardrails=_guardrails_config(
                guardrails_table if isinstance(guardrails_table, dict) else {},
                defaults=defaults.agent.guardrails,
            ),
        )

    config = AppConfig(
        llama=LlamaConfig(
            server_url=_require_non_empty_string(llama_table, "server_url", context="[llama]"),
            model=_require_non_empty_string(llama_table, "model", context="[llama]"),
            embedding_model=_optional_non_empty_string(
                llama_table,
                "embedding_model",
                default=defaults.llama.embedding_model,
                context="[llama]",
            ),
            backend=_optional_non_empty_string(
                llama_table,
                "backend",
                default=defaults.llama.backend,
                context="[llama]",
            ),
            max_tokens=_optional_positive_int(
                llama_table,
                "max_tokens",
                default=defaults.llama.max_tokens,
                context="[llama]",
            ),
            max_tool_rounds=_optional_positive_int(
                llama_table,
                "max_tool_rounds",
                default=defaults.llama.max_tool_rounds,
                context="[llama]",
            ),
            temperature=_optional_non_negative_float(
                llama_table,
                "temperature",
                default=defaults.llama.temperature,
                context="[llama]",
            ),
            enable_thinking=_optional_bool(
                llama_table,
                "enable_thinking",
                default=defaults.llama.enable_thinking,
                context="[llama]",
            ),
            request_timeout_seconds=_optional_positive_float(
                llama_table,
                "request_timeout_seconds",
                default=defaults.llama.request_timeout_seconds,
                context="[llama]",
            ),
        ),
        agent=agent_config,
    )
    _validate_cross_field_constraints(config)
    return config


def _require_table(payload: dict[str, Any], key: str, *, context: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{context} must contain a [{key}] table.")
    return value


def _require_non_empty_string(payload: dict[str, Any], key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{context}.{key} must be a non-empty string.")
    return value


def _require_positive_int(payload: dict[str, Any], key: str, *, context: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"{context}.{key} must be an integer greater than zero.")
    return value


def _require_positive_float(payload: dict[str, Any], key: str, *, context: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ConfigError(f"{context}.{key} must be a number greater than zero.")
    return float(value)


def _require_non_negative_float(payload: dict[str, Any], key: str, *, context: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ConfigError(f"{context}.{key} must be a number greater than or equal to zero.")
    return float(value)


def _require_bool(payload: dict[str, Any], key: str, *, context: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"{context}.{key} must be true or false.")
    return value


def _optional_bool(
    payload: dict[str, Any],
    key: str,
    *,
    default: bool,
    context: str,
) -> bool:
    if key not in payload:
        return default
    return _require_bool(payload, key, context=context)


def _optional_positive_int(
    payload: dict[str, Any],
    key: str,
    *,
    default: int,
    context: str,
) -> int:
    if key not in payload:
        return default
    return _require_positive_int(payload, key, context=context)


def _optional_positive_float(
    payload: dict[str, Any],
    key: str,
    *,
    default: float,
    context: str,
) -> float:
    if key not in payload:
        return default
    return _require_positive_float(payload, key, context=context)


def _optional_non_empty_string(
    payload: dict[str, Any],
    key: str,
    *,
    default: str,
    context: str,
) -> str:
    if key not in payload:
        return default
    return _require_non_empty_string(payload, key, context=context)


def _optional_non_negative_float(
    payload: dict[str, Any],
    key: str,
    *,
    default: float,
    context: str,
) -> float:
    if key not in payload:
        return default
    return _require_non_negative_float(payload, key, context=context)


def _retrieval_config(
    table: dict[str, Any],
    *,
    defaults: RetrievalConfig,
) -> RetrievalConfig:
    return RetrievalConfig(
        search_blocks_default_k=_optional_positive_int(
            table,
            "search_blocks_default_k",
            default=defaults.search_blocks_default_k,
            context="[agent.retrieval]",
        ),
        search_blocks_max_k=_optional_positive_int(
            table,
            "search_blocks_max_k",
            default=defaults.search_blocks_max_k,
            context="[agent.retrieval]",
        ),
        ask_grc_docs_default_k=_optional_positive_int(
            table,
            "ask_grc_docs_default_k",
            default=defaults.ask_grc_docs_default_k,
            context="[agent.retrieval]",
        ),
        ask_grc_docs_max_k=_optional_positive_int(
            table,
            "ask_grc_docs_max_k",
            default=defaults.ask_grc_docs_max_k,
            context="[agent.retrieval]",
        ),
    )


def _history_config(
    table: dict[str, Any],
    *,
    defaults: HistoryConfig,
) -> HistoryConfig:
    return HistoryConfig(
        checkpoint_retention=_optional_positive_int(
            table,
            "checkpoint_retention",
            default=defaults.checkpoint_retention,
            context="[agent.history]",
        )
    )


def _guardrails_config(
    table: dict[str, Any],
    *,
    defaults: GuardrailsConfig,
) -> GuardrailsConfig:
    return GuardrailsConfig(
        max_tool_output_bytes=_optional_positive_int(
            table,
            "max_tool_output_bytes",
            default=defaults.max_tool_output_bytes,
            context="[agent.guardrails]",
        ),
        max_validation_errors=_optional_positive_int(
            table,
            "max_validation_errors",
            default=defaults.max_validation_errors,
            context="[agent.guardrails]",
        ),
        max_validation_stderr_chars=_optional_positive_int(
            table,
            "max_validation_stderr_chars",
            default=defaults.max_validation_stderr_chars,
            context="[agent.guardrails]",
        ),
        max_compact_list_items=_optional_positive_int(
            table,
            "max_compact_list_items",
            default=defaults.max_compact_list_items,
            context="[agent.guardrails]",
        ),
        max_context_nodes=_optional_positive_int(
            table,
            "max_context_nodes",
            default=defaults.max_context_nodes,
            context="[agent.guardrails]",
        ),
        max_inspect_targets=_optional_positive_int(
            table,
            "max_inspect_targets",
            default=defaults.max_inspect_targets,
            context="[agent.guardrails]",
        ),
    )


def _validate_cross_field_constraints(config: AppConfig) -> None:
    retrieval = config.agent.retrieval
    guardrails = config.agent.guardrails

    if config.llama.backend not in ("ollama", "openrouter"):
        raise ConfigError(
            f"[llama].backend must be 'ollama' or 'openrouter'; found '{config.llama.backend}'."
        )

    if retrieval.search_blocks_default_k > retrieval.search_blocks_max_k:
        raise ConfigError(
            "[agent.retrieval].search_blocks_default_k must be <= search_blocks_max_k."
        )
    if retrieval.ask_grc_docs_default_k > retrieval.ask_grc_docs_max_k:
        raise ConfigError("[agent.retrieval].ask_grc_docs_default_k must be <= ask_grc_docs_max_k.")
    if guardrails.max_compact_list_items < 1:
        raise ConfigError("[agent.guardrails].max_compact_list_items must be >= 1.")


def update_toml_config_file(config_path: Path, updates: dict[str, Any]) -> None:
    """Read a TOML file, update fields in [llama] section, and write it back."""
    if not config_path.is_file():
        return
    lines = config_path.read_text(encoding="utf-8").splitlines()
    new_lines: list[str] = []
    in_llama = False
    updated_keys: set[str] = set()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if stripped == "[llama]":
                in_llama = True
            else:
                if in_llama:
                    for k, v in updates.items():
                        if k not in updated_keys:
                            new_lines.append(f"{k} = {json.dumps(v)}")
                    in_llama = False
            new_lines.append(line)
            continue

        if in_llama and "=" in line:
            key, _ = line.split("=", 1)
            key = key.strip()
            if key in updates:
                new_lines.append(f"{key} = {json.dumps(updates[key])}")
                updated_keys.add(key)
                continue

        new_lines.append(line)

    if in_llama:
        for k, v in updates.items():
            if k not in updated_keys:
                new_lines.append(f"{k} = {json.dumps(v)}")

    config_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Package paths (from paths.py)

# ---------------------------------------------------------------------------


def collect_package_paths() -> dict[str, str]:
    """Return a stable mapping of every on-disk location the package uses."""
    from grc_agent.history import HISTORY_ENV_VAR, default_history_path

    cache_root = Path.home() / ".cache"
    history_path = default_history_path()
    if not history_path.is_absolute():
        history_path = (Path.cwd() / history_path).resolve()
    paths: dict[str, str] = {
        "config_repo": str(default_config_path()),
        "config_user": str(user_config_path()),
        "preferences": str(user_preferences_path()),
        "history": str(history_path),
        "history_env_var": HISTORY_ENV_VAR,
        "sessions_db": str(Path.home() / ".grc_agent" / "sessions.db"),
        "grc_agent_state": str(Path.home() / ".grc_agent"),
        "grc_agent_cache": str(cache_root / "grc_agent"),
    }
    return paths


# ---------------------------------------------------------------------------
# User preferences (from preferences.py)
# ---------------------------------------------------------------------------

PREFS_FILE_NAME = "preferences.json"
PREFERENCES_SCHEMA_VERSION = 1


# Deterministic remap of stale persisted model names. When a model is
# upgraded via a new Modelfile variant (e.g. a different context window),
# GUI users whose `last_model.model` predates the upgrade must be migrated
# automatically — otherwise they keep running the old model despite the
# config default update. One uniform rule per entry (no per-field branching):
# if the persisted value is a key here, replace it with the mapped value on
# load and persist the result so the migration is a one-time event.
_MODEL_REMAP: dict[str, str] = {
    "gemma4:e4b-it-qat": "gemma4:e4b-it-qat-120k",
}


@dataclass(frozen=True)
class LastModel:
    """The model the user most recently loaded through the GUI/CLI."""

    model: str = ""
    saved_at: str = ""


@dataclass(frozen=True)
class UserPreferences:
    """The full set of persisted user preferences."""

    last_model: LastModel = field(default_factory=LastModel)
    provider_chosen: str = ""
    schema_version: int = PREFERENCES_SCHEMA_VERSION


def default_user_preferences() -> UserPreferences:
    """Return a fresh defaults instance."""
    return UserPreferences()


def user_preferences_path() -> Path:
    """Return the on-disk path of the preferences file."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "grc_agent" / PREFS_FILE_NAME
    return Path.home() / ".config" / "grc_agent" / PREFS_FILE_NAME


def _parse_last_model(raw: object) -> LastModel:
    """Build a :class:`LastModel` from a raw JSON value."""
    if not isinstance(raw, dict):
        logger.info("preferences: dropping non-dict last_model=%r", raw)
        return LastModel()
    out: dict[str, str] = {}
    for key in ("model", "saved_at"):
        value = raw.get(key)
        if isinstance(value, str):
            out[key] = value
        elif value is not None:
            logger.info("preferences: dropping non-string last_model.%s=%r", key, value)
    return LastModel(**out)


def _parse_preferences(raw: object) -> UserPreferences:
    """Build a :class:`UserPreferences` from a raw JSON value."""
    if not isinstance(raw, dict):
        return default_user_preferences()
    last_model = _parse_last_model(raw["last_model"]) if "last_model" in raw else LastModel()
    provider_chosen = ""
    if "provider_chosen" in raw:
        value = raw["provider_chosen"]
        if isinstance(value, str) and value in {"", "ollama", "openrouter"}:
            provider_chosen = value
        else:
            logger.info("preferences: dropping unknown provider_chosen=%r", value)
    schema_version = PREFERENCES_SCHEMA_VERSION
    if "schema_version" in raw:
        value = raw["schema_version"]
        if isinstance(value, int) and not isinstance(value, bool):
            schema_version = value
        else:
            logger.info("preferences: dropping non-int schema_version=%r", value)
    for unknown_key in raw:
        if unknown_key not in (
            "last_model",
            "provider_chosen",
            "schema_version",
        ):
            logger.info("preferences: ignoring unknown key %r", unknown_key)
    return UserPreferences(
        last_model=last_model,
        provider_chosen=provider_chosen,
        schema_version=schema_version,
    )


def load_user_preferences(path: Path | None = None) -> UserPreferences:
    """Load preferences from disk. Never raises."""
    target = (path or user_preferences_path()).expanduser()
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default_user_preferences()
    except OSError as exc:
        logger.warning("preferences: failed to read %s (%s); using defaults", target, exc)
        return default_user_preferences()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning(
            "preferences: %s is not valid JSON (%s); using defaults. "
            "The file has been left in place for inspection.",
            target,
            exc,
        )
        return default_user_preferences()
    prefs = _parse_preferences(raw)
    # Apply deterministic stale-model remap (e.g. gemma4:e4b-it-qat ->
    # gemma4:e4b-it-qat-120k) so users whose GUI previously pinned the old
    # model variant pick up the upgrade. The migration is persisted so it
    # runs once per file, not on every load. Save failures are logged but
    # never raised — startup must not block on a migration write.
    if prefs.last_model.model and prefs.last_model.model in _MODEL_REMAP:
        stale_model = prefs.last_model.model
        remapped_name = _MODEL_REMAP[stale_model]
        prefs = replace(
            prefs,
            last_model=replace(
                prefs.last_model,
                model=remapped_name,
                saved_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )
        try:
            save_user_preferences(prefs, path=target)
            logger.info(
                "preferences: remapped last_model.model %r -> %r and persisted.",
                stale_model,
                remapped_name,
            )
        except OSError as exc:
            logger.warning(
                "preferences: remapped %r -> %r in memory but failed to "
                "persist (%s). The remap will be retried on next load.",
                stale_model,
                remapped_name,
                exc,
            )
    if prefs.schema_version > PREFERENCES_SCHEMA_VERSION:
        logger.warning(
            "preferences: %s has schema_version=%d, this build supports up "
            "to %d. Falling back to defaults. Upgrade GRC Agent or move the "
            "file aside to use the new schema.",
            target,
            prefs.schema_version,
            PREFERENCES_SCHEMA_VERSION,
        )
        return default_user_preferences()
    return prefs


def save_user_preferences(prefs: UserPreferences, *, path: Path | None = None) -> None:
    """Atomically write preferences to disk."""
    target = (path or user_preferences_path()).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(prefs)
    fd, tmp_name = tempfile.mkstemp(
        prefix=PREFS_FILE_NAME + ".",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def apply_user_preferences_to_llama_config(llama_config: Any, prefs: UserPreferences) -> Any:
    """Overlay persisted preferences onto a :class:`LlamaConfig`."""
    if not isinstance(llama_config, LlamaConfig):
        return llama_config
    import dataclasses

    updated = llama_config
    if prefs.last_model.model:
        updated = dataclasses.replace(updated, model=prefs.last_model.model)
    if prefs.provider_chosen in {"ollama", "openrouter"}:
        updated = dataclasses.replace(updated, backend=prefs.provider_chosen)
    return updated


def update_last_model(
    *,
    model: str,
    path: Path | None = None,
) -> None:
    """Convenience: write just the ``last_model`` fields."""
    current = load_user_preferences(path=path)
    saved_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_last = LastModel(
        model=model,
        saved_at=saved_at,
    )
    updated = UserPreferences(
        last_model=new_last,
        provider_chosen=current.provider_chosen,
        schema_version=PREFERENCES_SCHEMA_VERSION,
    )
    save_user_preferences(updated, path=path)


def update_provider_chosen(
    *,
    provider: str,
    path: Path | None = None,
) -> None:
    """Persist the user's provider-picker choice."""
    if provider not in {"ollama", "openrouter"}:
        raise ValueError(f"provider must be 'ollama' or 'openrouter'; got {provider!r}")
    current = load_user_preferences(path=path)
    updated = UserPreferences(
        last_model=current.last_model,
        provider_chosen=provider,
        schema_version=PREFERENCES_SCHEMA_VERSION,
    )
    save_user_preferences(updated, path=path)


__all__ = [
    "AgentConfig",
    "AppConfig",
    "CONFIG_ENV_VAR",
    "CONFIG_FILE_NAME",
    "ConfigError",
    "GuardrailsConfig",
    "HistoryConfig",
    "LastModel",
    "LlamaConfig",
    "PREFERENCES_SCHEMA_VERSION",
    "PREFS_FILE_NAME",
    "RetrievalConfig",
    "UserPreferences",
    "apply_user_preferences_to_llama_config",
    "collect_package_paths",
    "default_app_config",
    "default_config_path",
    "default_user_preferences",
    "load_app_config",
    "load_user_preferences",
    "resolve_config_path",
    "save_user_preferences",
    "update_last_model",
    "update_provider_chosen",
    "update_toml_config_file",
    "user_config_path",
    "user_preferences_path",
]
