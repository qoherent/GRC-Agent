"""ChatGPT Plus/Pro (Codex) provider: OAuth sign-in and the pydantic-ai model.

Import-safe: nothing here opens a browser, reads credentials, or touches the
network until it is called.
"""

from .credentials import clear, is_signed_in
from .model import build_model

__all__ = [
    "build_model",
    "clear",
    "is_signed_in",
]
