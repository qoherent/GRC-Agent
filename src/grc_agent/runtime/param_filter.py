"""Single source of truth for GRC-native parameter filtering.

Every model-visible parameter payload in the agent (``inspect_graph``
overview, catalog ``describe_block``, catalog vector search) derives
its keep/drop decision from :func:`keep_param` below. No other module may
re-implement parameter filtering logic.

The rules (applied in order, one uniform rule per stage):

  Stage A — applied in every mode:
    drop ``hide == 'all'``
    drop ``category in EXCLUDED_PARAM_CATEGORIES``  (Advanced, Config)
    drop ``dtype == 'gui_hint'``
  Stage B — applied only in overview mode:
    keep if ``hide == 'none'``        (GRC marks the param always-shown)
    keep if ``dtype == 'enum'``       (structural selector: type, wintype, ...)
    keep if ``value != default``
    keep if value references a flowgraph variable

All ``hide`` / ``category`` / ``dtype`` / ``default`` values are read off live
GRC blocks (``gnuradio.grc.core``) via throwaway instantiation. ``hide`` is the
GRC-*evaluated* visibility (depends on current param values); the others are
static block-definition metadata.

Three structural exceptions to the pure hide/category/dtype rule, applied
before Stage A (see :func:`keep_param`):

  - ``dtype == 'id'``: the block's instance name. Native GRC dtype, not a
    name guess — GRC's own property editor (``gui/PropsDialog.py``,
    ``gui/canvas/block.py``) branches on this exact dtype to force-show the
    id field, so this mirrors upstream GRC, not an app invention.
  - ``param_key == 'showports'``: no native GRC attribute distinguishes it
    from any other boolean param — it is a per-``.block.yml`` community
    naming convention with zero support in ``gnuradio.grc.core``. Kept as a
    literal name check because no uniform-rule alternative exists.
  - ``param_key.startswith('bus_structure_')``: not an app-invented
    heuristic — GRC's own ``Block`` class (``gnuradio/grc/core/blocks/
    block.py``, ``bus_structure_source``/``bus_structure_sink``
    properties) reserves these exact literal keys itself.
"""

from __future__ import annotations

import logging
import re
from functools import cache
from typing import Any

logger = logging.getLogger(__name__)

from grc_agent.runtime.block_semantics import evaluated_param_hides

# GRC-native param-category constants (stable string values; importing them
# from ``gnuradio.grc.core.Constants`` at module top-level would violate the
# adapter boundary and break CI without GNU Radio).
ADVANCED_PARAM_TAB = "Advanced"
DEFAULT_PARAM_TAB = "General"

# Categories that hold only non-essential params (verified across the 564-block
# catalog): Advanced = GRC auto-added metadata (alias/affinity/comment/buffers);
# "Config" = QT-GUI cosmetics (colors/alphas/markers/styles).
EXCLUDED_PARAM_CATEGORIES: frozenset[str] = frozenset({ADVANCED_PARAM_TAB, "Config"})

# A value references a flowgraph variable when a whole Python identifier token
# in its expression equals a variable block's name.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_]\w*")

DETAILS = "details"  # Stage A only (catalog describe_block / embed)
OVERVIEW = "overview"  # Stage A + Stage B (inspect_graph overview)


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
    param_key: str = "",
) -> bool:
    """THE parameter keep/drop rule. Single authority for every tool.

    Returns True if the param survives the pipeline for the given ``mode``.
    Pure function — no I/O, no side effects.
    """
    # 'id' is a native GRC dtype (see module docstring) — the block's instance
    # name, always redundant with ``instance_name`` (already carried by every
    # model-visible block payload); the system prompt forbids editing it.
    # 'showports' and 'bus_structure_*' have no dtype/category signal (see
    # module docstring) and are excluded by name as a documented exception.
    if dtype == "id" or param_key == "showports" or param_key.startswith("bus_structure_"):
        return False
    if hide == "all":
        return False
    if category in EXCLUDED_PARAM_CATEGORIES:
        return False
    if dtype == "gui_hint":
        return False
    if mode != OVERVIEW:
        return True

    # Drop 'hide: part' parameters in overview mode if they match default and
    # don't reference a variable. 'type'/'generate_options' are excepted: both
    # are ordinary enum params with no native GRC attribute marking them as
    # structural (they drive which other params a block exposes) — this is
    # app-level UX policy, not a GRC-derived rule, kept because no
    # dtype/category-based alternative exists without behavior regression.
    if hide == "part" and param_key not in {"type", "generate_options"}:
        is_custom = str(value) != str(default)
        is_var_ref = variable_names and references_variable(value, variable_names)
        if not (is_custom or is_var_ref):
            return False

    if dtype == "enum":
        # Same 'type'/'generate_options' exception as above, applied to the
        # enum-specific keep rule.
        if str(value) != str(default) or param_key in {"type", "generate_options"}:
            return True
        return False
    if str(value) != str(default):
        return True
    if variable_names and references_variable(value, variable_names):
        return True
    return False


def overview_rank(hide: str) -> int:
    """Sort key: ``hide='none'`` first, then ``'part'``, then anything else."""
    return 0 if hide == "none" else 1 if hide == "part" else 2


@cache
def param_metadata(block_type: str) -> dict[str, dict[str, str]]:
    """Static per-param metadata from the GRC block definition.

    Returns ``{param_key: {"category": ..., "dtype": ..., "default": ...}}``
    from one throwaway block instantiation. Empty dict if the platform is
    unavailable.
    """
    try:
        from grc_agent.grc_native_adapter import get_platform_or_none

        platform = get_platform_or_none()
    except Exception as exc:
        logger.debug(
            "param_metadata platform_import_failed block=%s: %s: %s",
            block_type,
            type(exc).__name__,
            exc,
        )
        return {}
    if platform is None:
        logger.debug("param_metadata no_platform block=%s", block_type)
        return {}
    try:
        flow_graph = platform.make_flow_graph()
        block = flow_graph.new_block(block_type)
    except Exception as exc:
        logger.debug(
            "param_metadata new_block_failed block=%s: %s: %s", block_type, type(exc).__name__, exc
        )
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
    except Exception as exc:
        logger.debug(
            "param_metadata collect_failed block=%s: %s: %s", block_type, type(exc).__name__, exc
        )
        return {}


def categories(block_type: str) -> dict[str, str]:
    """``{param_key: category}`` for one block type, from native GRC metadata."""
    return {k: v["category"] for k, v in param_metadata(block_type).items()}


def dtypes(block_type: str) -> dict[str, str]:
    """``{param_key: dtype}`` for one block type, from native GRC metadata."""
    return {k: v["dtype"] for k, v in param_metadata(block_type).items()}


def visible_param_keys(
    block_id: str,
    keys: list[str] | tuple[str, ...],
    param_values: dict[str, Any] | None = None,
) -> list[str]:
    """Details-mode param keys for catalog embed text.

    Applies Stage A (hide + category) via :func:`keep_param` in details
    mode. Falls back to the full key list if GRC evaluation is unavailable.
    """
    hides = evaluated_param_hides(block_id, param_values or {})
    if not hides:
        return list(keys)
    cats = categories(block_id)
    param_dtypes = dtypes(block_id)
    # Missing hide entries default to "none" (kept) — embed text is conservative
    # and never silently drops a param whose visibility is unknown.
    return [
        key
        for key in keys
        if keep_param(
            hide=hides.get(str(key), "none"),
            category=cats.get(str(key), DEFAULT_PARAM_TAB),
            dtype=param_dtypes.get(str(key), ""),
            value="",
            default="",
            mode=DETAILS,
            param_key=str(key),
        )
    ]
