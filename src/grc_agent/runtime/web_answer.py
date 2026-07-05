"""LLM condensation for Ollama hosted web_search / web_fetch results.

Raw search results (multiple ``{title, url, content}`` hits) and fetched
pages (a full page body) can run to many thousands of tokens — feeding that
straight into the GRC agent's context would flood it. Both tools instead
route their raw output through one extra LLM call, via
:func:`grc_agent.runtime.llm_client.call_agent_llm`, that distills it into
the most concise answer relevant to the original request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grc_agent.runtime.llm_client import call_agent_llm, cap_words

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent

_MAX_CONTEXT_WORDS = 4000


def summarize_web_search(agent: GrcAgent, query: str, results: list[dict[str, Any]]) -> str:
    """Condense raw web_search results into one concise, query-focused answer."""
    context_parts = [
        f"# Result {i + 1}: {r.get('title', '')} ({r.get('url', '')})\n{r.get('content', '')}"
        for i, r in enumerate(results)
    ]
    context = cap_words("\n\n---\n\n".join(context_parts), _MAX_CONTEXT_WORDS)
    prompt = (
        "You are answering a question using live web search results. Each "
        "result below is a SHORT SNIPPET (title, URL, and a brief excerpt) "
        "— it will rarely contain a complete answer by itself, especially "
        "for technical questions with specific numbers, tables, or "
        "parameter values.\n\n"
        "Extract and report every concrete fact, number, definition, or "
        "table value that IS present in the excerpts below, even if the "
        "overall picture is incomplete. Quote specific values or names "
        "verbatim rather than paraphrasing them away. Cite which source "
        "(by title) each fact comes from.\n\n"
        "If a result looks like the right place to find the full answer "
        "(e.g. a spec page, reference table, or documentation page) but "
        "its excerpt doesn't contain the detail itself, say so explicitly "
        "and name that result's URL as the one worth fetching next for "
        "the full detail. Do not just say the results don't answer the "
        "question — a pointer to the right source is a useful answer.\n\n"
        "Only say the results are unrelated if they are genuinely about a "
        "different topic, not merely incomplete.\n\n"
        f"Question: {query}\n\n"
        f"Search results:\n{context}"
    )
    return call_agent_llm(agent, prompt)


def summarize_web_fetch(
    agent: GrcAgent,
    url: str,
    title: str,
    content: str,
    context_question: str = "",
) -> str:
    """Condense one fetched page's full content into a concise summary."""
    body = cap_words(content, _MAX_CONTEXT_WORDS)
    focus = (
        f"The user's immediate message was: {context_question}\n"
        "Treat this as a hint of what to prioritize, not a strict filter — "
        "it may lack context from earlier in the conversation (e.g. the "
        "specific topic or table this fetch is following up on). If the "
        "page's content doesn't obviously match this hint but clearly "
        "relates to the broader topic implied by the page title/URL, "
        "summarize it anyway rather than discarding it.\n\n"
        if context_question.strip()
        else ""
    )
    prompt = (
        "You are summarizing one fetched web page. Use ONLY the page "
        "content below.\n\n"
        "If the page contains a table, list of numeric values, "
        "specification parameters, or other structured data, REPRODUCE "
        "those exact values (e.g. as a markdown table) rather than "
        "paraphrasing or dropping them — for technical content, precision "
        "beats brevity. Prose summary is fine for everything else.\n\n"
        "If the page only partially covers the topic, say plainly what IS "
        "covered and what's missing, instead of a vague non-answer.\n\n"
        f"{focus}"
        f"Page title: {title}\n"
        f"Page URL: {url}\n\n"
        f"Page content:\n{body}"
    )
    return call_agent_llm(agent, prompt)


__all__ = ["summarize_web_search", "summarize_web_fetch"]
