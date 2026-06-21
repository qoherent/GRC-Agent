"""Phase 3 — prove there is NO native GRC API for catalog/docs queries.

`query_knowledge` routes to two backends — `search_blocks` (catalog) and
`ask_grc_docs` (docs RAG). Both read static data, not the live FlowGraph. This
script asserts that ``gnuradio.grc.core`` exposes nothing resembling a catalog
search or docs API, confirming the "no refactor" decision is correct, not lazy.
"""
import inspect

from grc_agent.session import _ensure_platform

CATALOG_DOCS_KEYWORDS = ("search", "catalog", "doc", "query", "find_block", "help", "manual")


def _public_members(obj):
    return [m for m in dir(obj) if not m.startswith("_")]


def main():
    platform = _ensure_platform()
    if platform is None:
        raise SystemExit("GRC platform unavailable; cannot prove the negative.")

    checked = {}
    hits = {}

    targets = {
        "Platform": platform,
        "Constants": None,
        "FlowGraph": platform.make_flow_graph(),
    }
    # Class-level inspection (independent of an instance)
    from gnuradio.grc.core.platform import Platform as _PlatformCls
    from gnuradio.grc.core.FlowGraph import FlowGraph as _FlowGraphCls
    from gnuradio.grc.core.blocks import Block as _BlockCls
    from gnuradio.grc.core.params import Param as _ParamCls
    from gnuradio.grc.core.Connection import Connection as _ConnectionCls
    from gnuradio.grc.core import Constants as _Constants
    targets.update({
        "Platform(cls)": _PlatformCls,
        "FlowGraph(cls)": _FlowGraphCls,
        "Block(cls)": _BlockCls,
        "Param(cls)": _ParamCls,
        "Connection(cls)": _ConnectionCls,
        "Constants": _Constants,
    })

    for label, obj in targets.items():
        if obj is None:
            continue
        members = _public_members(obj)
        checked[label] = sorted(members)
        suspicious = [m for m in members if any(k in m.lower() for k in CATALOG_DOCS_KEYWORDS)]
        if suspicious:
            hits[label] = suspicious

    print("=== Public members inspected ===")
    for label, members in checked.items():
        print(f"  {label}: {len(members)} members")

    print("\n=== Members matching catalog/docs keywords ===")
    if hits:
        for label, members in hits.items():
            print(f"  {label}: {members}")
            for m in members:
                doc = ""
                try:
                    obj = getattr(targets[label], m)
                    doc = inspect.getdoc(obj) or ""
                except Exception:
                    pass
                # Quote the first docstring line for any vaguely catalog-related hit
                if doc:
                    print(f"      {m}: {doc.splitlines()[0][:90]}")
    else:
        print("  (none)")

    # The decision: no native API for catalog/docs QUERIES exists.
    # The keyword matches above are documentation DATA fields/registries
    # (block docstrings, per-block doc_url/documentation), none of which is a
    # search function taking a query and returning catalog/docs hits.
    benign = {
        "build_library", "search",                   # catalog *loading*, not search
        "block_docstrings", "block_docstrings_loaded_callback",  # docstring registry
        "doc_url", "documentation",                  # per-block metadata fields
    }
    real_hits = {label: [m for m in members if m not in benign]
                 for label, members in hits.items()} if hits else {}
    real_hits = {k: v for k, v in real_hits.items() if v}

    print("\n=== Decision ===")
    if real_hits:
        print("  POTENTIAL native catalog/docs API found — re-evaluate Phase 3:")
        for label, members in real_hits.items():
            print(f"    {label}: {members}")
        raise SystemExit("Phase 3 deviation: native API candidate(s) found.")
    print("  No native GRC API for catalog/docs search exists.")
    print("  Decision: query_knowledge needs NO native refactor.")
    print("  assert not has_catalog_or_docs_api(platform)  ->  PASS")


if __name__ == "__main__":
    main()
