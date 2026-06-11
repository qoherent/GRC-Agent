"""System prompt builder for the GRC Agent.

Revision history
----------------
``2026-06-11-seamless-v1``
    Terminal configuration from empirical Phase 1–7 experiments (27 traces).
    Seamless single-paragraph prompt with echo→action bridge and structural
    domain rules. No section headers, no behavioral commands (ALWAYS/NEVER),
    no preachiness. Tool schemas sanitized in parallel. Architecturally
    isolated from model scale constraints — designed to scale to 32B+.
"""

__version__ = "2026-06-11-seamless-v1"


def build_system_prompt(session_id: str | None = None) -> str:
    """Return the full MVP wrapper-only system prompt shipped to the model, optionally isolated by session_id."""
    prefix = f"Session ID: {session_id}\n" if session_id else ""
    return prefix + (
        "You are a GNU Radio graph editing assistant. "
        "First, echo the user's complete request in your own words by explicitly listing "
        "every block and connection required, and then immediately execute the necessary "
        "tools to fulfill it. "
        "Keep these structural rules in mind while editing: "
        "variables are blocks (use add_blocks, update_params, remove_blocks). "
        "To insert a block on an existing wire, you must batch remove_connections, "
        "add_blocks, and add_connections together in a single payload. "
        "An input port can only accept one connection. "
        "To deactivate a block without severing paths, use update_states with 'bypass'. "
        "Use force=true only if you must commit an invalid intermediate graph state to progress."
    )
