"""Direct tests for catalog normalization helpers."""

import unittest
from pathlib import Path

from grc_agent.catalog.loaders import CatalogLoadError
from grc_agent.catalog.schema import (
    normalize_parameter,
    normalize_port,
    split_category_path,
)


class CatalogNormalizeTests(unittest.TestCase):
    """Exercise the pure normalization helpers directly."""

    def test_split_category_path_strips_brackets_and_empty_parts(self) -> None:
        self.assertEqual(
            split_category_path(" /[Core]// Filters / [Analog] / "),
            ["Core", "Filters", "Analog"],
        )

    def test_normalize_parameter_preserves_declared_metadata(self) -> None:
        parameter = normalize_parameter(
            {
                "id": "type",
                "dtype": "enum",
                "default": "float",
                "category": "General",
                "hide": "none",
                "options": ["float", "complex"],
            },
            source_path=Path("/tmp/test.block.yml"),
        )

        self.assertEqual(parameter.id, "type")
        self.assertEqual(parameter.dtype, "enum")
        self.assertEqual(parameter.default, "float")
        self.assertEqual(parameter.category, "General")
        self.assertEqual(parameter.hide, "none")
        self.assertEqual(parameter.options, ["float", "complex"])

    def test_normalize_parameter_requires_non_empty_id(self) -> None:
        with self.assertRaises(CatalogLoadError):
            normalize_parameter({"label": "Missing id"}, source_path=Path("/tmp/test.block.yml"))

    def test_normalize_port_preserves_declared_metadata(self) -> None:
        port = normalize_port(
            {
                "domain": "stream",
                "id": "in",
                "dtype": "complex",
            }
        )

        self.assertEqual(port.domain, "stream")
        self.assertEqual(port.port_id, "in")
        self.assertEqual(port.dtype, "complex")


if __name__ == "__main__":
    unittest.main()
