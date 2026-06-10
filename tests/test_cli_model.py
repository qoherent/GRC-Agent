"""Tests for the ``grc-agent model`` CLI subcommand."""

from __future__ import annotations

import argparse
import json
import unittest
from unittest import mock

from grc_agent.cli import _run_model_command
from grc_agent.config import default_app_config


def _capture_stdout(func) -> tuple[int, str]:
    """Run ``func`` and return ``(returncode, printed_text)``."""
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = func()
    return rc, buf.getvalue()


class ModelCommandListTests(unittest.TestCase):
    """``grc-agent model list`` renders discovered Ollama models."""

    @mock.patch("grc_agent.model_manager.discover_ollama_models", return_value=["llama3.2", "qwen2.5"])
    def test_list_success_for_ollama(self, mock_discover: mock.MagicMock) -> None:
        cfg = default_app_config()
        import dataclasses
        llama_cfg = dataclasses.replace(cfg.llama, backend="ollama")
        config = dataclasses.replace(cfg, llama=llama_cfg)

        args = argparse.Namespace(
            model_command="list",
            backend=None,
            json=False,
        )
        rc, out = _capture_stdout(
            lambda: _run_model_command(args, config)
        )
        self.assertEqual(rc, 0)
        self.assertIn("Found 2 Ollama model(s)", out)
        self.assertIn("llama3.2", out)
        self.assertIn("qwen2.5", out)
        mock_discover.assert_called_once()

    @mock.patch("grc_agent.model_manager.discover_ollama_models", return_value=[])
    def test_list_empty_cache_prints_hint(self, _mock: mock.MagicMock) -> None:
        cfg = default_app_config()
        import dataclasses
        llama_cfg = dataclasses.replace(cfg.llama, backend="ollama")
        config = dataclasses.replace(cfg, llama=llama_cfg)

        args = argparse.Namespace(
            model_command="list",
            backend=None,
            json=False,
        )
        rc, out = _capture_stdout(
            lambda: _run_model_command(args, config)
        )
        self.assertEqual(rc, 0)
        self.assertIn("No Ollama models found", out)

    @mock.patch("grc_agent.model_manager.discover_ollama_models", return_value=[])
    def test_list_json_output(self, _mock: mock.MagicMock) -> None:
        cfg = default_app_config()
        import dataclasses
        llama_cfg = dataclasses.replace(cfg.llama, backend="ollama")
        config = dataclasses.replace(cfg, llama=llama_cfg)

        args = argparse.Namespace(
            model_command="list",
            backend=None,
            json=True,
        )
        rc, out = _capture_stdout(
            lambda: _run_model_command(args, config)
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["models"], [])

    def test_list_fails_for_openrouter(self) -> None:
        cfg = default_app_config()
        import dataclasses
        llama_cfg = dataclasses.replace(cfg.llama, backend="openrouter")
        config = dataclasses.replace(cfg, llama=llama_cfg)

        args = argparse.Namespace(
            model_command="list",
            backend=None,
            json=True,
        )
        rc, out = _capture_stdout(
            lambda: _run_model_command(args, config)
        )
        self.assertEqual(rc, 1)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertIn("ollama", payload["error"].lower())


class ModelCommandSwapTests(unittest.TestCase):
    """``grc-agent model swap`` switches between ollama and openrouter backends."""

    def test_swap_success_for_ollama(self) -> None:
        cfg = default_app_config()
        import dataclasses
        llama_cfg = dataclasses.replace(cfg.llama, backend="ollama")
        config = dataclasses.replace(cfg, llama=llama_cfg)

        args = argparse.Namespace(
            model_command="swap",
            backend="ollama",
            model="llama3.2",
            json=True,
        )
        with mock.patch("grc_agent.config.resolve_config_path", return_value=None):
            rc, out = _capture_stdout(
                lambda: _run_model_command(args, config)
            )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["model_alias"], "llama3.2")
        self.assertEqual(payload["server_url"], "http://localhost:11434")

    def test_swap_success_for_openrouter(self) -> None:
        cfg = default_app_config()
        import os
        with mock.patch.dict(os.environ, {"OPENROUTER_MODEL": "deepseek/deepseek-v4-flash"}):
            args = argparse.Namespace(
                model_command="swap",
                backend="openrouter",
                model=None,
                json=True,
            )
            with mock.patch("grc_agent.config.resolve_config_path", return_value=None):
                rc, out = _capture_stdout(
                    lambda: _run_model_command(args, cfg)
                )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["model_alias"], "deepseek/deepseek-v4-flash")
        self.assertEqual(payload["server_url"], "https://openrouter.ai/api")

    def test_swap_unsupported_backend(self) -> None:
        cfg = default_app_config()
        args = argparse.Namespace(
            model_command="swap",
            backend="unsupported",
            model=None,
            json=True,
        )
        with mock.patch("grc_agent.config.resolve_config_path", return_value=None):
            rc, out = _capture_stdout(
                lambda: _run_model_command(args, cfg)
            )
        self.assertEqual(rc, 1)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
