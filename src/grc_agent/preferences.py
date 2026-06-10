"""User preferences persistence.

A small, fixed-shape JSON file at ``~/.config/grc_agent/preferences.json``
that holds UI/runtime preferences the user has expressed through the
GUI or CLI. Deliberately separate from ``grc_agent.toml``: that file
is the runtime config, hand-edited by power users, and parsed with
strict schema validation. Mixing auto-written UI prefs into a
hand-edited file invites clobber bugs.

The file holds at most a handful of keys. Each
preference is a field on :class:`UserPreferences`. There is no
generic key-value engine; the user said "focus on simple stuff."

Public surface:

- :class:`UserPreferences`, :class:`LastModel` — frozen dataclasses.
- :func:`user_preferences_path` — the on-disk path.
- :func:`load_user_preferences` — never raises; falls back to
  defaults on missing or malformed file.
- :func:`save_user_preferences` — atomic write; may raise OSError
  (caller handles).
- :func:`apply_user_preferences_to_llama_config` — the read-side
  helper that overlays preferences onto a :class:`LlamaConfig`.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


PREFS_FILE_NAME = "preferences.json"
PREFERENCES_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class LastModel:
    """The model the user most recently loaded through the GUI/CLI."""

    model: str = ""
    saved_at: str = ""  # RFC 3339 UTC, informational only


@dataclass(frozen=True)
class UserPreferences:
    """The full set of persisted user preferences."""

    last_model: LastModel = field(default_factory=LastModel)
    provider_chosen: str = ""  # "" | "ollama" | "openrouter"
    schema_version: int = PREFERENCES_SCHEMA_VERSION


def default_user_preferences() -> UserPreferences:
    """Return a fresh defaults instance. Used by first-run and by the
    loader when the file is missing or malformed."""
    return UserPreferences()


def user_preferences_path() -> Path:
    """Return the on-disk path of the preferences file.

    The directory matches :func:`grc_agent.config.user_config_path`
    so users find the file next to the config they already know
    about. XDG-aware: if ``$XDG_CONFIG_HOME`` is set we honour it
    (mirroring the existing pattern in ``config.py``).
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "grc_agent" / PREFS_FILE_NAME
    return Path.home() / ".config" / "grc_agent" / PREFS_FILE_NAME


def _parse_last_model(raw: object) -> LastModel:
    """Build a :class:`LastModel` from a raw JSON value, tolerating
    any junk without raising. Unknown sub-keys are logged and
    dropped; wrong types fall back to ``""``."""
    if not isinstance(raw, dict):
        logger.info(
            "preferences: dropping non-dict last_model=%r", raw
        )
        return LastModel()
    out: dict[str, str] = {}
    for key in ("model", "saved_at"):
        value = raw.get(key)
        if isinstance(value, str):
            out[key] = value
        elif value is not None:
            logger.info(
                "preferences: dropping non-string last_model.%s=%r", key, value
            )
    return LastModel(**out)


def _parse_preferences(raw: object) -> UserPreferences:
    """Build a :class:`UserPreferences` from a raw JSON value."""
    if not isinstance(raw, dict):
        return default_user_preferences()
    last_model = (
        _parse_last_model(raw["last_model"])
        if "last_model" in raw
        else LastModel()
    )
    provider_chosen = ""
    if "provider_chosen" in raw:
        value = raw["provider_chosen"]
        if isinstance(value, str) and value in {"", "ollama", "openrouter"}:
            provider_chosen = value
        else:
            logger.info(
                "preferences: dropping unknown provider_chosen=%r", value
            )
    schema_version = PREFERENCES_SCHEMA_VERSION
    if "schema_version" in raw:
        value = raw["schema_version"]
        if isinstance(value, int) and not isinstance(value, bool):
            schema_version = value
        else:
            logger.info(
                "preferences: dropping non-int schema_version=%r", value
            )
    for unknown_key in raw:
        if unknown_key not in (
            "last_model",
            "provider_chosen",
            "schema_version",
        ):
            logger.info("preferences: ignoring unknown key %r", unknown_key)
    return UserPreferences(
        last_model=last_model,
        provider_chosen=provider_chosen,
        schema_version=schema_version,
    )


def load_user_preferences(path: Path | None = None) -> UserPreferences:
    """Load preferences from disk. Never raises.

    Returns defaults when the file is missing, malformed, or has a
    schema_version the current code does not know how to handle.
    The malformed file is left in place so the user can inspect or
    hand-fix it; the next successful save overwrites it.
    """
    target = (path or user_preferences_path()).expanduser()
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default_user_preferences()
    except OSError as exc:
        logger.warning(
            "preferences: failed to read %s (%s); using defaults", target, exc
        )
        return default_user_preferences()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning(
            "preferences: %s is not valid JSON (%s); using defaults. "
            "The file has been left in place for inspection.",
            target,
            exc,
        )
        return default_user_preferences()
    prefs = _parse_preferences(raw)
    if prefs.schema_version > PREFERENCES_SCHEMA_VERSION:
        logger.warning(
            "preferences: %s has schema_version=%d, this build supports up "
            "to %d. Falling back to defaults. Upgrade GRC Agent or move the "
            "file aside to use the new schema.",
            target,
            prefs.schema_version,
            PREFERENCES_SCHEMA_VERSION,
        )
        return default_user_preferences()
    return prefs


def save_user_preferences(
    prefs: UserPreferences, *, path: Path | None = None
) -> None:
    """Atomically write preferences to disk.

    Writes go through a sibling temp file plus ``os.replace`` so a
    crash mid-write cannot leave a half-written file. Raises
    :class:`OSError` on I/O failure; the caller decides how to
    surface that (the GUI/CLI log a warning and continue).
    """
    target = (path or user_preferences_path()).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(prefs)
    # ``fd, name = tempfile.mkstemp`` is atomic on the same
    # filesystem; cleanup on exception is the caller's problem
    # because the ``with`` block in 3.12+ already closes the fd.
    fd, tmp_name = tempfile.mkstemp(
        prefix=PREFS_FILE_NAME + ".",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    except Exception:
        # Best-effort cleanup of the orphan temp file.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def apply_user_preferences_to_llama_config(llama_config: Any, prefs: UserPreferences) -> Any:
    """Overlay persisted preferences onto a :class:`LlamaConfig`.

    Touches ``model`` (from ``prefs.last_model``) and ``backend``
    (from ``prefs.provider_chosen``). Empty prefs fields are a
    no-op so the input config wins by default.

    This is intentionally a pure function: it does not write to
    disk, and it never raises on missing fields. Callers pass the
    returned config to the launcher.
    """
    from grc_agent.config import LlamaConfig

    if not isinstance(llama_config, LlamaConfig):
        return llama_config
    import dataclasses

    updated = llama_config
    if prefs.last_model.model:
        updated = dataclasses.replace(updated, model=prefs.last_model.model)
    if prefs.provider_chosen in {"ollama", "openrouter"}:
        updated = dataclasses.replace(updated, backend=prefs.provider_chosen)
    return updated


def update_last_model(
    *,
    model: str,
    path: Path | None = None,
) -> None:
    """Convenience: write just the ``last_model`` fields.

    Reads the current preferences, replaces the ``last_model``
    sub-record, sets ``saved_at`` to the current UTC time, and
    writes atomically. Used by the GUI/CLI after a successful
    model swap. Raises :class:`OSError` on I/O failure.
    """
    current = load_user_preferences(path=path)
    saved_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_last = LastModel(
        model=model,
        saved_at=saved_at,
    )
    updated = UserPreferences(
        last_model=new_last,
        provider_chosen=current.provider_chosen,
        schema_version=PREFERENCES_SCHEMA_VERSION,
    )
    save_user_preferences(updated, path=path)


def update_provider_chosen(
    *,
    provider: str,
    path: Path | None = None,
) -> None:
    """Persist the user's provider-picker choice.

    Reads the current preferences, sets ``provider_chosen`` to
    ``"ollama"`` or ``"openrouter"``, and writes atomically. Used
    by the GUI/CLI after the picker accepts. Subsequent launches
    skip the picker because ``prefs.provider_chosen`` is non-empty.
    """
    if provider not in {"ollama", "openrouter"}:
        raise ValueError(
            f"provider must be 'ollama' or 'openrouter'; got {provider!r}"
        )
    current = load_user_preferences(path=path)
    updated = UserPreferences(
        last_model=current.last_model,
        provider_chosen=provider,
        schema_version=PREFERENCES_SCHEMA_VERSION,
    )
    save_user_preferences(updated, path=path)


__all__ = [
    "LastModel",
    "PREFERENCES_SCHEMA_VERSION",
    "PREFS_FILE_NAME",
    "UserPreferences",
    "apply_user_preferences_to_llama_config",
    "default_user_preferences",
    "load_user_preferences",
    "save_user_preferences",
    "update_last_model",
    "update_provider_chosen",
    "user_preferences_path",
]
