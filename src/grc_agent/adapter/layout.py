from typing import Any

from grandalf.graphs import Edge as GrandalfEdge
from grandalf.graphs import Graph as GrandalfGraph
from grandalf.graphs import Vertex as GrandalfVertex
from grandalf.layouts import SugiyamaLayout, VertexViewer

# Conservative estimate of a block's on-canvas footprint, used only to place
# newly-added blocks without overlap — see change_graph's add_blocks phase
# for why this can't be the block's real rendered size (that's GUI-only and
# unavailable to this headless code path).
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
# single, generously-sized constant is the more honest fix: it costs some
# wasted canvas space for simple blocks, in exchange for not silently
# overlapping busier ones (live-reproduced: a Signal Source with 6 visible
# rows — samp_rate/waveform/freq/amp/offset/phase — placed exactly
# BLOCK_FOOTPRINT_H=100 above a newly-added sink rendered tall enough to
# visibly overlap it, since 100 was sized for a near-empty block).
BLOCK_FOOTPRINT_W = 300
BLOCK_FOOTPRINT_H = 220
BLOCK_SPACING = 60

# One grid step in each axis — a block's footprint plus the spacing gap.
# Used by the full-relayout grid in compute_full_layout/_order_flow_band.
GRID_W = BLOCK_FOOTPRINT_W + BLOCK_SPACING
GRID_H = BLOCK_FOOTPRINT_H + BLOCK_SPACING

# Default placement for the header band's top-left corner (an empty
# canvas). Matches GRC's own default new-block coordinates.
_DEFAULT_PLACE_Y = 12.0

# Floor on header-band column count so a handful of variables in an otherwise
# shallow flowgraph still pack into one wide strip (matching every hand-
# authored fixture in this repo, e.g. tests/data/dial_tone.grc) instead of
# stacking into a tall, narrow column.
_DEFAULT_HEADER_COLS = 6

# classify_role() outcomes that belong in the header band: all zero-port,
# always-short block roles. is_virtual_or_pad blocks are excluded — they are
# genuinely wired (real ports) and belong in the signal-flow band.
_HEADER_ROLES = frozenset({"variable", "options", "import", "snippet"})


def _rects_overlap(ax: float, ay: float, bx: float, by: float) -> bool:
    """AABB collision check with spacing gap. Coordinates are top-left
    corners; both blocks share the same conservative footprint estimate."""
    gap = BLOCK_SPACING
    return (
        ax < bx + BLOCK_FOOTPRINT_W + gap
        and ax + BLOCK_FOOTPRINT_W + gap > bx
        and ay < by + BLOCK_FOOTPRINT_H + gap
        and ay + BLOCK_FOOTPRINT_H + gap > by
    )


def _compute_ranks(  # noqa: C901
    flow_graph: Any, new_block_names: set[str], add_connections: list[str] | None
) -> dict[str, int]:
    """Topological rank (layer index, 0 = sources) for every existing block
    plus every new block about to be added, via grandalf's Sugiyama-style
    layer assignment (proper longest-path ranking with cycle breaking) over
    the full topology — existing connections plus the new ones from this
    same batch. Used only to anchor NEW blocks relative to their real
    distance from a neighbor in the existing graph; an existing block's own
    coordinate is never touched, and its computed rank here is read purely
    as context, never used to move it. Grandalf splits disconnected
    subgraphs into independent components (e.g. a variable block with no
    wire connections), each ranked from its own rank-0 root(s)."""
    from grc_agent.adapter.graph import parse_conn

    vertices: dict[str, Any] = {}
    for b in flow_graph.blocks:
        v = GrandalfVertex(b.name)
        v.view = VertexViewer(w=BLOCK_FOOTPRINT_W, h=BLOCK_FOOTPRINT_H)
        vertices[b.name] = v
    for name in new_block_names:
        if name not in vertices:
            v = GrandalfVertex(name)
            v.view = VertexViewer(w=BLOCK_FOOTPRINT_W, h=BLOCK_FOOTPRINT_H)
            vertices[name] = v

    edges = []
    for c in flow_graph.connections:
        src, dst = c.source_block.name, c.sink_block.name
        if src in vertices and dst in vertices:
            edges.append(GrandalfEdge(vertices[src], vertices[dst]))
    for conn_str in add_connections or []:
        p = parse_conn(conn_str)
        if p and p["src_block"] in vertices and p["dst_block"] in vertices:
            edges.append(GrandalfEdge(vertices[p["src_block"]], vertices[p["dst_block"]]))

    ranks: dict[str, int] = {}
    graph = GrandalfGraph(list(vertices.values()), edges)
    for component in graph.C:
        sug = SugiyamaLayout(component)
        try:
            sug.init_all()
        except Exception:
            continue
        for v in component.sV:
            ranks[v.data] = sug.grx[v].rank
    return ranks


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
    rejected: `_rects_overlap` (the one collision oracle every placement
    path and every overlap test in this codebase shares) always checks
    against the conservative BLOCK_FOOTPRINT_H, regardless of a block's
    actual role, so two header rows spaced any closer than GRID_H register
    as a false overlap against that shared oracle. Matching GRID_H keeps
    exactly one collision assumption for the whole canvas, consistent with
    BLOCK_FOOTPRINT_H's own comment above ("a single generously-sized
    constant is the more honest fix")."""
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


def _order_flow_band(
    flow_blocks: list[Any],
    ranks: dict[str, int],
    predecessors: dict[str, set[str]],
    y_origin: float,
) -> dict[str, tuple[float, float]]:
    """Groups flow-band blocks by grandalf rank (column = rank * GRID_W),
    and orders each rank's blocks vertically by a one-pass barycenter over
    already-placed lower-rank predecessors, falling back to alphabetical
    order for blocks with no resolvable upstream predecessor. This is a
    from-scratch grid assignment — every flow-band block gets a unique
    (rank, row) cell, so no collision search is needed (unlike the old
    per-new-block spiral search this replaced, which solved a different
    problem: finding a gap in an already-fixed layout when only one new
    block moved).

    Deliberately a simple bespoke heuristic rather than grandalf's own
    SugiyamaLayout.draw()/Layer.order() crossing-minimizer — that machinery
    is unused and unverified in this codebase today (only .grx[v].rank is
    read anywhere). Upgrading this function's internals to a real crossing-
    minimizer is a self-contained follow-up if visual quality isn't good
    enough in practice.

    Two disconnected components can legitimately share a rank number
    (grandalf ranks each connected component independently from its own
    rank 0 — see _compute_ranks/test_compute_ranks_reflects_topology): they
    land in the same column but different rows, never overlapping."""
    by_rank: dict[int, list[Any]] = {}
    for b in flow_blocks:
        by_rank.setdefault(ranks.get(b.name, 0), []).append(b)

    positions: dict[str, tuple[float, float]] = {}
    row_index: dict[str, int] = {}
    for rank in sorted(by_rank):
        resolved: list[tuple[float, str, Any]] = []
        unresolved: list[Any] = []
        for b in by_rank[rank]:
            preds = [row_index[p] for p in predecessors.get(b.name, ()) if p in row_index]
            if preds:
                resolved.append((sum(preds) / len(preds), b.name, b))
            else:
                unresolved.append(b)
        resolved.sort(key=lambda t: (t[0], t[1]))
        unresolved.sort(key=lambda b: b.name)
        ordered = [b for _, _, b in resolved] + unresolved

        for i, b in enumerate(ordered):
            positions[b.name] = (rank * GRID_W, y_origin + i * GRID_H)
            row_index[b.name] = i
    return positions


def compute_full_layout(
    flow_graph: Any,
    new_block_names: set[str],
    add_connections: list[str] | None,
    ranks: dict[str, int] | None = None,
) -> dict[str, tuple[float, float]]:
    """Recomputes every block's (x, y) from scratch. `flow_graph.blocks` is
    partitioned by classify_role into a header band (variable/options/
    import/snippet — the zero-port, always-short roles) and a flow band
    (everything else, including virtual_source/virtual_sink/pad_source/
    pad_sink, which are genuinely wired). Called once per change_graph batch
    that adds at least one block (see graph.py's add_blocks phase) — never
    from the manual-edit path, since nothing there calls change_graph.

    `ranks`, if provided, is reused as-is: change_graph already computes it
    once for add_blocks_sorted, and recomputing it here would be a second,
    redundant grandalf pass over the same inputs."""
    from grc_agent.adapter.graph import classify_role, parse_conn

    if ranks is None:
        ranks = _compute_ranks(flow_graph, new_block_names, add_connections)

    header_blocks: list[Any] = []
    flow_blocks: list[Any] = []
    for b in flow_graph.blocks:
        (header_blocks if classify_role(b) in _HEADER_ROLES else flow_blocks).append(b)

    predecessors: dict[str, set[str]] = {}
    for c in flow_graph.connections:
        predecessors.setdefault(c.sink_block.name, set()).add(c.source_block.name)
    for conn_str in add_connections or []:
        p = parse_conn(conn_str)
        if p:
            predecessors.setdefault(p["dst_block"], set()).add(p["src_block"])

    flow_max_rank = max((ranks.get(b.name, 0) for b in flow_blocks), default=-1)
    cols = max(flow_max_rank + 1, _DEFAULT_HEADER_COLS)

    positions = _pack_header_band(header_blocks, cols)
    num_header_rows = -(-len(header_blocks) // cols) if header_blocks else 0
    flow_y_origin = _DEFAULT_PLACE_Y + num_header_rows * GRID_H
    positions.update(_order_flow_band(flow_blocks, ranks, predecessors, flow_y_origin))
    return positions
