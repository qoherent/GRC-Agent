"""Tests for the user preferences module.

All filesystem-touching tests use ``tmp_path`` per the project
convention in ``tests/conftest.py`` and never touch the developer's
real ``~/.config/grc_agent/``.
"""

from __future__ import annotations

import json
import logging
import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from grc_agent.config import LlamaConfig
from grc_agent.preferences import (
    PREFERENCES_SCHEMA_VERSION,
    PREFS_FILE_NAME,
    LastModel,
    UserPreferences,
    apply_user_preferences_to_llama_config,
    default_user_preferences,
    load_user_preferences,
    save_user_preferences,
    update_last_model,
    user_preferences_path,
)


def _make_llama_config(**overrides: object) -> LlamaConfig:
    """Build a fully-populated LlamaConfig for tests."""
    base = dict(
        server_url="http://127.0.0.1:8080",
        model="default-model.gguf",
        hf_model="default/repo:default-model.gguf",
        model_path=None,
        device="CUDA0",
        gpu_layers=999,
        desired_context_tokens=120000,
        startup_timeout_seconds=300.0,
        max_tokens=4096,
        max_tool_rounds=8,
        temperature=0.0,
        enable_thinking=False,
        request_timeout_seconds=60.0,
        log_retention_days=7,
        models_dir=None,
    )
    base.update(overrides)
    return LlamaConfig(**base)


class DefaultsTests(unittest.TestCase):
    def test_default_preferences_have_expected_values(self) -> None:
        prefs = default_user_preferences()
        self.assertEqual(prefs.last_model.hf_repo, "")
        self.assertEqual(prefs.last_model.filename, "")
        self.assertEqual(prefs.last_model.alias, "")
        self.assertEqual(prefs.last_model.saved_at, "")
        self.assertFalse(prefs.confirm_model_swap)
        self.assertEqual(prefs.schema_version, PREFERENCES_SCHEMA_VERSION)


class PathTests(unittest.TestCase):
    def test_user_preferences_path_lives_next_to_user_config(self) -> None:
        # The path is in the same directory as ``user_config_path``.
        from grc_agent.config import user_config_path

        self.assertEqual(
            user_preferences_path().parent, user_config_path().parent
        )
        self.assertEqual(user_preferences_path().name, PREFS_FILE_NAME)

    def test_user_preferences_path_respects_xdg(self) -> None:
        with mock.patch.dict(
            os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg_test_xyz"}, clear=False
        ):
            path = user_preferences_path()
        self.assertEqual(path, Path("/tmp/xdg_test_xyz") / "grc_agent" / PREFS_FILE_NAME)


class LoadTests(unittest.TestCase):
    def setUp(self) -> None:
        # Make the preferences logger verbose and attach an
        # in-memory handler so we can assert on INFO messages
        # without depending on a root-level handler.
        self._logger = logging.getLogger("grc_agent.preferences")
        self._original_level = self._logger.level
        self._logger.setLevel(logging.DEBUG)
        self._records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.setLevel(logging.DEBUG)
        handler.emit = self._records.append  # type: ignore[method-assign]
        self._handler = handler
        self._logger.addHandler(handler)

    def tearDown(self) -> None:
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._original_level)

    def _has_log_containing(self, substring: str, *, min_level: int = logging.DEBUG) -> bool:
        return any(
            record.levelno >= min_level and substring in record.getMessage()
            for record in self._records
        )

    def test_load_returns_defaults_when_file_missing(self) -> None:
        prefs = load_user_preferences(path=Path("/nonexistent/prefs.json"))
        self.assertEqual(prefs, default_user_preferences())

    def test_load_round_trip(self) -> None:
        with self.subTest("full"):
            target = Path("/tmp/rt_full.json")
            original = UserPreferences(
                last_model=LastModel(
                    hf_repo="org/repo",
                    filename="model.gguf",
                    alias="model",
                    saved_at="2026-01-01T00:00:00Z",
                ),
                confirm_model_swap=True,
            )
            save_user_preferences(original, path=target)
            loaded = load_user_preferences(path=target)
            self.assertEqual(loaded, original)
        with self.subTest("defaults round-trip"):
            target2 = Path("/tmp/rt_default.json")
            save_user_preferences(default_user_preferences(), path=target2)
            loaded2 = load_user_preferences(path=target2)
            self.assertEqual(loaded2, default_user_preferences())

    def test_load_malformed_json_returns_defaults_and_warns(self) -> None:
        with tempfile_Target() as target:
            target.write_text("{not json", encoding="utf-8")
            prefs = load_user_preferences(path=target)
            # The malformed file must be left in place, not deleted.
            self.assertTrue(target.exists())
        self.assertEqual(prefs, default_user_preferences())
        self.assertTrue(
            self._has_log_containing("not valid JSON", min_level=logging.WARNING)
        )

    def test_load_wrong_types_returns_defaults_and_warns(self) -> None:
        with tempfile_Target() as target:
            target.write_text(
                json.dumps({"last_model": "not-a-dict"}), encoding="utf-8"
            )
            prefs = load_user_preferences(path=target)
        self.assertEqual(prefs.last_model, LastModel())
        self.assertTrue(self._has_log_containing("non-dict last_model"))

    def test_load_unknown_schema_version_returns_defaults(self) -> None:
        with tempfile_Target() as target:
            target.write_text(
                json.dumps(
                    {
                        "schema_version": 999,
                        "last_model": {"hf_repo": "o/r", "filename": "f.gguf"},
                    }
                ),
                encoding="utf-8",
            )
            prefs = load_user_preferences(path=target)
        self.assertEqual(prefs, default_user_preferences())
        self.assertTrue(
            self._has_log_containing("schema_version=999", min_level=logging.WARNING)
        )

    def test_load_ignores_unknown_keys(self) -> None:
        with tempfile_Target() as target:
            target.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_model": {"hf_repo": "o/r", "filename": "f.gguf"},
                        "future_flag": True,
                    }
                ),
                encoding="utf-8",
            )
            prefs = load_user_preferences(path=target)
        self.assertEqual(prefs.last_model.hf_repo, "o/r")
        self.assertTrue(self._has_log_containing("future_flag"))

    def test_load_handles_non_bool_confirm_model_swap(self) -> None:
        with tempfile_Target() as target:
            target.write_text(
                json.dumps(
                    {"schema_version": 1, "confirm_model_swap": "yes"}
                ),
                encoding="utf-8",
            )
            prefs = load_user_preferences(path=target)
        self.assertFalse(prefs.confirm_model_swap)
        self.assertTrue(self._has_log_containing("non-bool"))


class SaveTests(unittest.TestCase):
    def test_save_creates_parent_directory(self) -> None:
        # Build a path whose parent does not yet exist.
        with tempfile_Target(suffix="parent_dir_marker") as marker:
            parent = marker.parent / "freshly_created_dir" / "prefs.json"
            self.assertFalse(parent.parent.exists())
            save_user_preferences(
                UserPreferences(
                    last_model=LastModel(hf_repo="o/r", filename="f.gguf")
                ),
                path=parent,
            )
            self.assertTrue(parent.exists())
            self.assertTrue(parent.parent.is_dir())

    def test_save_is_atomic(self) -> None:
        with tempfile_Target(suffix="prefs.json") as target:
            save_user_preferences(
                UserPreferences(
                    last_model=LastModel(hf_repo="o/r", filename="f.gguf")
                ),
                path=target,
            )
            original_text = target.read_text(encoding="utf-8")
            # Inject an os.replace failure; the target file must
            # remain unchanged and the temp file must be cleaned up.
            with mock.patch(
                "os.replace", side_effect=OSError("disk full")
            ) as replace_mock:
                with self.assertRaises(OSError):
                    save_user_preferences(
                        UserPreferences(
                            last_model=LastModel(
                                hf_repo="o/r2", filename="f2.gguf"
                            )
                        ),
                        path=target,
                    )
            replace_mock.assert_called_once()
            # The original file is untouched.
            self.assertEqual(target.read_text(encoding="utf-8"), original_text)

    def test_save_writes_sorted_keys(self) -> None:
        with tempfile_Target(suffix="prefs.json") as target:
            save_user_preferences(
                UserPreferences(
                    last_model=LastModel(
                        hf_repo="zzz", filename="z.gguf", alias="z"
                    ),
                    confirm_model_swap=True,
                ),
                path=target,
            )
            text = target.read_text(encoding="utf-8")
            # json.dumps with sort_keys=True yields a stable,
            # diff-friendly file. Just assert sorted fields.
            self.assertLess(text.index('"confirm_model_swap"'),
                            text.index('"last_model"'))
            self.assertLess(text.index('"alias"'),
                            text.index('"filename"'))
            self.assertLess(text.index('"filename"'),
                            text.index('"hf_repo"'))


class ApplyToLlamaConfigTests(unittest.TestCase):
    def test_empty_prefs_is_noop(self) -> None:
        cfg = _make_llama_config()
        out = apply_user_preferences_to_llama_config(
            cfg, default_user_preferences()
        )
        self.assertEqual(out, cfg)

    def test_populated_prefs_override_model_and_hf_model(self) -> None:
        cfg = _make_llama_config(
            model="old.gguf", hf_model="old/repo:old.gguf"
        )
        out = apply_user_preferences_to_llama_config(
            cfg,
            UserPreferences(
                last_model=LastModel(
                    hf_repo="new/repo",
                    filename="new-model.gguf",
                    alias="new-model",
                )
            ),
        )
        self.assertEqual(out.model, "new-model")
        self.assertEqual(out.hf_model, "new/repo:new-model.gguf")

    def test_apply_does_not_touch_other_fields(self) -> None:
        cfg = _make_llama_config(
            device="Metal",
            gpu_layers=42,
            desired_context_tokens=65536,
            model_path="/tmp/placeholder.gguf",
        )
        out = apply_user_preferences_to_llama_config(
            cfg,
            UserPreferences(
                last_model=LastModel(
                    hf_repo="new/repo",
                    filename="new.gguf",
                    alias="new",
                )
            ),
        )
        self.assertEqual(out.device, "Metal")
        self.assertEqual(out.gpu_layers, 42)
        self.assertEqual(out.desired_context_tokens, 65536)
        # F1: swap persistence must clear model_path, otherwise the
        # launcher's ``-m`` flag would silently revert the swap.
        self.assertIsNone(out.model_path)

    def test_apply_clears_model_path_for_swap_persistence(self) -> None:
        """F1 regression: a swap persisted to prefs must override
        ``[llama].model_path`` from ``grc_agent.toml`` on the next
        launch. The launcher prefers ``-m model_path`` over
        ``-hf hf_model``, so leaving model_path untouched would
        silently revert the swap.
        """
        cfg = _make_llama_config(
            model="old-local.gguf",
            hf_model="old/repo:old-local.gguf",
            model_path="/data/big-local.gguf",
        )
        out = apply_user_preferences_to_llama_config(
            cfg,
            UserPreferences(
                last_model=LastModel(
                    hf_repo="new/repo",
                    filename="new-model.gguf",
                    alias="new-model",
                )
            ),
        )
        self.assertIsNone(out.model_path)
        self.assertEqual(out.model, "new-model")
        self.assertEqual(out.hf_model, "new/repo:new-model.gguf")

    def test_apply_alias_only_overlay(self) -> None:
        """F2 regression: an alias-only prefs file is honored at
        startup, matching the helper's own guard."""
        cfg = _make_llama_config()
        out = apply_user_preferences_to_llama_config(
            cfg,
            UserPreferences(
                last_model=LastModel(alias="my-fine-tune")
            ),
        )
        self.assertEqual(out.model, "my-fine-tune")
        # hf_model is unchanged because we only have an alias.
        self.assertEqual(out.hf_model, cfg.hf_model)


class UpdateLastModelTests(unittest.TestCase):
    def test_update_writes_last_model_and_preserves_other_keys(self) -> None:
        with tempfile_Target(suffix="prefs.json") as target:
            # Seed with a non-default confirm_model_swap.
            save_user_preferences(
                UserPreferences(confirm_model_swap=True), path=target
            )
            update_last_model(
                hf_repo="o/r",
                filename="f.gguf",
                alias="f",
                path=target,
            )
            loaded = load_user_preferences(path=target)
        self.assertEqual(loaded.last_model.hf_repo, "o/r")
        self.assertEqual(loaded.last_model.filename, "f.gguf")
        self.assertEqual(loaded.last_model.alias, "f")
        self.assertTrue(loaded.confirm_model_swap)
        # saved_at is a recent ISO-8601 UTC timestamp.
        parsed = datetime.strptime(
            loaded.last_model.saved_at, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=UTC)
        self.assertLess(
            (datetime.now(UTC) - parsed).total_seconds(), 60
        )


def tempfile_Target(suffix: str = "preferences.json"):
    """Yield a unique path under tmp_path for the duration of a `with`.

    Implemented as a context manager helper so individual tests stay
    readable. Uses ``tempfile.mkstemp`` for the parent dir so tests
    can run in parallel without colliding.
    """
    import tempfile

    class _TargetCM:
        def __enter__(self) -> Path:
            tmpdir = tempfile.mkdtemp(prefix="grc_prefs_test_")
            self._path = Path(tmpdir) / suffix
            return self._path

        def __exit__(self, *args: object) -> None:
            import shutil

            shutil.rmtree(self._path.parent, ignore_errors=True)

    return _TargetCM()


if __name__ == "__main__":
    unittest.main()
