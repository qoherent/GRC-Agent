"""On-disk credential store for the ChatGPT (Codex) OAuth provider.

Deliberately **not** in `.env`. Everything else the app persists is a
preference; these are a rotating access/refresh token pair. `.env` is
world-readable by default, lives in the repo root for a dev checkout (one
`git add -A` away from being committed), and `python-dotenv` has no notion of
file modes. This writes 0600 into a 0700 directory instead, and never logs a
token, an authorization code, or an Authorization header.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
TOKEN_URL = "https://auth.openai.com/oauth/token"

# The claim OpenAI namespaces its own auth data under inside the access token.
JWT_CLAIM_PATH = "https://api.openai.com/auth"

# Refresh this long before actual expiry, so a request is never sent with a
# token that expires mid-flight.
_MIN_VALIDITY_SECONDS = 5 * 60
_REFRESH_TIMEOUT = 15.0

_lock = asyncio.Lock()


class CodexError(RuntimeError):
    """Base for every ChatGPT-provider failure."""


class NotAuthenticated(CodexError):
    """No stored credential — the user has not signed in."""


class AuthenticationError(CodexError):
    """Sign-in or refresh was rejected; the user must sign in again."""


@dataclass(frozen=True)
class Credential:
    access: str
    refresh: str
    expires: float  # absolute unix seconds
    account_id: str

    @property
    def expires_soon(self) -> bool:
        return time.time() + _MIN_VALIDITY_SECONDS >= self.expires


def auth_path() -> Path:
    """Where the token pair lives.

    Fixed under ~/.config rather than following `env_path()`: that resolves to
    the repo root for a dev checkout, and tokens must never land inside a
    working tree.
    """
    override = os.environ.get("GRC_AGENT_CODEX_AUTH")
    if override:
        return Path(override)
    return Path.home() / ".config" / "grc_agent" / "openai-codex-auth.json"


def _decode_jwt_claims(token: str) -> dict:
    """Read an access token's payload. Claims only — the signature is not
    verified, and nothing security-relevant is decided from this. It is used
    solely to recover the account id the Codex endpoint requires as a header,
    which OpenAI returns nowhere else."""
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)  # JWT strips base64 padding
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, binascii.Error):
        return {}


def account_id_from(access_token: str) -> str:
    account_id = _decode_jwt_claims(access_token).get(JWT_CLAIM_PATH, {}).get("chatgpt_account_id")
    if not isinstance(account_id, str) or not account_id:
        raise AuthenticationError(
            "Signed in, but the account has no ChatGPT Codex entitlement "
            "(no chatgpt_account_id in the token). Codex needs an active "
            "ChatGPT Plus or Pro subscription."
        )
    return account_id


def credential_from_token_response(payload: dict) -> Credential:
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    if not access or not refresh or not isinstance(expires_in, (int, float)):
        raise AuthenticationError(
            "Token response was missing access_token/refresh_token/expires_in"
        )
    return Credential(
        access=access,
        refresh=refresh,
        expires=time.time() + float(expires_in),
        account_id=account_id_from(access),
    )


def load() -> Credential | None:
    try:
        raw = json.loads(auth_path().read_text())
    except (OSError, ValueError):
        return None
    try:
        return Credential(
            access=raw["access"],
            refresh=raw["refresh"],
            expires=float(raw["expires"]),
            account_id=raw["account_id"],
        )
    except (KeyError, TypeError, ValueError):
        return None


def save(cred: Credential) -> None:
    path = auth_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    body = json.dumps(
        {
            "type": "oauth",
            "access": cred.access,
            "refresh": cred.refresh,
            "expires": cred.expires,
            "account_id": cred.account_id,
        }
    )
    # Written via a temp file in the same directory then renamed, so a crash
    # mid-write cannot leave a truncated credential that reads as corrupt.
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(body)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(path)


def clear() -> None:
    auth_path().unlink(missing_ok=True)


def is_signed_in() -> bool:
    return load() is not None


async def _refresh(cred: Credential) -> Credential:
    async with httpx.AsyncClient(timeout=_REFRESH_TIMEOUT) as client:
        r = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": cred.refresh,
                "client_id": CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if r.status_code != 200:
        raise AuthenticationError(
            f"Could not refresh the ChatGPT session (HTTP {r.status_code}). Sign in again."
        )
    return credential_from_token_response(r.json())


async def get_valid() -> Credential:
    """The current credential, refreshed if it is close to expiry.

    Double-checked under a lock: several tool calls in one turn can hit an
    expiring token at once, and refreshing twice would burn the rotated
    refresh token from the first refresh, logging the user out.
    """
    cred = load()
    if cred is None:
        raise NotAuthenticated("Not signed in to ChatGPT. Sign in from Settings.")
    if not cred.expires_soon:
        return cred

    async with _lock:
        cred = load()
        if cred is None:
            raise NotAuthenticated("Not signed in to ChatGPT. Sign in from Settings.")
        if not cred.expires_soon:
            return cred
        refreshed = await _refresh(cred)
        save(refreshed)
        return refreshed
