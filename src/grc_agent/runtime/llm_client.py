"""Shared single-turn LLM call for agent-internal condensation steps.

Both the docs RAG tool (:mod:`grc_agent.runtime.doc_answer`) and the web
search/fetch tools (:mod:`grc_agent.runtime.web_answer`) need to turn a pile
of retrieved text into one concise, grounded answer. :func:`call_agent_llm`
is the single call site for that: it points the OpenAI-compatible client at
whatever backend the agent is already configured with
(``agent._llama_server_url`` / ``agent._llama_model``). Reusing that exact
server + model name is what lets a local Ollama server keep its currently
loaded chat model resident instead of loading a second one; against
OpenRouter there is no such notion, so this is simply a fresh stateless API
call each time.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent


def cap_words(text: str, max_words: int) -> str:
    """Cap ``text`` at ``max_words`` whitespace-separated words."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + f" [TRUNCATED: was {len(words)} words]"


def _openai_base_url(server_url: str) -> str:
    """Normalize a backend server URL to its OpenAI-compatible ``/v1`` endpoint.

    One uniform rule for both backends: append ``/v1`` unless the URL already
    ends with it.
    """
    base = server_url.rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def call_agent_llm(agent: GrcAgent, prompt: str) -> str:
    """Single-turn chat completion against the agent's configured backend.

    No ``extra_body`` overrides are sent — a reasoning-capable Ollama chat
    model must keep its default thinking behavior (see
    ``tests/test_thinking_preservation.py``).
    """
    from openai import OpenAI

    client = OpenAI(
        base_url=_openai_base_url(agent._llama_server_url),
        api_key=os.environ.get("OPENROUTER_API_KEY") or "not-needed",
        timeout=agent._llama_request_timeout_seconds,
    )
    completion = client.chat.completions.create(
        model=agent._llama_model,
        messages=[{"role": "user", "content": prompt}],
    )
    return completion.choices[0].message.content.strip()


__all__ = ["cap_words", "call_agent_llm"]
