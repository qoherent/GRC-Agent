



def lite_web_search(query: str) -> str:
    """Local web-search fallback for pydantic-ai's provider-adaptive
    ``WebSearch`` capability on providers without native search (Ollama).

    DuckDuckGo's primary endpoint silently empties responses for the standard
    ``ddgs``/``duckduckgo_search`` client (and for pydantic-ai's own built-in
    duckduckgo fallback, which calls the same ``DDGS().text()``), returning zero
    results for every query including controls. ``lite.duckduckgo.com`` is the
    one DuckDuckGo surface that still returns real results, so this scrapes it.

    Raw snippets are returned verbatim for the model to ground on directly — no
    secondary synthesis call, no clipping. Network errors propagate so a backend
    failure surfaces honestly instead of being masked as "no results" (the exact
    bug that made the previous ``web_search`` silently return nothing).
    """
    from urllib.parse import parse_qs, urlparse

    import httpx
    from bs4 import BeautifulSoup

    response = httpx.get(
        "https://lite.duckduckgo.com/lite/",
        params={"q": query},
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
            )
        },
        timeout=15.0,
        follow_redirects=True,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    links = soup.select("a.result-link")
    snippets = soup.select("td.result-snippet")

    results = []
    for anchor, snippet in zip(links, snippets, strict=False):
        uddg = parse_qs(urlparse(anchor.get("href", "")).query).get("uddg")
        url = uddg[0] if uddg else anchor.get("href", "")
        results.append(f"{anchor.get_text(strip=True)}\n{url}\n{snippet.get_text(strip=True)}")

    if not results:
        return f"No web results found for: {query}"
    return "\n\n".join(results)
