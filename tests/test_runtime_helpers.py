"""Tests for the runtime/text_utils.py, runtime/enums.py, runtime/integrity.py helpers."""

import unittest

from grc_agent.runtime.enums import SearchDomain
from grc_agent.runtime.integrity import compact_file_integrity
from grc_agent.runtime.text_utils import (
    compact_whitespace,
    format_truncation_flag,
    tokenize_identifier,
)


class FormatTruncationFlagTests(unittest.TestCase):
    def test_default_format(self) -> None:
        flag = format_truncation_flag("block_summary", 1000, 300)
        self.assertEqual(flag, "... [TRUNCATED block_summary: was 1000 chars, kept 300 chars]")

    def test_custom_unit(self) -> None:
        flag = format_truncation_flag("connections", 50, 10, unit="items")
        self.assertEqual(flag, "... [TRUNCATED connections: was 50 items, kept 10 items]")

    def test_flag_is_stable_for_uniqueness(self) -> None:
        # The 'was' and 'kept' values are part of the string so consumers can
        # reconstruct what was dropped without re-asking the producer.
        a = format_truncation_flag("x", 10, 5)
        b = format_truncation_flag("x", 10, 4)
        self.assertNotEqual(a, b)


class TokenizeIdentifierTests(unittest.TestCase):
    def test_canonical_form(self) -> None:
        self.assertEqual(
            tokenize_identifier("Blocks_Throttle X 2"),
            ["blocks", "throttle", "x", "2"],
        )

    def test_empty_after_split(self) -> None:
        self.assertEqual(tokenize_identifier("___"), [])

    def test_unicode_folds_via_casefold(self) -> None:
        # German sharp-s casefolds to "ss" so the tokens reflect that.
        self.assertIn("strasse", tokenize_identifier("Straße"))
        # Turkish dotted I: casefold yields ASCII 'i' for the I; the
        # combining-dot form of the I (U+0130) casefolds to a sequence
        # starting with 'i' and (under some locales) 'nci'. We just
        # assert 'nci' is in the token stream — the exact decomposition
        # is a property of Python's casefold, not our policy.
        self.assertIn("nci", tokenize_identifier("İNCİ"))

    def test_mixed_separators(self) -> None:
        self.assertEqual(
            tokenize_identifier("a-b.c d"),
            ["a", "b", "c", "d"],
        )


class CompactWhitespaceTests(unittest.TestCase):
    def test_collapses_runs(self) -> None:
        self.assertEqual(compact_whitespace("a   b\tc\nd"), "a b c d")

    def test_strips_ends(self) -> None:
        self.assertEqual(compact_whitespace("  hello  "), "hello")

    def test_empty_passthrough(self) -> None:
        self.assertEqual(compact_whitespace(""), "")


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
