"""ChatGPT Plus/Pro (Codex) provider: OAuth sign-in and the pydantic-ai model.

Import-safe: nothing here opens a browser, reads credentials, or touches the
network until it is called.
"""

from .credentials import (
    AuthenticationError,
    CodexError,
    NotAuthenticated,
    clear,
    is_signed_in,
)
from .model import DEFAULT_MODEL, EntitlementError, RateLimitError, build_model

__all__ = [
    "DEFAULT_MODEL",
    "AuthenticationError",
    "CodexError",
    "EntitlementError",
    "NotAuthenticated",
    "RateLimitError",
    "build_model",
    "clear",
    "is_signed_in",
]
