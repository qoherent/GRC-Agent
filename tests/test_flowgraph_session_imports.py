"""Regression: every name ``flowgraph_session`` consumes from
``grc_native_adapter`` is part of its public-import surface (importable
without an active flowgraph).

The previous shape deferred five ``from grc_native_adapter import ...``
calls inside method bodies; lifting them to module top makes the true
surface visible.
"""

from __future__ import annotations

import inspect

from grc_agent.flowgraph_session import FlowgraphSession


def test_flowgraph_session_imports_the_names_it_uses():
    import grc_agent.flowgraph_session as fs_mod

    # Symbols the module consumes — must be importable via the module's
    # __dict__ (proves the import lives at module top, not inside a method).
    assert "render_flow_graph" in fs_mod.__dict__
    assert "validate" in fs_mod.__dict__
    assert "serialize_raw_data" in fs_mod.__dict__
    assert "exclusive_file_lock" in fs_mod.__dict__
    assert "refuse_ambiguous_save_target" in fs_mod.__dict__
    assert "write_flow_graph_atomic" in fs_mod.__dict__
    assert "write_save_backup" in fs_mod.__dict__


def test_no_method_body_re_imports_grc_native_adapter():
    """No method body in FlowgraphSession may import from
    ``grc_native_adapter`` after this task — every such import lives at
    module top so the dependency graph is visible in one place."""
    forbidden = (
        "from grc_agent.grc_native_adapter",
        "from grc_native_adapter",
    )
    for name, member in inspect.getmembers(
        FlowgraphSession, predicate=inspect.isfunction
    ):
        try:
            src = inspect.getsource(member)
        except OSError:
            continue
        for forbidden_marker in forbidden:
            assert forbidden_marker not in src, (
                f"{name}() still has a deferred import: {forbidden_marker}\n"
                f"{src[:200]}"
            )
