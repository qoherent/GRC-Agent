"""Single source of truth for GRC-native parameter filtering.

Every model-visible parameter payload in the agent (``inspect_graph``
overview, catalog vector search) derives its keep/drop decision from
:func:`keep_param` below. No other module may re-implement parameter
filtering logic.

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
    keep if the param is type-controlling (see below) or ``generate_options``,
    even at default — otherwise the model would have no way to see a
    polymorphic block's resolved dtype before it drives port dtypes

All ``hide`` / ``category`` / ``dtype`` / ``default`` values are read off live
GRC blocks (``gnuradio.grc.core``) via throwaway instantiation. ``hide`` is the
GRC-*evaluated* visibility (depends on current param values); the others are
static block-definition metadata. ``is_type_controlling`` (passed into
:func:`keep_param` by every caller) comes from
:func:`type_controlling_params` — also native-derived, from each port's raw
dtype template (``${type}``, ``${itype}``, ...), never a hardcoded name.

Four structural exceptions to the pure hide/category/dtype rule, applied
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
  - ``param_key == 'generate_options'`` (Stage B only, see above): pure
    app-level UX policy — an ordinary enum param with no native GRC
    attribute marking it structural. The weakest-justified of the four; no
    dtype/category-based alternative was found.
"""

from __future__ import annotations

import logging
import re
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

DETAILS = "details"  # Stage A only (catalog search / embed)
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
    is_type_controlling: bool = False,
) -> bool:
    """THE parameter keep/drop rule. Single authority for every tool.

    Returns True if the param survives the pipeline for the given ``mode``.
    Pure function — no I/O, no side effects. ``is_type_controlling`` is
    precomputed by the caller via
    :func:`grc_agent.runtime.param_filter.type_controlling_params` (a
    native-derived check, not a hardcoded name) — kept as a plain bool
    parameter here so this function stays pure with no lookup of its own.
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

    # Stage B, first OR-branch (see module docstring): GRC's own 'hide=none'
    # means "always show this param" — independent of whether its current
    # value equals its default. The value/variable comparisons below apply
    # only to params GRC does NOT mark always-visible; a 'hide=none' param
    # whose value happens to equal its native default (e.g. analog_sig_
    # source_x's 'freq' defaults to '1000') must not disappear from the
    # overview just because nothing was customized.
    if hide == "none":
        return True

    # Drop 'hide: part' parameters in overview mode if they match default and
    # don't reference a variable. Type-controlling params (native-derived —
    # see module docstring) and 'generate_options' are excepted:
    # type-controlling params drive port dtypes; 'generate_options' is an
    # ordinary enum param with no native GRC attribute marking it as
    # structural — app-level UX policy, kept because no
    # dtype/category-based alternative exists without behavior regression.
    is_structural_enum = is_type_controlling or param_key == "generate_options"
    if hide == "part" and not is_structural_enum:
        is_custom = str(value) != str(default)
        is_var_ref = variable_names and references_variable(value, variable_names)
        if not (is_custom or is_var_ref):
            return False

    if dtype == "enum":
        # Same structural-enum exception as above, applied to the
        # enum-specific keep rule.
        if str(value) != str(default) or is_structural_enum:
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


def _throwaway_block(block_type: str, *, caller: str) -> Any | None:
    """One throwaway block instance for static native-metadata introspection.

    Shared platform/flow_graph/new_block dance used by both
    :func:`param_metadata` and :func:`port_metadata` — the only difference
    between the two was this setup step; the ``caller`` name only changes
    the debug-log prefix. Returns ``None`` if the platform is unavailable,
    instantiation fails, or the block type resolves to a control block
    (variable, parameter, ...) that the platform does not model as an
    instance block.
    """
    try:
        from grc_agent.grc_native_adapter import get_platform_or_none

        platform = get_platform_or_none()
    except Exception as exc:
        logger.debug(
            "%s platform_import_failed block=%s: %s: %s",
            caller,
            block_type,
            type(exc).__name__,
            exc,
        )
        return None
    if platform is None:
        logger.debug("%s no_platform block=%s", caller, block_type)
        return None
    try:
        flow_graph = platform.make_flow_graph()
        return flow_graph.new_block(block_type)
    except Exception as exc:
        logger.debug(
            "%s new_block_failed block=%s: %s: %s", caller, block_type, type(exc).__name__, exc
        )
        return None


def param_metadata(block_type: str) -> dict[str, dict[str, str]]:
    """Static per-param metadata from the GRC block definition.

    Returns ``{param_key: {"category": ..., "dtype": ..., "default": ...}}``
    from one throwaway block instantiation. Empty dict if the platform is
    unavailable.
    """
    block = _throwaway_block(block_type, caller="param_metadata")
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


def port_metadata(block_type: str) -> dict[str, dict[str, dict[str, Any]]]:
    """Static per-port metadata from the GRC block definition.

    Returns ``{"inputs": {port_key: {"hidden": bool, "raw_dtype": str,
    "raw_multiplicity": str}}, "outputs": {...}}`` from one throwaway block
    instantiation (sinks and sources kept separate, since stream ports on
    each side commonly share the same numeric keys). ``raw_dtype`` and
    ``raw_multiplicity`` are the port's *unevaluated* dtype/port-count
    templates (e.g. dtype ``"type"``/``"itype"``, multiplicity
    ``"num_inputs"``/``"num_streams"``) read off GRC's own ``Evaluated``
    descriptor's private raw-value slots (``_dtype``, ``_multiplicity``) —
    the only place the un-evaluated expression is preserved; there is no
    public accessor for either. Empty dict if the platform is unavailable.
    """
    block = _throwaway_block(block_type, caller="port_metadata")
    if block is None:
        return {}
    try:

        def _collect(ports: Any) -> dict[str, dict[str, Any]]:
            return {
                str(port.key): {
                    "hidden": bool(getattr(port, "hidden", False)),
                    "raw_dtype": str(getattr(port, "_dtype", "") or ""),
                    "raw_multiplicity": str(getattr(port, "_multiplicity", "") or ""),
                }
                for port in ports
            }

        return {
            "inputs": _collect(getattr(block, "sinks", ()) or ()),
            "outputs": _collect(getattr(block, "sources", ()) or ()),
        }
    except Exception as exc:
        logger.debug(
            "port_metadata collect_failed block=%s: %s: %s", block_type, type(exc).__name__, exc
        )
        return {}


def hidden_port_keys(block_type: str, *, direction: str) -> frozenset[str]:
    """Port keys marked ``hidden`` on the GRC block definition (Stage A for ports).

    ``direction`` is ``"inputs"`` or ``"outputs"``.
    """
    return frozenset(
        key for key, info in port_metadata(block_type).get(direction, {}).items() if info["hidden"]
    )


def type_controlling_params(block_type: str) -> frozenset[str]:
    """Param ids that control a port's dtype, derived from native port
    templates — never from a hardcoded name like ``"type"``.

    A port's raw (unevaluated) dtype template names the param that drives
    it: ``${type}`` -> ``"type"``; ``${itype}``/``${otype}`` for multi-type
    blocks like ``fec_generic_encoder``, which has no param literally named
    ``"type"`` at all. Intersecting every port's referenced identifier(s)
    with the block's own enum param ids gives the exact type-controlling
    set for this specific block, with zero per-block-name special-casing.
    """
    enum_params = {k for k, v in param_metadata(block_type).items() if v["dtype"] == "enum"}
    if not enum_params:
        return frozenset()
    referenced: set[str] = set()
    for direction_meta in port_metadata(block_type).values():
        for info in direction_meta.values():
            raw = info["raw_dtype"]
            if raw:
                referenced.update(_IDENTIFIER_RE.findall(raw))
    return frozenset(enum_params & referenced)


def port_count_controlling_params(block_type: str) -> frozenset[str]:
    """Param ids that control how many physical ports a block has, derived
    from native port templates — never from a hardcoded name like
    ``"num_inputs"``.

    A port's raw (unevaluated) multiplicity template names the param that
    drives its count: ``${num_inputs}`` -> ``"num_inputs"`` for
    ``blocks_add_xx``, but ``${num_streams}`` for ``pad_source`` — there is
    no single conventional name. Intersecting every port's referenced
    identifier(s) with the block's own param ids (any dtype — these are
    ordinary ``int`` params, not enums) gives the exact port-count-
    controlling set for this specific block, with zero per-block-name
    special-casing.
    """
    param_ids = set(param_metadata(block_type).keys())
    if not param_ids:
        return frozenset()
    referenced: set[str] = set()
    for direction_meta in port_metadata(block_type).values():
        for info in direction_meta.values():
            raw = info["raw_multiplicity"]
            if raw:
                referenced.update(_IDENTIFIER_RE.findall(raw))
    return frozenset(param_ids & referenced)


def ports_governed_by(block_type: str, param_key: str) -> tuple[frozenset[str], frozenset[str]]:
    """``(input port keys, output port keys)`` whose raw dtype template
    references ``param_key``.

    The direction-aware complement of :func:`type_controlling_params` —
    needed to resolve multi-param blocks correctly (e.g. ``fec_generic_encoder``'s
    ``itype``/``otype`` each govern only one side; assigning both from
    whichever neighbor happens to be found first would be wrong).
    """
    meta = port_metadata(block_type)

    def _match(direction: str) -> frozenset[str]:
        return frozenset(
            key
            for key, info in meta.get(direction, {}).items()
            if param_key in _IDENTIFIER_RE.findall(info["raw_dtype"])
        )

    return _match("inputs"), _match("outputs")


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
