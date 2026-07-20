import logging

_log = logging.getLogger(__name__)


async def lite_web_search(query: str) -> str:
    """Local web-search fallback for pydantic-ai's provider-adaptive
    ``WebSearch`` capability on providers without native search (Ollama).

    Uses the ``ddgs`` library (DuckDuckGo's official Python client), which
    hits the API endpoint directly instead of scraping HTML — the old
    ``lite.duckduckgo.com`` scrape was blocked by a CAPTCHA challenge
    ("Select all squares containing a duck") as of 2026-07.

    Network errors propagate naturally (the ``ddgs`` client raises
    ``httpx.HTTPStatusError`` on failure) so a backend failure surfaces
    honestly instead of being masked as "no results".
    """
    # ddgs.text() is sync — run it in a thread to avoid blocking the gbulb
    # event loop. The function is called from pydantic-ai's async tool
    # dispatcher, so we're already in an async context.
    import asyncio

    def _run_search() -> list[dict]:
        # Import is deferred to first call so the module can be imported
        # in environments where ddgs isn't installed (e.g. CI without the
        # optional dep).
        from ddgs import DDGS

        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=5))

    try:
        results = await asyncio.to_thread(_run_search)
    except Exception as exc:
        _log.warning("lite_web_search: ddgs call failed for %r: %s", query, exc)
        return f"Web search failed: {exc}"

    if not results:
        _log.warning("lite_web_search: no results for %r", query)
        return f"No web results found for: {query}"

    formatted = []
    for r in results:
        title = r.get("title", "")
        url = r.get("href") or r.get("url", "")
        body = r.get("body", "")
        formatted.append(f"{title}\n{url}\n{body}")

    return "\n\n".join(formatted)
