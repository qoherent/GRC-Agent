"""Tests for the ``grc-agent model`` CLI subcommand."""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
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
    """``grc-agent model list`` renders the discovered models."""

    def test_list_with_empty_cache_prints_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "grc_agent.cli.discover_cached_models", return_value=[]
            ):
                args = argparse.Namespace(
                    model_command="list",
                    hf_cache=tmp,
                    models_dir=None,
                    json=False,
                )
                rc, out = _capture_stdout(
                    lambda: _run_model_command(args, default_app_config())
                )
        self.assertEqual(rc, 0)
        self.assertIn("No .gguf files found", out)

    def test_list_with_models_prints_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "hf"
            cache.mkdir()
            (cache / "models--org--repo" / "snapshots" / "v1").mkdir(
                parents=True
            )
            (cache / "models--org--repo" / "refs" / "main").parent.mkdir(
                parents=True
            )
            (cache / "models--org--repo" / "refs" / "main").write_text(
                "v1", encoding="utf-8"
            )
            gguf = (
                cache / "models--org--repo" / "snapshots" / "v1" / "model.gguf"
            )
            gguf.write_bytes(b"\x00" * (3 * 1024 * 1024 + 512 * 1024))
            args = argparse.Namespace(
                model_command="list",
                hf_cache=str(cache),
                models_dir=None,
                json=False,
            )
            rc, out = _capture_stdout(
                lambda: _run_model_command(args, default_app_config())
            )
        self.assertEqual(rc, 0)
        self.assertIn("org/repo:model.gguf", out)
        self.assertIn("3.5 MiB", out)

    def test_list_json_output_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "grc_agent.cli.discover_cached_models", return_value=[]
            ):
                args = argparse.Namespace(
                    model_command="list",
                    hf_cache=tmp,
                    models_dir=None,
                    json=True,
                )
                rc, out = _capture_stdout(
                    lambda: _run_model_command(args, default_app_config())
                )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["models"], [])


class ModelCommandSpecsTests(unittest.TestCase):
    """``grc-agent model specs`` renders GPU/VRAM/RAM/CPU."""

    def test_specs_human_readable(self) -> None:
        fake_specs = mock.MagicMock()
        fake_specs.gpu_name = "Test GPU"
        fake_specs.gpu_vram_bytes = 8 * 1024 * 1024 * 1024
        fake_specs.ram_bytes = 16 * 1024 * 1024 * 1024
        fake_specs.cpu_name = "Test CPU"
        fake_specs.cpu_cores_logical = 8
        with mock.patch(
            "grc_agent.cli.list_system_specs", return_value=fake_specs
        ):
            args = argparse.Namespace(model_command="specs", json=False)
            rc, out = _capture_stdout(
                lambda: _run_model_command(args, default_app_config())
            )
        self.assertEqual(rc, 0)
        self.assertIn("Test GPU", out)
        self.assertIn("8.00 GiB", out)
        self.assertIn("Test CPU", out)

    def test_specs_json_output(self) -> None:
        fake_specs = mock.MagicMock()
        fake_specs.gpu_name = None
        fake_specs.gpu_vram_bytes = None
        fake_specs.ram_bytes = None
        fake_specs.cpu_name = None
        fake_specs.cpu_cores_logical = None
        with mock.patch(
            "grc_agent.cli.list_system_specs", return_value=fake_specs
        ):
            args = argparse.Namespace(model_command="specs", json=True)
            rc, out = _capture_stdout(
                lambda: _run_model_command(args, default_app_config())
            )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertIsNone(payload["specs"]["gpu_name"])


class ModelCommandSwapTests(unittest.TestCase):
    """``grc-agent model swap`` is the live swap path; success returns
    a payload, launcher errors return rc=1."""

    def test_swap_success_returns_payload(self) -> None:
        fake_result = mock.MagicMock()
        fake_result.model_alias = "new-model.gguf"
        fake_result.server_url = "http://127.0.0.1:8080"
        fake_result.status = "started"
        fake_result.health_evidence = {"llama_model_ready": True}
        with mock.patch(
            "grc_agent.llama_launcher.LlamaServerLauncher"
        ) as launcher_cls, mock.patch(
            "grc_agent.preferences.update_last_model"
        ):
            launcher_cls.return_value.swap_model.return_value = fake_result
            args = argparse.Namespace(
                model_command="swap",
                hf_repo="new/repo",
                filename="new-model.gguf",
                alias=None,
                json=True,
            )
            rc, out = _capture_stdout(
                lambda: _run_model_command(args, default_app_config())
            )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["model_alias"], "new-model.gguf")
        self.assertEqual(payload["server_url"], "http://127.0.0.1:8080")
        # The launcher was constructed and swap_model invoked with the
        # right args. The CLI passes ``args.alias`` through verbatim;
        # the launcher substitutes the filename when alias is None.
        launcher_cls.assert_called_once()
        launcher_cls.return_value.swap_model.assert_called_once_with(
            new_hf_repo="new/repo",
            new_filename="new-model.gguf",
            new_alias=None,
        )

    def test_swap_propagates_alias_override(self) -> None:
        fake_result = mock.MagicMock()
        fake_result.model_alias = "custom-alias"
        fake_result.server_url = "http://127.0.0.1:8080"
        fake_result.status = "started"
        fake_result.health_evidence = {}
        with mock.patch(
            "grc_agent.llama_launcher.LlamaServerLauncher"
        ) as launcher_cls, mock.patch(
            "grc_agent.preferences.update_last_model"
        ):
            launcher_cls.return_value.swap_model.return_value = fake_result
            args = argparse.Namespace(
                model_command="swap",
                hf_repo="new/repo",
                filename="new-model.gguf",
                alias="custom-alias",
                json=False,
            )
            rc, out = _capture_stdout(
                lambda: _run_model_command(args, default_app_config())
            )
        self.assertEqual(rc, 0)
        self.assertIn("custom-alias", out)
        launcher_cls.return_value.swap_model.assert_called_once_with(
            new_hf_repo="new/repo",
            new_filename="new-model.gguf",
            new_alias="custom-alias",
        )

    def test_swap_launcher_error_returns_rc_1(self) -> None:
        from grc_agent.llama_launcher import LlamaLauncherError

        with mock.patch(
            "grc_agent.llama_launcher.LlamaServerLauncher"
        ) as launcher_cls, mock.patch(
            "grc_agent.preferences.update_last_model"
        ):
            launcher_cls.return_value.swap_model.side_effect = (
                LlamaLauncherError("server timeout")
            )
            args = argparse.Namespace(
                model_command="swap",
                hf_repo="new/repo",
                filename="new-model.gguf",
                alias=None,
                json=True,
            )
            rc, out = _capture_stdout(
                lambda: _run_model_command(args, default_app_config())
            )
        self.assertEqual(rc, 1)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertIn("server timeout", payload["message"])


if __name__ == "__main__":
    unittest.main()
