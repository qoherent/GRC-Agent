"""Regression tests for malformed catalog metadata raising typed errors.

Exercises the live loading path (``find_block_source`` +
``_build_block_description``) directly — the same path
``runtime/search_blocks.py`` uses — rather than the ``describe_block``/
``_describe_block_with_root`` wrapper, which has no production caller
(it performed an exact single-block-id lookup with a typed error payload;
the live ``query_knowledge`` surface only does fuzzy vector search, which
skips unrenderable hits rather than surfacing a structured error for one).
The malformed-YAML handling these tests protect is still very much live —
it's exercised on every catalog load and vector-index build.
"""

import stat
import tempfile
import unittest
from pathlib import Path

import yaml
from grc_agent.catalog.loaders import CatalogLoadError, _build_block_description, find_block_source


class CatalogErrorEnvelopeTests(unittest.TestCase):
    """Ensure malformed metadata raises the typed catalog error, not a crash."""

    def test_malformed_block_yaml_raises_catalog_load_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = self._write_catalog_root(
                Path(tmpdir),
                block_text="id: broken\nlabel: Broken\nparameters: [\n",
            )

            with self.assertRaises(CatalogLoadError) as ctx:
                find_block_source("test_block", catalog_root=root)
        self.assertIn("Could not parse GNU metadata file", str(ctx.exception))

    def test_missing_label_raises_catalog_load_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = self._valid_block_payload()
            payload.pop("label")
            root = self._write_catalog_root(Path(tmpdir), block_payload=payload)

            raw_block = find_block_source("test_block", catalog_root=root)
            with self.assertRaises(CatalogLoadError) as ctx:
                _build_block_description(raw_block)
        self.assertIn("label", str(ctx.exception))

    def test_invalid_section_shapes_raise_catalog_load_error(self) -> None:
        for field in ("parameters", "inputs", "outputs"):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as tmpdir:
                    payload = self._valid_block_payload()
                    payload[field] = "not-a-list"
                    root = self._write_catalog_root(Path(tmpdir), block_payload=payload)

                    raw_block = find_block_source("test_block", catalog_root=root)
                    with self.assertRaises(CatalogLoadError) as ctx:
                        _build_block_description(raw_block)
                self.assertIn(field, str(ctx.exception))

    def test_unreadable_block_file_raises_catalog_load_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = self._write_catalog_root(Path(tmpdir), block_payload=self._valid_block_payload())
            block_path = root / "test_block.block.yml"
            original_mode = block_path.stat().st_mode
            block_path.chmod(0)
            try:
                with self.assertRaises(CatalogLoadError) as ctx:
                    find_block_source("test_block", catalog_root=root)
            finally:
                block_path.chmod(original_mode | stat.S_IRUSR | stat.S_IWUSR)
        self.assertIn("Could not read GNU metadata file", str(ctx.exception))

    def _write_catalog_root(
        self,
        base: Path,
        *,
        block_payload: dict[str, object] | None = None,
        block_text: str | None = None,
    ) -> Path:
        root = base / "catalog"
        root.mkdir()
        (root / "test.domain.yml").write_text("id: stream\nlabel: Stream\n", encoding="utf-8")
        (root / "test.tree.yml").write_text("'[Core]': ['test_block']\n", encoding="utf-8")
        if block_text is not None:
            (root / "test_block.block.yml").write_text(block_text, encoding="utf-8")
        else:
            payload = block_payload if block_payload is not None else self._valid_block_payload()
            (root / "test_block.block.yml").write_text(
                yaml.safe_dump(payload, sort_keys=False),
                encoding="utf-8",
            )
        return root

    def _valid_block_payload(self) -> dict[str, object]:
        return {
            "id": "test_block",
            "label": "Test Block",
            "parameters": [
                {
                    "id": "gain",
                    "label": "Gain",
                    "dtype": "int",
                    "default": "1",
                }
            ],
            "inputs": [
                {
                    "domain": "stream",
                    "dtype": "float",
                }
            ],
            "outputs": [
                {
                    "domain": "stream",
                    "dtype": "float",
                }
            ],
            "file_format": 1,
        }
