"""Public utility: stable mapping of every on-disk location the package uses.

Lives outside ``cli.py`` so the GUI and any other consumer can import it
without crossing the CLI module's surface area.
"""

from __future__ import annotations

from pathlib import Path


def collect_package_paths() -> dict[str, str]:
    """Return a stable mapping of every on-disk location the package uses."""
    from grc_agent.config import default_config_path, user_config_path
    from grc_agent.history import HISTORY_ENV_VAR, default_history_path
    from grc_agent.preferences import user_preferences_path

    cache_root = Path.home() / ".cache"
    # `default_history_path()` returns a cwd-relative path when no env
    # override is set; resolve it to an absolute path so the output is
    # unambiguous regardless of the current working directory.
    history_path = default_history_path()
    if not history_path.is_absolute():
        history_path = (Path.cwd() / history_path).resolve()
    paths: dict[str, str] = {
        "config_repo": str(default_config_path()),
        "config_user": str(user_config_path()),
        "preferences": str(user_preferences_path()),
        "history": str(history_path),
        "history_env_var": HISTORY_ENV_VAR,
        "sessions_db": str(Path.home() / ".grc_agent" / "sessions.db"),
        "vector_index_default": str(Path.home() / ".grc_agent" / "vector_index"),
        "fastembed_cache": str(cache_root / "fastembed"),
        "grc_agent_state": str(Path.home() / ".grc_agent"),
        "grc_agent_cache": str(cache_root / "grc_agent"),
    }
    return paths


__all__ = ["collect_package_paths"]
