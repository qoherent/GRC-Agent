"""The ChatGPT (Codex) pydantic-ai model.

Deliberately thin. pydantic-ai's `OpenAIResponsesModel` already implements the
entire Responses mapping, and `OpenAIResponsesModelSettings` already exposes
every field the Codex body needs (`openai_store`, `openai_text_verbosity`,
reasoning effort/summary, extra headers). The OpenAI SDK posts to the literal
path `/responses`, so a base URL of `https://chatgpt.com/backend-api/codex`
yields exactly `POST https://chatgpt.com/backend-api/codex/responses` with no
URL manipulation.

What genuinely has to exist here is only what pydantic-ai cannot know: the
OAuth bearer (refreshed per request), the three Codex-specific headers, and
the error taxonomy for subscription limits. Re-implementing the transport
would mean owning every future change to the Responses API for no benefit.
"""

from __future__ import annotations

import httpx
from openai import APIStatusError, AsyncOpenAI
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from . import credentials
from .credentials import CodexError

BASE_URL = "https://chatgpt.com/backend-api/codex"
ORIGINATOR = "grc-agent"
# Verified against a live ChatGPT account's /codex/models: `gpt-5.1-codex`
# is rejected with "not supported when using Codex with a ChatGPT account".
# Only what that endpoint lists is usable. This default is a starting point,
# not a claim about any particular account: Settings -> Load lists what the
# signed-in account actually offers, which is the reliable way to pick one.
DEFAULT_MODEL = "gpt-5.6-luna"

# Sent as ?client_version= on the models endpoint, which requires it and
# filters the catalog against each model's `minimal_client_version`. Verified
# live: 0.104.0 returned 3 models and silently hid gpt-5.5 (needs 0.124.0) and
# the gpt-5.6 family (needs 0.144.0). The gate describes Codex CLI features —
# apply-patch modes, shell types, websocket transport — that this app does not
# use, since it drives the plain Responses API through pydantic-ai, so asking
# for the unfiltered catalog is correct rather than a version claim. A pinned
# number would silently hide each new model instead.
CLIENT_VERSION = "9999.0.0"

# Codex rejects `store: true` outright ("Store must be set to false") and
# likewise requires `stream: true` ("Stream must be set to true"), which the
# chat sidebar always does.
#
# Every model reports `default_reasoning_summary: "none"`, so without asking
# for a summary the reasoning is computed and then discarded — the sidebar's
# per-turn trace stays empty. Requesting one turns it into ThinkingParts
# (confirmed live: Codex emits `response.reasoning_summary_text.delta`).
CODEX_MODEL_SETTINGS = {
    "openai_store": False,
    "openai_text_verbosity": "low",
    "openai_reasoning_summary": "auto",
    "openai_reasoning_effort": "medium",
}


class EntitlementError(CodexError):
    """The account cannot use Codex — no active Plus/Pro subscription."""


class RateLimitError(CodexError):
    """The subscription's usage limit has been reached."""


class _CodexAuth(httpx.Auth):
    """Attaches the OAuth bearer and account id to every request.

    An `httpx.Auth` rather than a static `api_key`, because the access token
    is short-lived: it has to be re-read (and refreshed if due) per request,
    not captured once when the model is built. This is also the seam that
    avoids reaching into pydantic-ai internals to inject a header.
    """

    requires_response_body = False

    def sync_auth_flow(self, _request):  # pragma: no cover - the agent is async-only
        raise RuntimeError("The ChatGPT provider requires an async client")

    async def async_auth_flow(self, request):
        cred = await credentials.get_valid()
        request.headers["Authorization"] = f"Bearer {cred.access}"
        request.headers["chatgpt-account-id"] = cred.account_id
        yield request


def _translate(exc: APIStatusError) -> Exception:
    """Turn a Codex API failure into something a user can act on.

    Subscription limits are the error users actually hit, and the raw form is
    an opaque status code. The body carries `plan_type` and `resets_at`.
    """
    try:
        error = (exc.response.json() or {}).get("error") or {}
    except Exception:
        error = {}
    code = str(error.get("code") or error.get("type") or "")

    if exc.status_code == 429 or code in (
        "usage_limit_reached",
        "usage_not_included",
        "rate_limit_exceeded",
    ):
        plan = error.get("plan_type")
        detail = f" ({plan} plan)" if plan else ""
        resets_at = error.get("resets_at")
        when = ""
        if isinstance(resets_at, (int, float)):
            import time

            minutes = max(0, int((resets_at - time.time()) // 60))
            when = f" Try again in ~{minutes} min."
        return RateLimitError(f"ChatGPT usage limit reached{detail}.{when}")

    if exc.status_code in (401, 403):
        return EntitlementError(
            "ChatGPT rejected the request. Codex requires an active ChatGPT "
            "Plus or Pro subscription; sign in again from Settings if the "
            "problem persists."
        )
    return exc


class CodexResponsesModel(OpenAIResponsesModel):
    """`OpenAIResponsesModel` with Codex's error taxonomy.

    Everything else — request building, streaming, tool-call round-tripping —
    is inherited unchanged.
    """

    async def request(self, *args, **kwargs):
        try:
            return await super().request(*args, **kwargs)
        except APIStatusError as exc:
            raise _translate(exc) from exc

    def request_stream(self, *args, **kwargs):
        # Returns an async context manager, so the failure surfaces on
        # __aenter__ rather than at call time; wrap that instead of the call.
        outer = super().request_stream(*args, **kwargs)

        class _Wrapped:
            async def __aenter__(self):
                try:
                    return await outer.__aenter__()
                except APIStatusError as exc:
                    raise _translate(exc) from exc

            async def __aexit__(self, *exc_info):
                return await outer.__aexit__(*exc_info)

        return _Wrapped()


def build_model(model_name: str = DEFAULT_MODEL) -> CodexResponsesModel:
    """Construct the model. Does not touch the network or read credentials —
    `_CodexAuth` resolves those per request, so an unauthenticated config
    still builds and fails with a clear message only when actually used."""
    client = AsyncOpenAI(
        base_url=BASE_URL,
        api_key="oauth",  # unused: _CodexAuth sets the Authorization header
        http_client=httpx.AsyncClient(
            auth=_CodexAuth(), timeout=httpx.Timeout(1800.0, connect=10.0)
        ),
        default_headers={
            "originator": ORIGINATOR,
            "OpenAI-Beta": "responses=experimental",
        },
    )
    return CodexResponsesModel(model_name, provider=OpenAIProvider(openai_client=client))


async def list_models() -> list[str]:
    """Model slugs this ChatGPT account can actually use.

    `visibility: "hide"` entries (e.g. `codex-auto-review`) are internal to
    Codex and are not selectable, so they are dropped. The server's own array
    order is kept — it comes back newest-first, which is what a dropdown
    wants. (Its `priority` field ranks the *older* models higher, so sorting
    by it buries the newest ones at the bottom.)
    """
    cred = await credentials.get_valid()
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{BASE_URL}/models",
            params={"client_version": CLIENT_VERSION},
            headers={
                "Authorization": f"Bearer {cred.access}",
                "chatgpt-account-id": cred.account_id,
                "originator": ORIGINATOR,
                "OpenAI-Beta": "responses=experimental",
            },
        )
    if r.status_code >= 400:
        raise RuntimeError(f"ChatGPT model list failed (HTTP {r.status_code})")
    models = r.json().get("models", [])
    return [m["slug"] for m in models if m.get("visibility") != "hide" and m.get("slug")]


def _fetch_models_sync() -> list[dict]:
    """The raw catalog, using a stored credential without refreshing it.

    Sync so the context-length lookup can use it from the GTK main loop, where
    the existing Ollama/OpenAI lookups already make a bounded blocking call.
    Returns [] rather than refreshing when the token is stale — a real request
    refreshes it moments later, and the result is cached by the caller.
    """
    cred = credentials.load()
    if cred is None or cred.expires_soon:
        return []
    r = httpx.get(
        f"{BASE_URL}/models",
        params={"client_version": CLIENT_VERSION},
        headers={
            "Authorization": f"Bearer {cred.access}",
            "chatgpt-account-id": cred.account_id,
            "originator": ORIGINATOR,
            "OpenAI-Beta": "responses=experimental",
        },
        timeout=5.0,
    )
    if r.status_code >= 400:
        return []
    return r.json().get("models", [])


def context_window(slug: str) -> int | None:
    """The model's context window, so the sidebar can show `x / y` rather than
    a bare token count."""
    for m in _fetch_models_sync():
        if m.get("slug") == slug:
            n = m.get("context_window")
            return int(n) if isinstance(n, (int, float)) else None
    return None
