"""Public surface of the GRC adapter.

Exports only the names other packages in ``grc_agent`` consume. Everything
else - including every underscore-prefixed helper - is imported from the
module that defines it (``grc_agent.adapter.graph``, ``.rag``, ``.layout``,
``.block_library``), by production code and tests alike. A re-export layer
that exists so tests can reach internals turns those internals into public
API and hides where they really live.
"""

from grc_agent.adapter.block_library import save_block_to_library
from grc_agent.adapter.graph import (
    change_graph,
    flow_graph_content_hash,
    get_blocks_panel_visibility,
    get_gui_platform,
    get_platform,
    gui_actions,
    gui_application_cls,
    inspect_graph,
    install_untitled_save_folder_provider,
    load_flow_graph,
    preview_flowgraph_py,
    register_execution_messenger,
    set_blocks_panel_visibility,
    set_param,
)
from grc_agent.adapter.rag import (
    build_status,
    embed_document,
    embed_documents,
    get_db_and_model,
    query_catalog,
    query_docs,
    render_catalog_block,
)

__all__ = [
    "build_status",
    "change_graph",
    "embed_document",
    "embed_documents",
    "flow_graph_content_hash",
    "get_blocks_panel_visibility",
    "get_db_and_model",
    "get_gui_platform",
    "get_platform",
    "gui_actions",
    "gui_application_cls",
    "inspect_graph",
    "install_untitled_save_folder_provider",
    "load_flow_graph",
    "preview_flowgraph_py",
    "query_catalog",
    "query_docs",
    "register_execution_messenger",
    "render_catalog_block",
    "save_block_to_library",
    "set_blocks_panel_visibility",
    "set_param",
]
