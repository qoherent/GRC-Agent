"""Single source of truth for GRC-native parameter filtering.

Every model-visible parameter payload in the agent (``inspect_graph``
overview/details, catalog ``describe_block``, catalog vector search) derives
its keep/drop decision from :func:`keep_param` below. No other module may
re-implement parameter visibility or prominence logic.

The rules (applied in order, one uniform rule per stage):

  Stage A — visibility (every mode):
    drop ``hide == 'all'``
    drop ``category in EXCLUDED_PARAM_CATEGORIES``  (Advanced, Config)
    drop ``dtype == 'gui_hint'``
  Stage B — prominence (``PROMINENCE`` mode only):
    keep if ``dtype == 'enum'``  (structural selectors: type, wintype, ...)
    keep if ``value != default``
    keep if value references a flowgraph variable

All ``hide`` / ``category`` / ``dtype`` / ``default`` values are read off live
GRC blocks (``gnuradio.grc.core``) via throwaway instantiation. ``hide`` is the
GRC-*evaluated* visibility (depends on current param values); the others are
static block-definition metadata.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from grc_agent.runtime.block_semantics import evaluated_param_hides

# GRC-native param-category constants.
try:
    from gnuradio.grc.core.Constants import ADVANCED_PARAM_TAB, DEFAULT_PARAM_TAB
except ImportError:  # pragma: no cover - GRC absent
    ADVANCED_PARAM_TAB = "Advanced"
    DEFAULT_PARAM_TAB = "General"

# Categories that hold only non-essential params (verified across the 564-block
# catalog): Advanced = GRC auto-added metadata (alias/affinity/comment/buffers);
# "Config" = QT-GUI cosmetics (colors/alphas/markers/styles).
EXCLUDED_PARAM_CATEGORIES: frozenset[str] = frozenset({ADVANCED_PARAM_TAB, "Config"})

# A value references a flowgraph variable when a whole Python identifier token
# in its expression equals a variable block's name.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_]\w*")

VISIBILITY = "visibility"  # Stage A only (Details / describe_block / embed)
PROMINENCE = "prominence"  # Stage A + Stage B (Overview)


def references_variable(value: Any, variable_names: set[str]) -> bool:
    """True if ``value`` names a flowgraph variable as a whole identifier token."""
    if not variable_names:
        return False
    return any(tok in variable_names for tok in _IDENTIFIER_RE.findall(str(value)))


def keep_param(
    *,
    hide: str,
    category: str,
    dtype: str,
    value: Any,
    default: Any,
    mode: str,
    variable_names: set[str] | None = None,
) -> bool:
    """THE parameter keep/drop rule. Single authority for every tool.

    Returns True if the param survives the pipeline for the given ``mode``.
    Pure function — no I/O, no side effects.
    """
    if hide == "all":
        return False
    if category in EXCLUDED_PARAM_CATEGORIES:
        return False
    if dtype == "gui_hint":
        return False
    if mode != PROMINENCE:
        return True
    if dtype == "enum":
        return True
    if str(value) != str(default):
        return True
    if variable_names and references_variable(value, variable_names):
        return True
    return False


def prominence_rank(hide: str) -> int:
    """Sort key: ``hide='none'`` first, then ``'part'``, then anything else."""
    return 0 if hide == "none" else 1 if hide == "part" else 2


@lru_cache(maxsize=None)
def param_metadata(block_type: str) -> dict[str, dict[str, str]]:
    """Static per-param metadata from the GRC block definition.

    Returns ``{param_key: {"category": ..., "dtype": ..., "default": ...}}``
    from one throwaway block instantiation. Empty dict if the platform is
    unavailable.
    """
    try:
        from grc_agent.session import _ensure_platform

        platform = _ensure_platform()
    except Exception:
        return {}
    if platform is None:
        return {}
    try:
        flow_graph = platform.make_flow_graph()
        block = flow_graph.new_block(block_type)
    except Exception:
        return {}
    if block is None:
        return {}
    try:
        return {
            str(name): {
                "category": str(getattr(param, "category", DEFAULT_PARAM_TAB)),
                "dtype": str(getattr(param, "dtype", "")),
                "default": str(getattr(param, "default", "")),
            }
            for name, param in block.params.items()
        }
    except Exception:
        return {}


def categories(block_type: str) -> dict[str, str]:
    """``{param_key: category}`` for one block type, from native GRC metadata."""
    return {k: v["category"] for k, v in param_metadata(block_type).items()}


def filter_live_block_params(
    block_type: str,
    param_values: dict[str, Any],
    *,
    mode: str,
    variable_names: set[str] | None = None,
) -> dict[str, str]:
    """Apply :func:`keep_param` to a live flowgraph block's ``{key: value}`` map.

    Returns ``{param_key: value}`` for surviving params. Falls back to all
    non-empty values if GRC hide-evaluation is unavailable (never silently
    drops a param that might be useful).
    """
    hides = evaluated_param_hides(block_type, param_values)
    if not hides:
        return {str(k): str(v) for k, v in param_values.items() if str(v).strip()}

    meta = param_metadata(block_type)
    out: dict[str, str] = {}
    for key, value in param_values.items():
        ks = str(key)
        info = meta.get(ks, {})
        if keep_param(
            hide=hides.get(ks, "all"),
            category=info.get("category", DEFAULT_PARAM_TAB),
            dtype=info.get("dtype", ""),
            value=value,
            default=info.get("default", ""),
            mode=mode,
            variable_names=variable_names,
        ):
            vs = str(value).strip()
            if vs:
                out[ks] = vs
    return out


def visible_param_keys(
    block_id: str,
    keys: list[str] | tuple[str, ...],
    param_values: dict[str, Any] | None = None,
) -> list[str]:
    """Visibility-filtered param keys for catalog embed text.

    Applies Stage A (hide + category) via :func:`keep_param` in VISIBILITY
    mode. Falls back to the full key list if GRC evaluation is unavailable.
    """
    hides = evaluated_param_hides(block_id, param_values or {})
    if not hides:
        return list(keys)
    cats = categories(block_id)
    # dtype is unavailable for a bare key list; pass "" so the gui_hint branch
    # is skipped (a single cosmetic key in embed text is negligible).
    # Missing hide entries default to "none" (kept) — embed text is conservative
    # and never silently drops a param whose visibility is unknown.
    return [
        key for key in keys
        if keep_param(
            hide=hides.get(str(key), "none"),
            category=cats.get(str(key), DEFAULT_PARAM_TAB),
            dtype="",
            value="",
            default="",
            mode=VISIBILITY,
        )
    ]
