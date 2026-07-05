"""Tests for runtime/enums.py helpers."""

import unittest

from grc_agent.runtime.enums import SearchDomain


class SearchDomainEnumTests(unittest.TestCase):
    def test_string_values(self) -> None:
        self.assertEqual(SearchDomain.CATALOG.value, "catalog")
        self.assertEqual(SearchDomain.DOCS.value, "docs")


if __name__ == "__main__":
    unittest.main()
