"""Direct tests for validation message helpers."""

import unittest

from grc_agent.validation.messages import (
    format_allowed_values,
    format_catalog_lookup_message,
    format_endpoint,
    format_port_range,
)


class ValidationMessageTests(unittest.TestCase):
    """Exercise the pure formatting helpers directly."""

    def test_format_allowed_values_renders_sorted_text(self) -> None:
        self.assertEqual(
            format_allowed_values(["float", "complex"]),
            "Valid values: float, complex.",
        )

    def test_format_allowed_values_handles_empty_iterable(self) -> None:
        self.assertEqual(format_allowed_values([]), "")

    def test_format_endpoint_uses_block_and_port_shape(self) -> None:
        self.assertEqual(format_endpoint("blocks_throttle2_0", 1), "blocks_throttle2_0(1)")

    def test_format_port_range_handles_empty_single_and_many(self) -> None:
        self.assertEqual(format_port_range(0), "none")
        self.assertEqual(format_port_range(1), "0")
        self.assertEqual(format_port_range(4), "0-3")

    def test_format_catalog_lookup_message_mentions_block_type(self) -> None:
        self.assertEqual(
            format_catalog_lookup_message("analog_agc_xx"),
            "Could not resolve GNU catalog metadata for block type 'analog_agc_xx'.",
        )


if __name__ == "__main__":
    unittest.main()
