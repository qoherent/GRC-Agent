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
    desired_context_tokens: int
    startup_timeout_seconds: float
    max_tokens: int
    max_tool_rounds: int
    temperature: float
    enable_thinking: bool
    request_timeout_seconds: float


@dataclass(frozen=True)
class DocsAnswerConfig:
    """Configurable defaults for ask_grc_docs grounded-answer behavior."""

    enabled: bool
    helper_mode: str
    helper_max_output_tokens: int
    helper_timeout_seconds: float
    helper_max_snippet_chars: int
    helper_max_total_context_chars: int
    max_sources: int
    answer_target_chars: int
    excerpt_target_chars: int
    lexical_first: bool
    semantic_manual_enabled: bool
    semantic_tutorial_enabled: bool
    probe_timeout_seconds: float
    retry_interval_on_failure_seconds: float
    retry_interval_on_success_seconds: float
    fallback_enabled: bool
    answer_cache_size: int
    helper_prompt_version: str


@dataclass(frozen=True)
class RetrievalConfig:
    """Configurable defaults for wrapper retrieval behavior."""

    search_blocks_default_k: int
    search_blocks_max_k: int
    ask_grc_docs_default_k: int
    ask_grc_docs_max_k: int
    conceptual_cache_size: int
    exact_match_fast_path: bool


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
    max_graph_summary_blocks: int
    max_context_nodes: int


DEFAULT_DOCS_ANSWER_CONFIG = DocsAnswerConfig(
    enabled=True,
    helper_mode="never",
    helper_max_output_tokens=320,
    helper_timeout_seconds=3.5,
    helper_max_snippet_chars=320,
    helper_max_total_context_chars=900,
    max_sources=2,
    answer_target_chars=300,
    excerpt_target_chars=220,
    lexical_first=True,
    semantic_manual_enabled=True,
    semantic_tutorial_enabled=True,
    probe_timeout_seconds=0.5,
    retry_interval_on_failure_seconds=3.0,
    retry_interval_on_success_seconds=1.5,
    fallback_enabled=True,
    answer_cache_size=64,
    helper_prompt_version="v3_compact",
)

DEFAULT_RETRIEVAL_CONFIG = RetrievalConfig(
    search_blocks_default_k=5,
    search_blocks_max_k=10,
    ask_grc_docs_default_k=3,
    ask_grc_docs_max_k=6,
    conceptual_cache_size=64,
    exact_match_fast_path=True,
)

DEFAULT_HISTORY_CONFIG = HistoryConfig(checkpoint_retention=100)

DEFAULT_GUARDRAILS_CONFIG = GuardrailsConfig(
    max_tool_output_bytes=32768,
    max_validation_errors=8,
    max_validation_stderr_chars=1200,
    max_compact_list_items=3,
    max_graph_summary_blocks=50,
    max_context_nodes=20,
)


@dataclass(frozen=True)
class AgentConfig:
    """Configurable defaults for the GrcAgent behavior."""

    history_compact_budget: int
    advisor_enabled: bool = False
    advisor_limited_advisory: bool = False
    advisor_shadow_telemetry: bool = True
    legacy_model_tool_surface: bool = False
    docs_answer: DocsAnswerConfig = DEFAULT_DOCS_ANSWER_CONFIG
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
        llama=LlamaConfig(
            server_url="http://127.0.0.1:8080",
            model="unsloth/gemma-4-E2B-it-GGUF",
            hf_model="unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL",
            desired_context_tokens=120000,
            startup_timeout_seconds=300.0,
            max_tokens=4096,
            max_tool_rounds=50,
            temperature=0.0,
            enable_thinking=False,
            request_timeout_seconds=60.0,
        ),
        agent=AgentConfig(
            history_compact_budget=100000,
            advisor_enabled=False,
            advisor_limited_advisory=False,
            advisor_shadow_telemetry=True,
            legacy_model_tool_surface=False,
            docs_answer=DEFAULT_DOCS_ANSWER_CONFIG,
            retrieval=DEFAULT_RETRIEVAL_CONFIG,
            history=DEFAULT_HISTORY_CONFIG,
            guardrails=DEFAULT_GUARDRAILS_CONFIG,
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
        raise ConfigError(
            f"Config file {resolved_path} must contain a top-level TOML table."
        )

    defaults = default_app_config()
    llama_table = _require_table(payload, "llama", context="root")
    agent_table = payload.get("agent")
    if not isinstance(agent_table, dict):
        agent_config = defaults.agent
    else:
        docs_table = agent_table.get("docs_answer")
        retrieval_table = agent_table.get("retrieval")
        history_table = agent_table.get("history")
        guardrails_table = agent_table.get("guardrails")
        agent_config = AgentConfig(
            history_compact_budget=_require_positive_int(
                agent_table, "history_compact_budget", context="[agent]"
            ),
            advisor_enabled=_optional_bool(
                agent_table, "advisor_enabled", default=False, context="[agent]"
            ),
            advisor_limited_advisory=_optional_bool(
                agent_table, "advisor_limited_advisory", default=False, context="[agent]"
            ),
            advisor_shadow_telemetry=_optional_bool(
                agent_table, "advisor_shadow_telemetry", default=True, context="[agent]"
            ),
            legacy_model_tool_surface=_optional_bool(
                agent_table, "legacy_model_tool_surface", default=False, context="[agent]"
            ),
            docs_answer=_docs_answer_config(
                docs_table if isinstance(docs_table, dict) else {},
                defaults=defaults.agent.docs_answer,
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
            server_url=_require_non_empty_string(
                llama_table, "server_url", context="[llama]"
            ),
            model=_require_non_empty_string(llama_table, "model", context="[llama]"),
            hf_model=_require_non_empty_string(
                llama_table, "hf_model", context="[llama]"
            ),
            desired_context_tokens=_optional_positive_int(
                llama_table,
                "desired_context_tokens",
                default=defaults.llama.desired_context_tokens,
                context="[llama]",
            ),
            startup_timeout_seconds=_require_positive_float(
                llama_table,
                "startup_timeout_seconds",
                context="[llama]",
            ),
            max_tokens=_require_positive_int(
                llama_table, "max_tokens", context="[llama]"
            ),
            max_tool_rounds=_optional_positive_int(
                llama_table,
                "max_tool_rounds",
                default=defaults.llama.max_tool_rounds,
                context="[llama]",
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
        ),
        agent=agent_config,
    )
    _validate_cross_field_constraints(config)
    return config


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


def _docs_answer_config(
    table: dict[str, Any],
    *,
    defaults: DocsAnswerConfig,
) -> DocsAnswerConfig:
    return DocsAnswerConfig(
        enabled=_optional_bool(table, "enabled", default=defaults.enabled, context="[agent.docs_answer]"),
        helper_mode=_optional_non_empty_string(table, "helper_mode", default=defaults.helper_mode, context="[agent.docs_answer]"),
        helper_max_output_tokens=_optional_positive_int(
            table,
            "helper_max_output_tokens",
            default=defaults.helper_max_output_tokens,
            context="[agent.docs_answer]",
        ),
        helper_timeout_seconds=_optional_positive_float(
            table,
            "helper_timeout_seconds",
            default=defaults.helper_timeout_seconds,
            context="[agent.docs_answer]",
        ),
        helper_max_snippet_chars=_optional_positive_int(
            table,
            "helper_max_snippet_chars",
            default=defaults.helper_max_snippet_chars,
            context="[agent.docs_answer]",
        ),
        helper_max_total_context_chars=_optional_positive_int(
            table,
            "helper_max_total_context_chars",
            default=defaults.helper_max_total_context_chars,
            context="[agent.docs_answer]",
        ),
        max_sources=_optional_positive_int(
            table,
            "max_sources",
            default=defaults.max_sources,
            context="[agent.docs_answer]",
        ),
        answer_target_chars=_optional_positive_int(
            table,
            "answer_target_chars",
            default=defaults.answer_target_chars,
            context="[agent.docs_answer]",
        ),
        excerpt_target_chars=_optional_positive_int(
            table,
            "excerpt_target_chars",
            default=defaults.excerpt_target_chars,
            context="[agent.docs_answer]",
        ),
        lexical_first=_optional_bool(
            table,
            "lexical_first",
            default=defaults.lexical_first,
            context="[agent.docs_answer]",
        ),
        semantic_manual_enabled=_optional_bool(
            table,
            "semantic_manual_enabled",
            default=defaults.semantic_manual_enabled,
            context="[agent.docs_answer]",
        ),
        semantic_tutorial_enabled=_optional_bool(
            table,
            "semantic_tutorial_enabled",
            default=defaults.semantic_tutorial_enabled,
            context="[agent.docs_answer]",
        ),
        probe_timeout_seconds=_optional_positive_float(
            table,
            "probe_timeout_seconds",
            default=defaults.probe_timeout_seconds,
            context="[agent.docs_answer]",
        ),
        retry_interval_on_failure_seconds=_optional_positive_float(
            table,
            "retry_interval_on_failure_seconds",
            default=defaults.retry_interval_on_failure_seconds,
            context="[agent.docs_answer]",
        ),
        retry_interval_on_success_seconds=_optional_positive_float(
            table,
            "retry_interval_on_success_seconds",
            default=defaults.retry_interval_on_success_seconds,
            context="[agent.docs_answer]",
        ),
        fallback_enabled=_optional_bool(
            table,
            "fallback_enabled",
            default=defaults.fallback_enabled,
            context="[agent.docs_answer]",
        ),
        answer_cache_size=_optional_positive_int(
            table,
            "answer_cache_size",
            default=defaults.answer_cache_size,
            context="[agent.docs_answer]",
        ),
        helper_prompt_version=_optional_non_empty_string(
            table,
            "helper_prompt_version",
            default=defaults.helper_prompt_version,
            context="[agent.docs_answer]",
        ),
    )


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
        conceptual_cache_size=_optional_positive_int(
            table,
            "conceptual_cache_size",
            default=defaults.conceptual_cache_size,
            context="[agent.retrieval]",
        ),
        exact_match_fast_path=_optional_bool(
            table,
            "exact_match_fast_path",
            default=defaults.exact_match_fast_path,
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
        max_graph_summary_blocks=_optional_positive_int(
            table,
            "max_graph_summary_blocks",
            default=defaults.max_graph_summary_blocks,
            context="[agent.guardrails]",
        ),
        max_context_nodes=_optional_positive_int(
            table,
            "max_context_nodes",
            default=defaults.max_context_nodes,
            context="[agent.guardrails]",
        ),
    )


def _validate_cross_field_constraints(config: AppConfig) -> None:
    """Reject contradictory numeric config combinations early."""
    retrieval = config.agent.retrieval
    docs_answer = config.agent.docs_answer
    guardrails = config.agent.guardrails

    if retrieval.search_blocks_default_k > retrieval.search_blocks_max_k:
        raise ConfigError(
            "[agent.retrieval].search_blocks_default_k must be <= search_blocks_max_k."
        )
    if retrieval.ask_grc_docs_default_k > retrieval.ask_grc_docs_max_k:
        raise ConfigError(
            "[agent.retrieval].ask_grc_docs_default_k must be <= ask_grc_docs_max_k."
        )
    if docs_answer.max_sources > retrieval.ask_grc_docs_max_k:
        raise ConfigError(
            "[agent.docs_answer].max_sources must be <= [agent.retrieval].ask_grc_docs_max_k."
        )
    if docs_answer.helper_max_snippet_chars > docs_answer.helper_max_total_context_chars:
        raise ConfigError(
            "[agent.docs_answer].helper_max_snippet_chars must be <= helper_max_total_context_chars."
        )
    if config.llama.desired_context_tokens < 4096:
        raise ConfigError(
            "[llama].desired_context_tokens must be >= 4096 for bounded chat/tool turns."
        )
    if guardrails.max_compact_list_items < 1:
        raise ConfigError(
            "[agent.guardrails].max_compact_list_items must be >= 1."
        )


__all__ = [
    "AgentConfig",
    "AppConfig",
    "CONFIG_ENV_VAR",
    "CONFIG_FILE_NAME",
    "ConfigError",
    "DocsAnswerConfig",
    "GuardrailsConfig",
    "HistoryConfig",
    "LlamaConfig",
    "RetrievalConfig",
    "default_app_config",
    "default_config_path",
    "load_app_config",
    "resolve_config_path",
    "user_config_path",
]
