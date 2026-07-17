import logging
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

_log = logging.getLogger(__name__)


async def lite_web_search(query: str) -> str:
    """Local web-search fallback for pydantic-ai's provider-adaptive
    ``WebSearch`` capability on providers without native search (Ollama).

    Scrapes ``lite.duckduckgo.com`` (the one DuckDuckGo surface that still
    returns real results for this client). Network errors propagate via
    ``raise_for_status`` so a backend failure surfaces honestly instead of
    being masked as "no results".

    Two failure modes that previously looked identical to a genuine
    "no results" are made diagnosable here via logging (per the project's
    "no silent transformation" rule):

    * a 200 response that parses ZERO result anchors — either a real
      no-results query OR DuckDuckGo's HTML selectors having drifted. The
      empty parse alone can't tell them apart, so a warning naming both
      possibilities is logged instead of masking drift as "no results".
    * a link/snippet selector-count mismatch (page layout changed) — logged
      rather than silently truncated by ``zip(..., strict=False)``.
    """
    async with httpx.AsyncClient(
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
            )
        },
        timeout=15.0,
        follow_redirects=True,
    ) as client:
        response = await client.get("https://lite.duckduckgo.com/lite/", params={"q": query})
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    links = soup.select("a.result-link")
    snippets = soup.select("td.result-snippet")

    if not links:
        # Empty parse: could be genuine no-results OR selector drift. The log
        # makes drift diagnosable; the return stays the honest "no results".
        _log.warning(
            "lite_web_search: no result-link anchors parsed for %r "
            "(possible DuckDuckGo HTML selector drift, or a genuine no-results query).",
            query,
        )
        return f"No web results found for: {query}"

    if len(links) != len(snippets):
        _log.warning(
            "lite_web_search: selector count mismatch for %r (%d links vs %d snippets) "
            "— pairing to the shorter length instead of silently misaligning.",
            query,
            len(links),
            len(snippets),
        )

    results = []
    for anchor, snippet in zip(links, snippets, strict=False):
        uddg = parse_qs(urlparse(anchor.get("href", "")).query).get("uddg")
        url = uddg[0] if uddg else anchor.get("href", "")
        results.append(f"{anchor.get_text(strip=True)}\n{url}\n{snippet.get_text(strip=True)}")

    if not results:
        return f"No web results found for: {query}"
    return "\n\n".join(results)
