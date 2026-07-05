"""GRC Agent package."""

# Re-export the main public types so callers can import from one place.
from .agent import GrcAgent
from .catalog.loaders import describe_block
from .flowgraph_session import FlowgraphSession
from .retrieval import initialize_retrieval
from .session import load_grc, summarize_graph
from .startup import RuntimeBootstrapResult, bootstrap_runtime

__all__ = [
    "bootstrap_runtime",
    "describe_block",
    "FlowgraphSession",
    "GrcAgent",
    "initialize_retrieval",
    "load_grc",
    "RuntimeBootstrapResult",
    "summarize_graph",
]
