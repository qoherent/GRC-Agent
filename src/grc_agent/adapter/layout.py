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

# Default placement when there are no neighbors and no existing bounding box
# (an empty canvas). Matches GRC's own default new-block coordinates.
_DEFAULT_PLACE_X = 200.0
_DEFAULT_PLACE_Y = 12.0

# Maximum ring-search radius for the collision-avoidance spiral. Each ring is
# one grid step (BLOCK_FOOTPRINT + BLOCK_SPACING), so 60 rings covers a very
# large canvas — if no slot is found by then, the graph is so dense that the
# linear fallback (place to the right of everything) is more legible anyway.
_MAX_SEARCH_RINGS = 60


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


def _find_block_placement(  # noqa: C901
    new_block_name: str,
    occupied: list[tuple[float, float]],
    neighbor_map: dict[str, set[str]],
    block_coords: dict[str, tuple[float, float]],
    bbox: tuple[float, float, float, float],
    ranks: dict[str, int] | None = None,
) -> tuple[float, float]:
    """Find a non-overlapping position for a new block.

    Prioritizes placement near connected neighbors (from the same batch's
    add_connections), anchored by each neighbor's grandalf-computed rank
    distance (see _compute_ranks). Never places a downstream block to the left
    of its upstream providers, maintaining a clean left-to-right signal flow.
    """
    grid_w = BLOCK_FOOTPRINT_W + BLOCK_SPACING
    grid_h = BLOCK_FOOTPRINT_H + BLOCK_SPACING

    # 1. Find connected neighbors' coordinates and min_allowed_x for downstream blocks
    neighbor_coords = []
    my_rank = (ranks or {}).get(new_block_name)
    min_allowed_x = 0.0

    for other in neighbor_map.get(new_block_name, ()):
        if other not in block_coords:
            continue
        ox, oy = block_coords[other]
        other_rank = (ranks or {}).get(other)
        if my_rank is not None and other_rank is not None:
            rank_diff = my_rank - other_rank
            neighbor_coords.append((ox + rank_diff * grid_w, oy))
            if rank_diff > 0:
                # 'other' is upstream of us — we must stay at or to the right of (ox + grid_w)
                min_allowed_x = max(min_allowed_x, ox + grid_w)
        else:
            neighbor_coords.append((ox + grid_w, oy))

    # 2. Compute target point
    if neighbor_coords:
        target_x = sum(c[0] for c in neighbor_coords) / len(neighbor_coords)
        target_y = sum(c[1] for c in neighbor_coords) / len(neighbor_coords)
    elif bbox:
        # No connections — place at graph centroid to fill empty space
        target_x = (bbox[0] + bbox[2]) / 2
        target_y = (bbox[1] + bbox[3]) / 2
    else:
        target_x = _DEFAULT_PLACE_X
        target_y = _DEFAULT_PLACE_Y

    # Ensure target_x respects min_allowed_x
    target_x = max(target_x, min_allowed_x)

    # 3. Snap target to grid
    gx = max(min_allowed_x, round(target_x / grid_w) * grid_w)
    gy = max(0.0, round(target_y / grid_h) * grid_h)

    # Check target position first
    if gx >= 0 and gy >= 0 and not any(_rects_overlap(gx, gy, ox, oy) for ox, oy in occupied):
        return (gx, gy)

    # 4. Directionally prioritized grid search:
    #    Test same-column vertical offsets first (dx=0), then forward downstream (dx>0),
    #    and backwards (dx<0) only if allowed by min_allowed_x.
    dx_sequence = [0]
    for d in range(1, _MAX_SEARCH_RINGS):
        dx_sequence.append(d)
        dx_sequence.append(-d)

    dy_sequence = [0]
    for d in range(1, _MAX_SEARCH_RINGS):
        dy_sequence.append(d)
        dy_sequence.append(-d)

    for ring in range(1, _MAX_SEARCH_RINGS):
        for dx in dx_sequence:
            for dy in dy_sequence:
                if max(abs(dx), abs(dy)) != ring:
                    continue
                cx = gx + dx * grid_w
                cy = gy + dy * grid_h
                if cx < min_allowed_x or cy < 0:
                    continue
                if not any(_rects_overlap(cx, cy, ox, oy) for ox, oy in occupied):
                    return (cx, cy)

    # 5. Fallback: place to the right of everything
    fallback_x = max(o[0] for o in occupied) + grid_w if occupied else _DEFAULT_PLACE_X
    fallback_x = max(fallback_x, min_allowed_x)
    return (fallback_x, gy)
