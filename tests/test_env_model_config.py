"""Tests for the .env-sourced model-name API.

``.env`` is the single source of truth for chat and embedding model names,
keyed by backend. These tests lock in the resolver contract and the
bidirectional ``set_env_model`` write path used by the GUI.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from grc_agent.config import (
    _DEFAULT_OLLAMA_EMBEDDING_MODEL,
    _DEFAULT_OLLAMA_MODEL,
    _DEFAULT_OPENROUTER_EMBEDDING_MODEL,
    _DEFAULT_OPENROUTER_MODEL,
    default_chat_model,
    default_embedding_model,
    default_ollama_embedding_model,
    default_ollama_model,
    default_openrouter_embedding_model,
    default_openrouter_model,
    set_env_model,
)


class ResolverTests(unittest.TestCase):
    def test_chat_model_per_backend_from_env(self) -> None:
        env = {"OLLAMA_MODEL": "ollama-chat", "OPENROUTER_MODEL": "or/chat"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(default_chat_model("ollama"), "ollama-chat")
            self.assertEqual(default_chat_model("openrouter"), "or/chat")

    def test_embedding_model_per_backend_from_env(self) -> None:
        env = {
            "OLLAMA_EMBEDDING_MODEL": "ollama-embed",
            "OPENROUTER_EMBEDDING_MODEL": "or/embed",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(default_embedding_model("ollama"), "ollama-embed")
            self.assertEqual(default_embedding_model("openrouter"), "or/embed")

    def test_present_but_empty_env_falls_back(self) -> None:
        # ``or`` semantics: a present-but-empty OLLAMA_MODEL= falls back to the
        # literal default instead of resolving to "".
        with mock.patch.dict(os.environ, {"OLLAMA_MODEL": ""}, clear=False):
            self.assertEqual(default_chat_model("ollama"), _DEFAULT_OLLAMA_MODEL)

    def test_literal_fallbacks_when_unset(self) -> None:
        env = {
            "OLLAMA_MODEL": "",
            "OPENROUTER_MODEL": "",
            "OLLAMA_EMBEDDING_MODEL": "",
            "OPENROUTER_EMBEDDING_MODEL": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(default_chat_model("ollama"), _DEFAULT_OLLAMA_MODEL)
            self.assertEqual(default_chat_model("openrouter"), _DEFAULT_OPENROUTER_MODEL)
            self.assertEqual(default_embedding_model("ollama"), _DEFAULT_OLLAMA_EMBEDDING_MODEL)
            self.assertEqual(
                default_embedding_model("openrouter"), _DEFAULT_OPENROUTER_EMBEDDING_MODEL
            )

    def test_convenience_accessors_delegate(self) -> None:
        env = {
            "OLLAMA_MODEL": "oc",
            "OPENROUTER_MODEL": "orc",
            "OLLAMA_EMBEDDING_MODEL": "oe",
            "OPENROUTER_EMBEDDING_MODEL": "ore",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(default_ollama_model(), "oc")
            self.assertEqual(default_openrouter_model(), "orc")
            self.assertEqual(default_ollama_embedding_model(), "oe")
            self.assertEqual(default_openrouter_embedding_model(), "ore")

    def test_openrouter_embedding_default_is_perplexity_pplx(self) -> None:
        # Locked default per the packaging/embedding design.
        self.assertEqual(_DEFAULT_OPENROUTER_EMBEDDING_MODEL, "perplexity/pplx-embed-v1-0.6b")


class SetEnvModelTests(unittest.TestCase):
    def test_set_env_model_writes_file_and_environ(self) -> None:
        import dotenv

        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("OPENROUTER_MODEL=old\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                returned = set_env_model("OPENROUTER_MODEL", "new-model", env_path=env_path)
                self.assertEqual(returned, env_path)
                # os.environ updated in-process immediately.
                self.assertEqual(os.environ["OPENROUTER_MODEL"], "new-model")
            # .env file updated on disk and round-trips through dotenv.
            cfg = dict(dotenv.dotenv_values(env_path))
            self.assertEqual(cfg.get("OPENROUTER_MODEL"), "new-model")

    def test_set_env_model_creates_file_when_absent(self) -> None:
        import dotenv

        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "sub" / ".env"
            self.assertFalse(env_path.exists())
            set_env_model("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text", env_path=env_path)
            self.assertTrue(env_path.is_file())
            cfg = dict(dotenv.dotenv_values(env_path))
            self.assertEqual(cfg.get("OLLAMA_EMBEDDING_MODEL"), "nomic-embed-text")


if __name__ == "__main__":
    unittest.main()
