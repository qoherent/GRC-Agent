"""Regression tests for malformed catalog metadata and error envelopes."""

from pathlib import Path
import stat
import tempfile
import unittest

import yaml

from grc_agent.catalog.describe import _describe_block_with_root
from grc_agent.catalog.loaders import clear_catalog_snapshot_cache


class CatalogErrorEnvelopeTests(unittest.TestCase):
    """Ensure malformed metadata stays inside the public error payload shape."""

    def setUp(self) -> None:
        clear_catalog_snapshot_cache()

    def tearDown(self) -> None:
        clear_catalog_snapshot_cache()

    def test_malformed_block_yaml_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = self._write_catalog_root(
                Path(tmpdir),
                block_text="id: broken\nlabel: Broken\nparameters: [\n",
            )

            result = _describe_block_with_root("test_block", catalog_root=root)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "CatalogLoadError")
        self.assertIn("Could not parse GNU metadata file", result["message"])

    def test_missing_label_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = self._valid_block_payload()
            payload.pop("label")
            root = self._write_catalog_root(Path(tmpdir), block_payload=payload)

            result = _describe_block_with_root("test_block", catalog_root=root)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "CatalogLoadError")
        self.assertIn("label", result["message"])

    def test_invalid_section_shapes_return_structured_errors(self) -> None:
        for field in ("parameters", "inputs", "outputs"):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as tmpdir:
                    payload = self._valid_block_payload()
                    payload[field] = "not-a-list"
                    root = self._write_catalog_root(Path(tmpdir), block_payload=payload)

                    result = _describe_block_with_root("test_block", catalog_root=root)

                self.assertFalse(result["ok"])
                self.assertEqual(result["error_type"], "CatalogLoadError")
                self.assertIn(field, result["message"])

    def test_unreadable_block_file_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = self._write_catalog_root(Path(tmpdir), block_payload=self._valid_block_payload())
            block_path = root / "test_block.block.yml"
            original_mode = block_path.stat().st_mode
            block_path.chmod(0)
            try:
                result = _describe_block_with_root("test_block", catalog_root=root)
            finally:
                block_path.chmod(original_mode | stat.S_IRUSR | stat.S_IWUSR)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "CatalogLoadError")
        self.assertIn("Could not read GNU metadata file", result["message"])

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
