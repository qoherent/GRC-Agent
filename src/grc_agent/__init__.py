"""GRC Agent package."""

# Re-export the main public types so callers can import from one place.
from .agent import GrcAgent
from .catalog.loaders import describe_block
from .doctor import run_doctor
from .flowgraph_session import FlowgraphSession
from ._payload import Block, Connection, Flowgraph
from .retrieval import initialize_retrieval
from .session import get_grc_context, load_grc, summarize_graph
from .startup import RuntimeBootstrapResult, bootstrap_runtime
from .transaction import apply_edit, propose_edit
from .validation import preflight_transaction

__all__ = [
    "Block",
    "bootstrap_runtime",
    "Connection",
    "describe_block",
    "Flowgraph",
    "FlowgraphSession",
    "GrcAgent",
    "get_grc_context",
    "initialize_retrieval",
    "load_grc",
    "apply_edit",
    "preflight_transaction",
    "propose_edit",
    "run_doctor",
    "RuntimeBootstrapResult",
    "summarize_graph",
]
