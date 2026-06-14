"""Direct tests for catalog normalization helpers."""

import unittest
from pathlib import Path

from grc_agent.catalog.loaders import CatalogLoadError
from grc_agent.catalog.schema import (
    build_signature,
    compact_text,
    normalize_parameter,
    normalize_port,
    split_category_path,
)


class CatalogNormalizeTests(unittest.TestCase):
    """Exercise the pure normalization helpers directly."""

    def test_compact_text_flattens_nested_values(self) -> None:
        value = {
            "label": "  AGC\nBlock  ",
            "tags": [" gain ", " control "],
            "hidden": None,
        }

        self.assertEqual(
            compact_text(value),
            "label AGC Block; tags gain, control; hidden",
        )

    def test_split_category_path_strips_brackets_and_empty_parts(self) -> None:
        self.assertEqual(
            split_category_path(" /[Core]// Filters / [Analog] / "),
            ["Core", "Filters", "Analog"],
        )

    def test_normalize_parameter_preserves_declared_metadata(self) -> None:
        parameter = normalize_parameter(
            {
                "id": "type",
                "label": "Type",
                "dtype": "enum",
                "default": "float",
                "category": "General",
                "hide": "none",
                "options": ["float", "complex"],
                "option_labels": ["Float", "Complex"],
                "option_attributes": {"color": ["blue", "red"], "priority": 1},
                "base_key": "type",
            },
            source_path=Path("/tmp/test.block.yml"),
        )

        self.assertEqual(parameter.id, "type")
        self.assertEqual(parameter.dtype, "enum")
        self.assertEqual(parameter.options, ["float", "complex"])
        self.assertEqual(parameter.option_labels, ["Float", "Complex"])
        self.assertEqual(parameter.option_attributes["color"], ["blue", "red"])
        self.assertEqual(parameter.option_attributes["priority"], [1])
        self.assertEqual(parameter.base_key, "type")

    def test_normalize_parameter_requires_non_empty_id(self) -> None:
        with self.assertRaises(CatalogLoadError):
            normalize_parameter({"label": "Missing id"}, source_path=Path("/tmp/test.block.yml"))

    def test_normalize_port_preserves_optional_fields(self) -> None:
        port = normalize_port(
            {
                "label": "in",
                "domain": "stream",
                "id": "in",
                "dtype": "complex",
                "vlen": "2",
                "multiplicity": 4,
                "optional": True,
                "hide": "part",
            }
        )

        self.assertEqual(port.label, "in")
        self.assertEqual(port.domain, "stream")
        self.assertEqual(port.dtype, "complex")
        self.assertEqual(port.vlen, "2")
        self.assertEqual(port.multiplicity, 4)
        self.assertTrue(port.optional)
        self.assertEqual(port.hide, "part")

    def test_build_signature_renders_defaults_and_remaining_count(self) -> None:
        parameters = [
            normalize_parameter(
                {"id": f"param_{index}", "default": index},
                source_path=Path("/tmp/test.block.yml"),
            )
            for index in range(8)
        ]

        signature = build_signature("demo_block", parameters, max_parameters=3)

        self.assertEqual(signature, "demo_block(param_0=0, param_1=1, param_2=2, ... +5 more)")


if __name__ == "__main__":
    unittest.main()
