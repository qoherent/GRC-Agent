"""Tests for runtime/enums.py and runtime/integrity.py helpers."""

import unittest

from grc_agent.runtime.enums import SearchDomain
from grc_agent.runtime.integrity import compact_file_integrity


class SearchDomainEnumTests(unittest.TestCase):
    def test_string_values(self) -> None:
        self.assertEqual(SearchDomain.CATALOG.value, "catalog")
        self.assertEqual(SearchDomain.DOCS.value, "docs")


class CompactFileIntegrityTests(unittest.TestCase):
    def test_preserves_full_hashes(self) -> None:
        full = "a" * 64
        payload = {
            "persisted_sha256": full,
            "current_sha256": full,
            "status": "clean",
        }
        out = compact_file_integrity(payload)
        self.assertEqual(out["persisted_sha256"], full)
        self.assertEqual(out["current_sha256"], full)
        self.assertEqual(out["status"], "clean")

    def test_silently_drops_unknown_keys(self) -> None:
        # The compactor surfaces a stable whitelist of fields, so any
        # extra keys the producer tacks on are filtered out.
        payload = {"persisted_sha256": "x", "current_sha256": "y", "status": "clean", "extra": "z"}
        out = compact_file_integrity(payload)
        self.assertNotIn("extra", out)

    def test_full_hash_never_clipped(self) -> None:
        # Regression guard: change_graph previously truncated to 12 chars.
        long_hash = "abcdef" * 11  # 66 chars
        out = compact_file_integrity(
            {"persisted_sha256": long_hash, "current_sha256": long_hash, "status": "clean"}
        )
        self.assertEqual(out["persisted_sha256"], long_hash)
        self.assertEqual(out["current_sha256"], long_hash)

    def test_optional_error_field_surfaced(self) -> None:
        out = compact_file_integrity(
            {"status": "modified", "path": "/tmp/x.grc", "error": "changed on disk"}
        )
        self.assertEqual(out["error"], "changed on disk")

    def test_empty_values_dropped(self) -> None:
        out = compact_file_integrity({"status": "", "path": "/tmp/x.grc"})
        self.assertNotIn("status", out)
        self.assertEqual(out["path"], "/tmp/x.grc")


if __name__ == "__main__":
    unittest.main()
