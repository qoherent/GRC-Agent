"""ChatGPT (Codex) OAuth sign-in: PKCE against auth.openai.com.

Subscription access to Codex authenticates with a ChatGPT login rather than
an API key — OpenAI documents this as the subscription auth mode
(https://developers.openai.com/codex/auth). The constants below are the Codex
client's, matching the reference implementation in `pi`.

Importing this module must not open a browser or touch the network; nothing
here runs until `start_login()` is called.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import urllib.parse
from dataclasses import dataclass

import httpx

from .credentials import (
    CLIENT_ID,
    TOKEN_URL,
    AuthenticationError,
    Credential,
    credential_from_token_response,
    save,
)

AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
SCOPE = "openid profile email offline_access"
ORIGINATOR = "grc-agent"

# Fixed, not ephemeral: this exact URI is registered against the client id
# above, and the authorization server rejects anything else. Binding a
# different port produces a redirect_uri mismatch rather than a working login.
# Both loopback families, because the redirect URI says `localhost` and the
# browser picks how to resolve it. On a dual-stack Linux box `localhost`
# resolves to ::1 first, so binding only 127.0.0.1 gets the redirect refused
# and the callback never arrives — the browser lands on the authorization
# server's own "you can return to your app" page and the sign-in silently
# never completes.
CALLBACK_HOSTS = ["127.0.0.1", "::1"]
CALLBACK_PORT = 1455
CALLBACK_PATH = "/auth/callback"
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"

_EXCHANGE_TIMEOUT = 30.0

_SUCCESS_HTML = b"""<!doctype html><meta charset="utf-8"><title>Signed in</title>
<body style="font-family:system-ui;padding:3rem;text-align:center">
<h2>Signed in to ChatGPT</h2><p>You can close this tab and return to GRC Agent.</p></body>"""

_FAILURE_HTML = b"""<!doctype html><meta charset="utf-8"><title>Sign-in failed</title>
<body style="font-family:system-ui;padding:3rem;text-align:center">
<h2>Sign-in failed</h2><p>Return to GRC Agent and try again.</p></body>"""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@dataclass(frozen=True)
class LoginFlow:
    url: str
    verifier: str
    state: str


def start_login() -> LoginFlow:
    """Build the authorization URL and the PKCE material it commits to."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_hex(16)
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": ORIGINATOR,
    }
    return LoginFlow(
        url=f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}",
        verifier=verifier,
        state=state,
    )


def parse_redirect(text: str, expected_state: str) -> str:
    """Recover the authorization code from whatever the user pasted.

    Accepts the full redirect URL, a bare `?code=...&state=...` query string,
    or the code on its own — a headless or remote session cannot receive the
    loopback callback, and telling the user their paste was "invalid" when it
    contained the code is a pointless dead end.
    """
    value = text.strip()
    if not value:
        raise AuthenticationError("Nothing pasted.")
    query = ""
    if "?" in value:
        query = urllib.parse.urlparse(value).query or value.split("?", 1)[1]
    elif "=" in value:
        query = value
    if query:
        params = urllib.parse.parse_qs(query)
        if "error" in params:
            raise AuthenticationError(f"Authorization failed: {params['error'][0]}")
        code = (params.get("code") or [""])[0]
        state = (params.get("state") or [""])[0]
        if state and state != expected_state:
            raise AuthenticationError("State mismatch — start the sign-in again.")
        if code:
            return code
    if " " in value or "/" in value:
        raise AuthenticationError("Could not find an authorization code in that text.")
    return value


async def exchange_code(code: str, verifier: str, redirect_uri: str = REDIRECT_URI) -> Credential:
    """Trade the authorization code for a token pair and persist it."""
    async with httpx.AsyncClient(timeout=_EXCHANGE_TIMEOUT) as client:
        r = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if r.status_code != 200:
        raise AuthenticationError(f"Token exchange failed (HTTP {r.status_code}).")
    cred = credential_from_token_response(r.json())
    save(cred)
    return cred


async def wait_for_callback(flow: LoginFlow, timeout: float = 300.0) -> str:
    """Serve the loopback redirect once and return the authorization code.

    Raises TimeoutError if the browser never comes back, so the caller can
    fall back to asking the user to paste the URL.
    """
    result: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            target = request_line.decode("latin-1").split(" ")[1] if b" " in request_line else ""
            parsed = urllib.parse.urlparse(target)
            ok = parsed.path == CALLBACK_PATH
            body = _SUCCESS_HTML if ok else _FAILURE_HTML
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                b"Content-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
            if ok and not result.done():
                try:
                    result.set_result(parse_redirect(parsed.query, flow.state))
                except AuthenticationError as exc:
                    result.set_exception(exc)
        except Exception:  # pragma: no cover - a malformed request must not hang the flow
            pass
        finally:
            writer.close()

    server = await _listen(handle)
    try:
        return await asyncio.wait_for(result, timeout)
    finally:
        server.close()
        await server.wait_closed()


async def _listen(handle) -> asyncio.AbstractServer:
    """Listen on every loopback family available, not just the first.

    Tries both at once, then falls back to whichever alone can bind — a host
    with IPv6 disabled must still be able to sign in, and so must one where
    `localhost` only resolves to ::1.
    """
    try:
        return await asyncio.start_server(handle, CALLBACK_HOSTS, CALLBACK_PORT)
    except OSError as both_failed:
        for host in CALLBACK_HOSTS:
            try:
                return await asyncio.start_server(handle, host, CALLBACK_PORT)
            except OSError:
                continue
        raise AuthenticationError(
            f"Could not listen on port {CALLBACK_PORT} for the sign-in redirect "
            f"({both_failed}). Close anything else using it — the Codex CLI uses "
            "the same port — then try again."
        ) from both_failed
