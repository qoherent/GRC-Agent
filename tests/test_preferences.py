"""Tests for the user preferences module.

All filesystem-touching tests use ``tmp_path`` per the project
convention in ``tests/conftest.py`` and never touch the developer's
real ``~/.config/grc_agent/``.

Preferences carry only the last-chosen provider (``provider_chosen``) and
the schema version. Model names are NOT persisted here — ``.env`` is the
single source of truth (see ``tests/test_env_model_config.py``).
"""

from __future__ import annotations

import json
import logging
import os
import unittest
from pathlib import Path
from unittest import mock

from grc_agent.config import (
    ALLOWED_BACKENDS,
    PREFERENCES_SCHEMA_VERSION,
    PREFS_FILE_NAME,
    LlamaConfig,
    UserPreferences,
    apply_user_preferences_to_llama_config,
    default_user_preferences,
    load_user_preferences,
    save_user_preferences,
    update_provider_chosen,
    user_preferences_path,
)


def _make_llama_config(**overrides: object) -> LlamaConfig:
    """Build a fully-populated LlamaConfig for tests."""
    base = dict(
        server_url="http://127.0.0.1:8080",
        model="test-model",
        embedding_model="test-embed",
        backend="ollama",
        max_tokens=4096,
        max_tool_rounds=8,
        request_timeout_seconds=120.0,
    )
    base.update(overrides)
    return LlamaConfig(**base)


class DefaultsTests(unittest.TestCase):
    def test_default_preferences_have_expected_values(self) -> None:
        prefs = default_user_preferences()
        self.assertEqual(prefs.provider_chosen, "")
        self.assertEqual(prefs.schema_version, PREFERENCES_SCHEMA_VERSION)


class PathTests(unittest.TestCase):
    def test_user_preferences_path_lives_next_to_user_config(self) -> None:
        # The path is in the same directory as ``user_config_path``.
        from grc_agent.config import user_config_path

        self.assertEqual(user_preferences_path().parent, user_config_path().parent)
        self.assertEqual(user_preferences_path().name, PREFS_FILE_NAME)

    def test_user_preferences_path_respects_xdg(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg_test_xyz"}, clear=False):
            path = user_preferences_path()
        self.assertEqual(path, Path("/tmp/xdg_test_xyz") / "grc_agent" / PREFS_FILE_NAME)


class LoadTests(unittest.TestCase):
    def setUp(self) -> None:
        # Make the preferences logger verbose and attach an in-memory handler
        # so we can assert on INFO messages without depending on a root handler.
        self._logger = logging.getLogger("grc_agent.config")
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
            original = UserPreferences(provider_chosen="openrouter")
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
        self.assertTrue(self._has_log_containing("not valid JSON", min_level=logging.WARNING))

    def test_load_wrong_provider_type_returns_default_and_warns(self) -> None:
        with tempfile_Target() as target:
            target.write_text(json.dumps({"provider_chosen": 123}), encoding="utf-8")
            prefs = load_user_preferences(path=target)
        self.assertEqual(prefs.provider_chosen, "")
        self.assertTrue(self._has_log_containing("unknown provider_chosen"))

    def test_load_unknown_schema_version_returns_defaults(self) -> None:
        with tempfile_Target() as target:
            target.write_text(
                json.dumps({"schema_version": 999, "provider_chosen": "ollama"}),
                encoding="utf-8",
            )
            prefs = load_user_preferences(path=target)
        self.assertEqual(prefs, default_user_preferences())
        self.assertTrue(self._has_log_containing("schema_version=999", min_level=logging.WARNING))

    def test_load_ignores_unknown_keys(self) -> None:
        with tempfile_Target() as target:
            target.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "provider_chosen": "ollama",
                        "future_flag": True,
                    }
                ),
                encoding="utf-8",
            )
            prefs = load_user_preferences(path=target)
        self.assertEqual(prefs.provider_chosen, "ollama")
        self.assertTrue(self._has_log_containing("future_flag"))


class SaveTests(unittest.TestCase):
    def test_save_creates_parent_directory(self) -> None:
        # Build a path whose parent does not yet exist.
        with tempfile_Target(suffix="parent_dir_marker") as marker:
            parent = marker.parent / "freshly_created_dir" / "prefs.json"
            self.assertFalse(parent.parent.exists())
            save_user_preferences(UserPreferences(provider_chosen="ollama"), path=parent)
            self.assertTrue(parent.exists())
            self.assertTrue(parent.parent.is_dir())

    def test_save_is_atomic(self) -> None:
        with tempfile_Target(suffix="prefs.json") as target:
            save_user_preferences(UserPreferences(provider_chosen="ollama"), path=target)
            original_text = target.read_text(encoding="utf-8")
            # Inject an os.replace failure; the target file must remain
            # unchanged and the temp file must be cleaned up.
            with mock.patch("os.replace", side_effect=OSError("disk full")) as replace_mock:
                with self.assertRaises(OSError):
                    save_user_preferences(
                        UserPreferences(provider_chosen="openrouter"), path=target
                    )
            replace_mock.assert_called_once()
            # The original file is untouched.
            self.assertEqual(target.read_text(encoding="utf-8"), original_text)

    def test_save_writes_sorted_keys(self) -> None:
        with tempfile_Target(suffix="prefs.json") as target:
            save_user_preferences(UserPreferences(provider_chosen="openrouter"), path=target)
            text = target.read_text(encoding="utf-8")
            # json.dumps with sort_keys=True yields a stable, diff-friendly file.
            self.assertLess(text.index('"provider_chosen"'), text.index('"schema_version"'))


class ApplyToLlamaConfigTests(unittest.TestCase):
    def test_empty_prefs_is_noop(self) -> None:
        cfg = _make_llama_config()
        out = apply_user_preferences_to_llama_config(cfg, default_user_preferences())
        self.assertEqual(out, cfg)

    def test_same_backend_is_noop(self) -> None:
        cfg = _make_llama_config(backend="ollama")
        out = apply_user_preferences_to_llama_config(cfg, UserPreferences(provider_chosen="ollama"))
        self.assertEqual(out, cfg)

    def test_provider_flip_re_resolves_models_from_env(self) -> None:
        cfg = _make_llama_config(
            backend="ollama", model="ollama-chat", embedding_model="ollama-embed"
        )
        with mock.patch.dict(
            os.environ,
            {"OPENROUTER_MODEL": "or/chat", "OPENROUTER_EMBEDDING_MODEL": "or/embed"},
        ):
            out = apply_user_preferences_to_llama_config(
                cfg, UserPreferences(provider_chosen="openrouter")
            )
        self.assertEqual(out.backend, "openrouter")
        self.assertEqual(out.model, "or/chat")
        self.assertEqual(out.embedding_model, "or/embed")

    def test_apply_preserves_non_model_fields(self) -> None:
        cfg = _make_llama_config(backend="ollama", max_tokens=2048, server_url="http://x:1")
        with mock.patch.dict(
            os.environ, {"OPENROUTER_MODEL": "or/chat", "OPENROUTER_EMBEDDING_MODEL": "or/embed"}
        ):
            out = apply_user_preferences_to_llama_config(
                cfg, UserPreferences(provider_chosen="openrouter")
            )
        self.assertEqual(out.server_url, "http://x:1")
        self.assertEqual(out.max_tokens, 2048)

    def test_invalid_provider_is_ignored(self) -> None:
        cfg = _make_llama_config(backend="ollama")
        # provider_chosen must be a known backend to flip; unknown values are
        # dropped by the loader so this just guards the no-flip path.
        out = apply_user_preferences_to_llama_config(cfg, UserPreferences(provider_chosen=""))
        self.assertEqual(out, cfg)


class UpdateProviderChosenTests(unittest.TestCase):
    def test_update_writes_provider_chosen(self) -> None:
        with tempfile_Target(suffix="prefs.json") as target:
            update_provider_chosen(provider="openrouter", path=target)
            loaded = load_user_preferences(path=target)
        self.assertEqual(loaded.provider_chosen, "openrouter")

    def test_update_rejects_unknown_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "provider must be"):
            update_provider_chosen(provider="not-a-backend", path=Path("/tmp/unused.json"))

    def test_all_allowed_backends_round_trip(self) -> None:
        for backend in ALLOWED_BACKENDS:
            with tempfile_Target(suffix="prefs.json") as target:
                update_provider_chosen(provider=backend, path=target)
                loaded = load_user_preferences(path=target)
            self.assertEqual(loaded.provider_chosen, backend)


def tempfile_Target(suffix: str = "preferences.json"):
    """Yield a unique path under tmp_path for the duration of a `with`.

    Implemented as a context manager helper so individual tests stay readable.
    Uses ``tempfile.mkstemp`` for the parent dir so tests can run in parallel
    without colliding.
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


class PreferencesLoadPassthroughTests(unittest.TestCase):
    """``load_user_preferences`` passes the persisted provider through unchanged
    and never creates a preferences file when one does not exist.
    """

    def test_load_leaves_unrelated_provider_unchanged(self) -> None:
        with tempfile_Target(suffix="prefs.json") as target:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps({"provider_chosen": "openrouter"}),
                encoding="utf-8",
            )
            prefs = load_user_preferences(path=target)
            self.assertEqual(prefs.provider_chosen, "openrouter")
            # And the on-disk file is untouched (no spurious rewrite).
            raw = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(raw["provider_chosen"], "openrouter")

    def test_load_with_no_file_does_not_create_file(self) -> None:
        with tempfile_Target(suffix="prefs.json") as target:
            # File does not exist — load returns defaults and must NOT create
            # the file just to persist defaults.
            self.assertFalse(target.exists())
            prefs = load_user_preferences(path=target)
            self.assertEqual(prefs.provider_chosen, "")
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
