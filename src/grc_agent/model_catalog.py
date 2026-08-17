"""Ask a provider which models it actually serves.

Typing a model id by hand is how you end up sending `gpt-5.1-codex` to an
endpoint that answers *"not supported when using Codex with a ChatGPT
account"* — the id looks plausible, and nothing catches it until the first
message fails. Every backend can be asked instead:

* Ollama          `GET {base}/api/tags`
* OpenAI-shaped   `GET {base}/v1/models`
* ChatGPT/Codex   `GET .../codex/models?client_version=…`

The first two reuse `agent_factory._preflight_target`, which already resolves
each provider's URL and auth headers, so there is one place that knows how to
address a backend rather than two that can disagree.

Async because the ChatGPT path may need to refresh an OAuth token, and because
the caller is a GTK dialog that must not block the UI on a network round trip.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

_log = logging.getLogger(__name__)

_TIMEOUT = 15.0


async def list_models(cfg: dict, api_key: str = "", base_url: str = "") -> list[str]:
    """Model ids the configured provider offers, best-effort sorted.

    Raises RuntimeError with a displayable message when the backend cannot be
    asked — the caller shows that instead of an empty dropdown, so "no models"
    is never confused with "could not reach the backend".
    """
    provider = cfg.get("provider", "ollama")
    if provider == "openai_codex":
        from grc_agent.providers.openai_codex.model import list_models as codex_models

        return await codex_models()
    return await asyncio.to_thread(_list_http_models, provider, api_key, base_url)


def _list_http_models(provider: str, api_key: str, base_url: str) -> list[str]:
    from grc_agent.agent_factory import _preflight_target

    target = _preflight_target(provider, api_key, base_url)
    if isinstance(target, str):
        raise RuntimeError(target)
    url, headers = target
    try:
        r = httpx.get(url, headers=headers, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"could not reach {url}: {exc}") from exc
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} from {url}")
    try:
        data = r.json()
    except ValueError as exc:
        raise RuntimeError(f"{url} did not return JSON") from exc

    # Ollama's /api/tags and the OpenAI /v1/models shape differ; both are
    # keyed off what the response actually contains rather than the provider
    # name, so an OpenAI-compatible Ollama proxy works either way.
    if isinstance(data.get("models"), list):
        names = [m.get("name") or m.get("model") for m in data["models"]]
    else:
        names = [m.get("id") for m in data.get("data", [])]
    return sorted(n for n in names if n)
