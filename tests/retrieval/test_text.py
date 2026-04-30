"""Tests for retrieval text normalization helpers."""

import unittest

from grc_agent.retrieval.text import expand_terms, normalize_text, tokenize_text


class NormalizeTextTests(unittest.TestCase):
    def test_lowercases_input(self) -> None:
        self.assertEqual(normalize_text("Hello World"), "hello world")

    def test_extracts_alphanumeric_tokens(self) -> None:
        self.assertEqual(normalize_text("analog_agc_xx"), "analog agc xx")

    def test_strips_non_alphanumeric(self) -> None:
        self.assertEqual(normalize_text("foo-bar.baz!qux"), "foo bar baz qux")

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(normalize_text(""), "")

    def test_only_symbols_returns_empty(self) -> None:
        self.assertEqual(normalize_text("---...!!!"), "")

    def test_preserves_digits(self) -> None:
        self.assertEqual(normalize_text("block_v2_0"), "block v2 0")


class TokenizeTextTests(unittest.TestCase):
    def test_splits_into_tuple(self) -> None:
        self.assertEqual(tokenize_text("Hello World"), ("hello", "world"))

    def test_empty_string_returns_empty_tuple(self) -> None:
        self.assertEqual(tokenize_text(""), ())

    def test_single_word(self) -> None:
        self.assertEqual(tokenize_text("hello"), ("hello",))

    def test_extra_whitespace_collapsed(self) -> None:
        self.assertEqual(tokenize_text("  hello   world  "), ("hello", "world"))


class ExpandTermsTests(unittest.TestCase):
    def test_deduplicates_tokens(self) -> None:
        result = expand_terms(["hello", "hello"])
        self.assertIn("hello", result)
        count = result.count("hello")
        self.assertEqual(count, 1)

    def test_joins_adjacent_pairs(self) -> None:
        result = expand_terms(["band", "pass"])
        self.assertIn("bandpass", result)
        self.assertEqual(result, ("band", "pass", "bandpass"))

    def test_single_token_no_join(self) -> None:
        result = expand_terms(["hello"])
        self.assertEqual(result, ("hello",))

    def test_three_tokens_produce_two_bigrams(self) -> None:
        result = expand_terms(["qt", "gui", "sink"])
        self.assertIn("qtgui", result)
        self.assertIn("guisink", result)

    def test_empty_input_returns_empty(self) -> None:
        result = expand_terms([])
        self.assertEqual(result, ())

    def test_filters_empty_strings(self) -> None:
        result = expand_terms(["hello", "", "world"])
        self.assertEqual(result, ("hello", "world", "helloworld"))

    def test_expands_audio_smoother_alias(self) -> None:
        result = expand_terms(["audio", "smoother"])

        self.assertIn("low", result)
        self.assertIn("pass", result)
        self.assertIn("filter", result)

    def test_expands_automatic_gain_control_alias(self) -> None:
        result = expand_terms(["automatic", "gain", "control"])

        self.assertIn("agc", result)

    def test_expands_spectrum_alias(self) -> None:
        result = expand_terms(["spectrum"])

        self.assertIn("frequency", result)
        self.assertIn("waterfall", result)
        self.assertIn("sink", result)

    def test_expands_rate_limiter_alias(self) -> None:
        result = expand_terms(["rate", "limiter"])

        self.assertIn("throttle", result)

    def test_expands_scope_trace_aliases(self) -> None:
        self.assertIn("time", expand_terms(["scope"]))
        self.assertIn("sink", expand_terms(["trace"]))


if __name__ == "__main__":
    unittest.main()
