"""
Recursively finds and removes cache directories under the project root.
Targets common Python/tool cache dirs: .mypy_cache, .ruff_cache, .pytest_cache,
__pycache__, .cache, .eggs, *.egg-info, .tox, .mypy_cache, node_modules (if present).
"""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CACHE_DIRS = {
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "__pycache__",
    ".cache",
    ".eggs",
    ".tox",
    "node_modules",
}

CACHE_FILE_SUFFIXES = {".pyc", ".pyo", ".egg-info"}


def _is_cache_entry(entry: Path) -> bool:
    return (
        entry.name in CACHE_DIRS
        or entry.suffix in CACHE_FILE_SUFFIXES
        or entry.name.endswith(".egg-info")
    )


def main() -> None:
    removed_dirs = 0
    removed_files = 0

    for entry in sorted(ROOT.rglob("*")):
        if not _is_cache_entry(entry):
            continue

        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
                removed_dirs += 1
                print(f"  [dir]  {entry.relative_to(ROOT)}")
            elif entry.is_file():
                entry.unlink()
                removed_files += 1
                print(f"  [file] {entry.relative_to(ROOT)}")
        except PermissionError:
            print(f"  [skip] {entry.relative_to(ROOT)} (PermissionError)")

    print(f"\nRemoved {removed_dirs} directories and {removed_files} files.")


if __name__ == "__main__":
    main()
