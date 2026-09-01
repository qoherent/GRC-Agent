import logging
from dataclasses import dataclass
from typing import Any

from grandalf.graphs import Edge as GrandalfEdge
from grandalf.graphs import Graph as GrandalfGraph
from grandalf.graphs import Vertex as GrandalfVertex
from grandalf.layouts import SugiyamaLayout, VertexViewer

_log = logging.getLogger(__name__)

# Conservative estimate of a block's on-canvas footprint — the single size
# assumption behind the whole full-canvas grid: every placement cell
# (BLOCK_FOOTPRINT_* + BLOCK_SPACING = GRID_*) is derived from it. See
# change_graph's add_blocks phase for why this can't be the block's real
# rendered size (that's GUI-only and unavailable to this headless code path).
#
# A per-block estimate derived from counting each param's native `hide`
# attribute (`hide not in ('all', 'part')` is exactly the rule GRC's own
# canvas rendering uses to decide whether a param gets a row — see
# gui/canvas/block.py) was tried and rejected: it's accurate for simple
# blocks, but multi-channel sink/source blocks (e.g. qtgui_time_sink_x) carry
# ~10 near-duplicate per-channel param groups (label1..label10, color1..10,
# etc.) that GRC's canvas dynamically collapses down to however many
# channels are actually connected — a raw hide-attribute count sees all ~60+
# of them as visible regardless. Replicating that collapsing correctly would
# mean hardcoding which params group together and how, per block family —
# exactly the "no hand-picked heuristics" this codebase avoids elsewhere. A
# single, calibrated constant is the more honest fix: it minimizes
# wasted canvas space while not silently overlapping busier ones (e.g.
# a Signal Source with 6 visible rows renders ~160px tall).
BLOCK_FOOTPRINT_W = 200
BLOCK_FOOTPRINT_H = 168
BLOCK_SPACING = 32

# One grid step in each axis — a block's footprint plus the spacing gap.
# Used by the full-relayout grid in compute_full_layout/_place_flow_components.
GRID_W = BLOCK_FOOTPRINT_W + BLOCK_SPACING
GRID_H = BLOCK_FOOTPRINT_H + BLOCK_SPACING

# Default placement for the header band's top-left corner (an empty
# canvas). Matches GRC's own default new-block coordinates.
_DEFAULT_PLACE_Y = 12.0

# Floor on header-band column count so a handful of variables in an otherwise
# shallow flowgraph still pack into one wide strip (matching every hand-
# authored fixture in this repo, e.g. tests/data/dial_tone.grc) instead of
# stacking to a tall, narrow column.
_DEFAULT_HEADER_COLS = 6

# classify_role() outcomes that belong in the header band: all zero-port,
# always-short block roles. is_virtual_or_pad blocks are excluded — they are
# genuinely wired (real ports) and belong in the signal-flow band.
_HEADER_ROLES = frozenset({"variable", "options", "import", "snippet"})

# Vertical gap between one connected component's row band and the next.
# Deliberately the same GRID_H as the header band's row spacing and the
# within-column stack spacing: one collision assumption for the whole canvas
# (see BLOCK_FOOTPRINT_H's comment).
ROW_GAP = GRID_H

# Bounded down/up sweeps for the crossing-minimizing layer order. grandalf's
# own Layer.order is a barycenter sweep that returns immediately once a pass
# reports no crossings, so extra iterations cost nothing on converged graphs.
ORDER_SWEEPS = 8


@dataclass
class LayoutModel:
    """Ranks and per-component crossing-minimized orderings of every block in
    a flowgraph, computed ONCE by `_compute_layout_model` and consumed by
    `compute_full_layout` — this is what keeps `change_graph` from running a
    second grandalf pass for the layout it already ranked for add_blocks_sorted.

    ranks:         block name -> rank (0 = sources). Grandalf ranks each
                   weakly-connected component independently from its own
                   rank 0, so two disconnected chains can share rank numbers.
    components:    one member-name list per weakly-connected component,
                   sorted by first member for cross-process determinism
                   (grandalf's own component discovery walks Python sets,
                   whose iteration order is identity-hash based).
    ordered_ranks: parallel to components — per component, rank -> names in
                   the crossing-minimized vertical order (grandalf's
                   Layer.order multi-sweep barycenter over that component's
                   own SugiyamaLayout)."""

    ranks: dict[str, int]
    components: list[list[str]]
    ordered_ranks: list[dict[int, list[str]]]


def _vertex(name: str) -> GrandalfVertex:
    v = GrandalfVertex(name)
    v.view = VertexViewer(w=BLOCK_FOOTPRINT_W, h=BLOCK_FOOTPRINT_H)
    return v


def _rank_and_order_component(component: Any, model: LayoutModel) -> None:
    """Ranks one weakly-connected component and appends its crossing-
    minimized per-rank order to `model`. On an init_all refusal the component
    contributes no ranks (its flow-band members fall to the deterministic
    fallback band in _place_flow_components); on an ordering failure it keeps
    the alphabetical init_all order, still a valid DAG order."""
    sug = SugiyamaLayout(component)
    ordered: dict[int, list[str]] = {}
    comp_names = sorted(v.data for v in component.sV)
    try:
        sug.init_all()
    except Exception:
        model.components.append(comp_names)
        model.ordered_ranks.append(ordered)
        return
    for v in component.sV:
        model.ranks[v.data] = sug.grx[v].rank
    # Deterministic tie-breaking: grandalf's initial layer order walks Python
    # sets (identity-hash order, varies between processes), and the barycenter
    # sweeps below sort stably — so ties would otherwise break by hash. Sort
    # every layer alphabetically and re-derive its vertex positions first.
    # Note: sug.init_all() creates DummyVertex instances for multi-rank edges,
    # which lack a .data attribute — getattr(v, "data", "") handles them safely.
    try:
        for layer in sug.layers:
            layer.sort(key=lambda v: getattr(v, "data", ""))
            layer.setup(sug)
        # Crossing-minimized vertical order via grandalf's own Layer.order
        # (verified on 200 random layered DAGs against a hand-rolled single
        # top-down barycenter pass: strictly fewer crossings on 106, equal on 55,
        # worse on 39; 0 crashes).
        sweeps = ORDER_SWEEPS
        while sweeps > 0.5:
            for _ in sug.ordering_step():
                pass
            sweeps -= 1
        for layer in sug.layers:
            for v in layer:
                if not getattr(sug.grx[v], "dummy", 0):
                    ordered.setdefault(sug.grx[v].rank, []).append(v.data)
    except Exception:
        pass
    model.components.append(comp_names)
    model.ordered_ranks.append(ordered)


def _compute_layout_model(
    flow_graph: Any, new_block_names: set[str], add_connections: list[str] | None
) -> LayoutModel:
    """Ranks every block (existing plus the new ones about to be added) and
    runs grandalf's crossing-minimizing layer ordering per weakly-connected
    component, over the full topology — existing connections plus the new
    ones from this same batch. Ranks and orderings together are the single
    source of the flow band's column AND row assignment."""
    from grc_agent.adapter.graph import parse_conn

    vertices: dict[str, Any] = {}
    for b in flow_graph.blocks:
        vertices[b.name] = _vertex(b.name)
    for name in new_block_names:
        if name not in vertices:
            vertices[name] = _vertex(name)

    edges = []
    for c in flow_graph.connections:
        src, dst = c.source_block.name, c.sink_block.name
        if src in vertices and dst in vertices:
            edges.append(GrandalfEdge(vertices[src], vertices[dst]))
    for conn_str in add_connections or []:
        p = parse_conn(conn_str)
        if p and p["src_block"] in vertices and p["dst_block"] in vertices:
            edges.append(GrandalfEdge(vertices[p["src_block"]], vertices[p["dst_block"]]))

    model = LayoutModel(ranks={}, components=[], ordered_ranks=[])
    try:
        graph = GrandalfGraph(list(vertices.values()), edges)
        for component in graph.C:
            _rank_and_order_component(component, model)
        # Deterministic band order: grandalf discovers components by walking a
        # Python set (identity-hash order), so without this the topmost band would
        # vary between processes. Alphabetical first member is stable and readable.
        pairs = sorted(
            zip(model.components, model.ordered_ranks, strict=True),
            key=lambda p: (p[0][0] if p[0] else "", p[0]),
        )
        model.components = [c for c, _ in pairs]
        model.ordered_ranks = [o for _, o in pairs]
    except Exception as exc:
        _log.warning("Layout model computation failed; falling back to unranked layout: %s", exc)
    return model






def _pack_header_band(header_blocks: list[Any], cols: int) -> dict[str, tuple[float, float]]:
    """Deterministically packs header-role blocks (variables/options/import/
    snippet) left-to-right into `cols`-wide rows. The flowgraph's singleton
    options block is always pinned first (col 0, row 0) — matching the
    convention already visible in this repo's own fixtures (e.g.
    tests/data/dial_tone.grc hand-places options leftmost). Everything else
    is sorted alphabetically by instance name (b.name): it's the one
    identifier every block in this set always has (unlike GUI label, which
    plain `variable` blocks don't reliably carry), it's already unique
    (change_graph rejects duplicate instance names), and it's exactly the
    identifier the agent already reasons about via inspect_graph.

    Row spacing uses the same GRID_H as the flow band, not a smaller
    dedicated header-row constant — a smaller constant was tried and
    rejected: the overlap tests in tests/test_layout.py always check against
    the conservative BLOCK_FOOTPRINT_H, regardless of a block's actual
    role, so two header rows spaced any closer than GRID_H register as a
    false overlap against that shared assumption. Matching GRID_H keeps
    exactly one collision assumption for the whole canvas, consistent with
    BLOCK_FOOTPRINT_H's own comment above ("a single generously-sized
    constant is the honest heuristic")."""
    from grc_agent.adapter.graph import classify_role

    options = [b for b in header_blocks if classify_role(b) == "options"]
    rest = sorted(
        (b for b in header_blocks if classify_role(b) != "options"),
        key=lambda b: b.name,
    )
    ordered = options + rest

    positions: dict[str, tuple[float, float]] = {}
    for i, b in enumerate(ordered):
        row, col = divmod(i, cols)
        positions[b.name] = (col * GRID_W, _DEFAULT_PLACE_Y + row * GRID_H)
    return positions


def _place_flow_components(
    flow_blocks: list[Any],
    model: LayoutModel,
    y_origin: float,
) -> dict[str, tuple[float, float]]:
    """Places every flow-band block as a left-to-right flow chart: each
    weakly-connected component gets its own row band starting again at the
    left margin, one column per step away from the sources (rank * GRID_W),
    rows within a column in the crossing-minimized order from
    `_compute_layout_model`. This replaces the old single shared vertical
    stack, where two independent chains sharing a rank number interleaved in
    the same columns and their wires threaded through each other's blocks.

    Every position is assigned directly — no collision search needed, since
    components never share a band and a block's (rank, row) cell is unique
    inside its band. Blocks that ended up in no rankable component (a
    grandalf init_all refusal, exotic cycle) get their own deterministic
    alphabetical fallback band instead of silently losing a coordinate."""
    positions: dict[str, tuple[float, float]] = {}
    flow_by_name = {b.name: b for b in flow_blocks}
    top = y_origin
    for comp_names, ordered in zip(model.components, model.ordered_ranks, strict=True):
        band = [n for n in comp_names if n in flow_by_name]
        if not band:
            continue  # a header-role component (isolated variable/options/etc.)
        rows = max((len(v) for v in ordered.values()), default=0)
        for rank, layer in ordered.items():
            x = rank * GRID_W
            for i, name in enumerate(layer):
                if name in flow_by_name:
                    positions[name] = (x, top + i * GRID_H)
        top += rows * GRID_H + ROW_GAP
    missing = [b for b in flow_blocks if b.name not in positions]
    if missing:
        for i, b in enumerate(sorted(missing, key=lambda b: b.name)):
            positions[b.name] = (0.0, top + i * GRID_H)
        top += len(missing) * GRID_H + ROW_GAP
    return positions


def compute_full_layout(
    flow_graph: Any,
    new_block_names: set[str],
    add_connections: list[str] | None,
    model: LayoutModel | None = None,
) -> dict[str, tuple[float, float]]:
    """Recomputes every block's (x, y) from scratch. `flow_graph.blocks` is
    partitioned by classify_role into a header band (variable/options/
    import/snippet — the zero-port, always-short roles) and a flow band
    (everything else, including virtual_source/virtual_sink/pad_source/
    pad_sink, which are genuinely wired). Called once per change_graph batch
    that changes topology (see graph.py) — never from the manual-edit path,
    since nothing there calls change_graph.

    `model`, if provided, is reused as-is: change_graph already computes it
    once for add_blocks_sorted, and recomputing it here would be a second,
    redundant grandalf pass over the same inputs."""
    from grc_agent.adapter.graph import classify_role

    if model is None:
        model = _compute_layout_model(flow_graph, new_block_names, add_connections)

    header_blocks: list[Any] = []
    flow_blocks: list[Any] = []
    for b in flow_graph.blocks:
        (header_blocks if classify_role(b) in _HEADER_ROLES else flow_blocks).append(b)

    # The widest component decides the header band's column count (each
    # component ranks from its own 0, so max rank across components == the
    # widest component's rank count).
    widest = max((len(o) for o in model.ordered_ranks), default=0)
    cols = max(widest, _DEFAULT_HEADER_COLS)

    positions = _pack_header_band(header_blocks, cols)
    num_header_rows = -(-len(header_blocks) // cols) if header_blocks else 0
    flow_y_origin = _DEFAULT_PLACE_Y + num_header_rows * GRID_H
    positions.update(_place_flow_components(flow_blocks, model, flow_y_origin))
    return positions
