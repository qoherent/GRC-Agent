"""Generic Ollama Cloud chat-completion helper, shared by any experiment under
experiments/. Mirrors the exact endpoint/auth convention web.py already uses
for the ollama_cloud provider (_build_model/grc_health):
https://ollama.com/v1/chat/completions with `Authorization: Bearer <key>`.
"""

import os

import httpx
from dotenv import dotenv_values

from grc_agent.settings import env_path

ENDPOINT = "https://ollama.com/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash:cloud"


def chat_completion(prompt: str, timeout: float = 300.0) -> str:
    """Single-shot, non-streaming chat completion. Raises on any failure —
    no retry, no silent fallback."""
    env = dotenv_values(env_path())
    api_key = env.get("OLLAMA_CLOUD_API_KEY") or os.environ.get("OLLAMA_CLOUD_API_KEY")
    if not api_key:
        raise RuntimeError(
            f"OLLAMA_CLOUD_API_KEY not found in {env_path()} or the environment"
        )
    model = env.get("OLLAMA_CLOUD_MODEL") or os.environ.get("OLLAMA_CLOUD_MODEL", DEFAULT_MODEL)

    response = httpx.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]
