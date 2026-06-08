"""Tests for the read-only model discovery and system-spec probes."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from grc_agent.model_manager import (
    CachedModel,
    SystemSpecs,
    cached_model_to_dict,
    discover_cached_models,
    list_system_specs,
    system_specs_to_dict,
)


def _write_fake_hf_cache(
    cache_root: Path,
    *,
    org: str,
    repo: str,
    filename: str,
    contents: bytes = b"\x00" * 16,
    add_download_in_progress: bool = False,
    refs_main_rev: str = "refs-main-v1",
) -> Path:
    """Build a fake HF hub layout under ``cache_root`` and return the gguf path."""
    repo_dir = cache_root / f"models--{org}--{repo}"
    snapshot_dir = repo_dir / "snapshots" / refs_main_rev
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    gguf_path = snapshot_dir / filename
    gguf_path.write_bytes(contents)
    if add_download_in_progress:
        (snapshot_dir / f"{filename}.downloadInProgress").write_text("", encoding="utf-8")
    (repo_dir / "refs" / "main").parent.mkdir(parents=True, exist_ok=True)
    (repo_dir / "refs" / "main").write_text(refs_main_rev, encoding="utf-8")
    return gguf_path


class DiscoverCachedModelsTests(unittest.TestCase):
    """Phase 1 model discovery: filesystem-only, no model downloads."""

    def test_empty_cache_returns_empty_list(self) -> None:
        from grc_agent import model_manager

        with mock.patch.object(model_manager, "_HF_HUB_DEFAULT", Path("/nonexistent")):
            self.assertEqual(discover_cached_models(), [])

    def test_finds_single_gguf_in_hf_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "hf"
            _write_fake_hf_cache(
                cache,
                org="unsloth",
                repo="Qwen3.5-2B-GGUF",
                filename="Qwen3.5-2B-UD-Q4_K_XL.gguf",
                contents=b"\x00" * 4096,
            )
            models = discover_cached_models(hf_cache=cache)
            self.assertEqual(len(models), 1)
            m = models[0]
            self.assertEqual(m.hf_repo, "unsloth/Qwen3.5-2B-GGUF")
            self.assertEqual(m.filename, "Qwen3.5-2B-UD-Q4_K_XL.gguf")
            self.assertEqual(m.hf_model_token, "unsloth/Qwen3.5-2B-GGUF:Qwen3.5-2B-UD-Q4_K_XL.gguf")
            self.assertEqual(m.size_bytes, 4096)
            self.assertIsInstance(m.last_used, datetime)
            self.assertIsNotNone(m.last_used)
            assert m.last_used is not None
            self.assertEqual(m.last_used.tzinfo, UTC)

    def test_skips_in_progress_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "hf"
            _write_fake_hf_cache(
                cache,
                org="unsloth",
                repo="Half-Done-GGUF",
                filename="model.gguf",
                add_download_in_progress=True,
            )
            self.assertEqual(discover_cached_models(hf_cache=cache), [])

    def test_deduplicates_revisions_keeping_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "hf"
            repo_dir = cache / "models--org--repo"
            for rev, contents in (("v1", b"\x00" * 16), ("v2", b"\x00" * 32)):
                snap = repo_dir / "snapshots" / rev
                snap.mkdir(parents=True, exist_ok=True)
                (snap / "file.gguf").write_bytes(contents)
            (repo_dir / "refs" / "main").parent.mkdir(parents=True, exist_ok=True)
            (repo_dir / "refs" / "main").write_text("v2", encoding="utf-8")
            models = discover_cached_models(hf_cache=cache)
            self.assertEqual(len(models), 1)
            self.assertIn(models[0].size_bytes, (16, 32))

    def test_finds_files_in_models_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "hf"
            models_dir = Path(tmp) / "models"
            models_dir.mkdir()
            (models_dir / "local.gguf").write_bytes(b"\x00" * 1024)
            models = discover_cached_models(hf_cache=cache, models_dir=models_dir)
            self.assertEqual(len(models), 1)
            self.assertEqual(models[0].filename, "local.gguf")
            self.assertTrue(models[0].hf_repo.startswith("local/"))

    def test_sorted_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "hf"
            _write_fake_hf_cache(
                cache, org="zzz", repo="z", filename="z.gguf", contents=b"\x00" * 8
            )
            _write_fake_hf_cache(
                cache, org="aaa", repo="a", filename="a.gguf", contents=b"\x00" * 8
            )
            models = discover_cached_models(hf_cache=cache)
            self.assertEqual([m.hf_repo for m in models], ["aaa/a", "zzz/z"])

    def test_handles_unreadable_dir_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "hf"
            cache.mkdir()
            # Create a broken symlink under the cache; discovery must skip
            # it cleanly without raising.
            (cache / "models--org--repo").symlink_to(
                "/nonexistent/path/that/never/exists"
            )
            self.assertEqual(discover_cached_models(hf_cache=cache), [])

    def test_preserves_gguf_filename_for_symlink_blobs(self) -> None:
        """Real HF cache stores files as symlinks to blobs. The original
        filename (on the symlink) must be preserved, not replaced with the
        content-hash blob name.
        """
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "hf"
            repo_dir = cache / "models--org--repo"
            snapshot_dir = repo_dir / "snapshots" / "v1"
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            blob_dir = repo_dir / "blobs"
            blob_dir.mkdir(parents=True, exist_ok=True)
            blob_path = blob_dir / "deadbeef_blob"
            blob_path.write_bytes(b"\x00" * 1024)
            (snapshot_dir / "Qwen3.5-2B-UD-Q4_K_XL.gguf").symlink_to(blob_path)
            (repo_dir / "refs" / "main").parent.mkdir(parents=True, exist_ok=True)
            (repo_dir / "refs" / "main").write_text("v1", encoding="utf-8")

            models = discover_cached_models(hf_cache=cache)
            self.assertEqual(len(models), 1)
            m = models[0]
            self.assertEqual(m.filename, "Qwen3.5-2B-UD-Q4_K_XL.gguf")
            self.assertNotIn("deadbeef", m.filename)
            self.assertEqual(m.size_bytes, 1024)

    def test_filters_mmproj_projection_files(self) -> None:
        """mmproj-* and proj-* projector files must be excluded; they are
        loaded via ``--mmproj`` alongside a vision model, never as a
        chat model. The launcher passes ``--no-mmproj`` and would
        fail to use one as a chat model.
        """
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "hf"
            for filename in (
                "real-chat-model.gguf",
                "mmproj-F16.gguf",
                "mmproj-F32.gguf",
                "proj-F32.gguf",
                "Llama-3.2-90B-Vision-Instruct-mmproj-f16.gguf",
            ):
                _write_fake_hf_cache(
                    cache,
                    org="org",
                    repo="r",
                    filename=filename,
                    contents=b"\x00" * 8,
                )
            models = discover_cached_models(hf_cache=cache)
            filenames = [m.filename for m in models]
            self.assertEqual(filenames, ["real-chat-model.gguf"])


class SystemSpecsTests(unittest.TestCase):
    """Phase 1 system-spec probes."""

    def test_returns_dataclass_with_all_fields(self) -> None:
        specs = list_system_specs()
        self.assertIsInstance(specs, SystemSpecs)
        for field in (
            "gpu_name",
            "gpu_vram_bytes",
            "ram_bytes",
            "cpu_name",
            "cpu_cores_logical",
        ):
            self.assertTrue(hasattr(specs, field))

    def test_handles_nvidia_smi_unavailable(self) -> None:
        with mock.patch(
            "grc_agent.model_manager.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            specs = list_system_specs()
        self.assertIsNone(specs.gpu_name)
        self.assertIsNone(specs.gpu_vram_bytes)

    def test_handles_nvidia_smi_timeout(self) -> None:
        import subprocess

        with mock.patch(
            "grc_agent.model_manager.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=2.0),
        ):
            specs = list_system_specs()
        self.assertIsNone(specs.gpu_name)

    def test_parses_nvidia_smi_csv(self) -> None:
        fake_result = mock.Mock()
        fake_result.returncode = 0
        fake_result.stdout = "GeForce RTX 4090, 24564\n"
        with mock.patch(
            "grc_agent.model_manager.subprocess.run", return_value=fake_result
        ):
            specs = list_system_specs()
        self.assertEqual(specs.gpu_name, "GeForce RTX 4090")
        self.assertEqual(specs.gpu_vram_bytes, 24564 * 1024 * 1024)


class SerializationTests(unittest.TestCase):
    """JSON-serializable views used by the CLI's --json output."""

    def test_cached_model_to_dict_round_trip(self) -> None:
        m = CachedModel(
            hf_repo="org/repo",
            filename="f.gguf",
            snapshot_path=Path("/tmp/f.gguf"),
            size_bytes=2048,
            last_used=datetime(2026, 1, 1, tzinfo=UTC),
        )
        d = cached_model_to_dict(m)
        # Must be JSON-serializable.
        encoded = json.dumps(d, sort_keys=True)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["hf_repo"], "org/repo")
        self.assertEqual(decoded["filename"], "f.gguf")
        self.assertEqual(decoded["size_bytes"], 2048)
        self.assertEqual(decoded["last_used"], "2026-01-01T00:00:00+00:00")

    def test_system_specs_to_dict_handles_none(self) -> None:
        specs = SystemSpecs(
            gpu_name=None,
            gpu_vram_bytes=None,
            ram_bytes=None,
            cpu_name=None,
            cpu_cores_logical=None,
        )
        d = system_specs_to_dict(specs)
        self.assertEqual(
            json.dumps(d, sort_keys=True),
            json.dumps(
                {
                    "cpu_cores_logical": None,
                    "cpu_name": None,
                    "gpu_name": None,
                    "gpu_vram_bytes": None,
                    "ram_bytes": None,
                },
                sort_keys=True,
            ),
        )


if __name__ == "__main__":
    unittest.main()
