"""Single source of truth for model-facing tool surface policy."""

from dataclasses import dataclass


PUBLIC_TOOL_NAMES: tuple[str, ...] = (
    "new_grc",
    "load_grc",
    "summarize_graph",
    "search_grc",
    "get_grc_context",
    "describe_block",
    "search_manual",
    "semantic_search_grc",
    "suggest_compatible_insertions",
    "insert_block_on_connection",
    "auto_insert_block",
    "remove_connection",
    "rewire_connection",
    "apply_edit",
    "propose_edit",
    "validate_graph",
    "save_graph",
)

MVP_MODEL_TOOL_NAMES: tuple[str, ...] = (
    "inspect_graph",
    "search_blocks",
    "ask_grc_docs",
    "change_graph",
    "save_graph_explicit",
    "load_graph_explicit",
)

MODEL_TOOL_NAMES_ORDERED: tuple[str, ...] = (
    *PUBLIC_TOOL_NAMES,
    *MVP_MODEL_TOOL_NAMES,
)


@dataclass(frozen=True)
class ToolSurface:
    """Runtime policy for one model-facing tool profile."""

    name: str
    model_tool_names: tuple[str, ...]
    internal_tool_names: tuple[str, ...]
    assistant_text_fallback_enabled: bool
    default_max_tool_rounds: int

    @property
    def model_tool_count(self) -> int:
        return len(self.model_tool_names)

    @property
    def internal_tool_count(self) -> int:
        return len(self.internal_tool_names)


MVP_TOOL_SURFACE = ToolSurface(
    name="mvp",
    model_tool_names=MVP_MODEL_TOOL_NAMES,
    internal_tool_names=PUBLIC_TOOL_NAMES,
    assistant_text_fallback_enabled=False,
    default_max_tool_rounds=8,
)

LEGACY_TOOL_SURFACE = ToolSurface(
    name="legacy",
    model_tool_names=PUBLIC_TOOL_NAMES,
    internal_tool_names=MVP_MODEL_TOOL_NAMES,
    assistant_text_fallback_enabled=True,
    default_max_tool_rounds=50,
)


def tool_surface_for_legacy_flag(*, legacy_model_tool_surface: bool) -> ToolSurface:
    """Return the active tool surface for CLI/runtime config."""
    return LEGACY_TOOL_SURFACE if legacy_model_tool_surface else MVP_TOOL_SURFACE
