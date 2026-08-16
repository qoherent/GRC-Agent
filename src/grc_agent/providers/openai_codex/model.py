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
# Only what that endpoint lists is usable, so the default is its everyday
# coding model — and the Settings dialog lists the account's real options
# rather than making anyone guess an id.
DEFAULT_MODEL = "gpt-5.4"

# Sent as ?client_version= on the models endpoint, which requires it, and
# compared by the server against each model's `minimal_client_version`.
CLIENT_VERSION = "0.104.0"

# Codex rejects `store: true` outright ("Store must be set to false").
CODEX_MODEL_SETTINGS = {"openai_store": False, "openai_text_verbosity": "low"}


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
    Codex and are not selectable, so they are dropped; the rest are ordered by
    the server's own `priority` so the dropdown leads with what OpenAI leads
    with.
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
    listed = [m for m in models if m.get("visibility") != "hide" and m.get("slug")]
    listed.sort(key=lambda m: -(m.get("priority") or 0))
    return [m["slug"] for m in listed]
