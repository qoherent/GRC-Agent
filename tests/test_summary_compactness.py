"""Summary compactness tests — verify max_blocks truncation works as designed.

No model backend required.
"""

from __future__ import annotations

import unittest

from grc_agent.flowgraph_session import DEFAULT_SUMMARY_BLOCK_LIMIT, FlowgraphSession

# Large graph (46 blocks) from Corpus v3
LARGE_GRAPH = "/usr/share/gnuradio/examples/digital/equalizers/linear_equalizer_compare.grc"
# Small graph (8 blocks)
SMALL_GRAPH = "/usr/share/gnuradio/examples/audio/dial_tone.grc"


class TestSummaryCompactness(unittest.TestCase):

    def test_default_max_blocks_caps(self):
        session = FlowgraphSession()
        session.load(LARGE_GRAPH)
        result = session.summary_payload()
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["block_count"], 46)
        self.assertEqual(result["blocks_shown"], DEFAULT_SUMMARY_BLOCK_LIMIT)
        self.assertEqual(
            result["blocks_truncated"],
            46 - DEFAULT_SUMMARY_BLOCK_LIMIT,
        )
        self.assertEqual(len(result["blocks"]), DEFAULT_SUMMARY_BLOCK_LIMIT)

    def test_max_blocks_3(self):
        session = FlowgraphSession()
        session.load(LARGE_GRAPH)
        result = session.summary_payload(max_blocks=3)
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["block_count"], 46)
        self.assertEqual(result["blocks_shown"], 3)
        self.assertEqual(result["blocks_truncated"], 43)
        self.assertEqual(len(result["blocks"]), 3)

    def test_total_block_count_unchanged(self):
        session = FlowgraphSession()
        session.load(LARGE_GRAPH)
        for cap in (3, 8, 100):
            result = session.summary_payload(max_blocks=cap)
            self.assertTrue(result.get("ok"))
            self.assertEqual(result["block_count"], 46)

    def test_connection_count_unchanged(self):
        session = FlowgraphSession()
        session.load(LARGE_GRAPH)
        for cap in (3, 8, 100):
            result = session.summary_payload(max_blocks=cap)
            self.assertTrue(result.get("ok"))
            self.assertEqual(result["connection_count"], 21)

    def test_small_graph_not_truncated(self):
        session = FlowgraphSession()
        session.load(SMALL_GRAPH)
        result = session.summary_payload()
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["block_count"], 8)
        self.assertEqual(result["blocks_shown"], 8)
        self.assertEqual(result["blocks_truncated"], 0)
        self.assertEqual(len(result["blocks"]), 8)

    def test_summary_text_includes_truncate_hint(self):
        session = FlowgraphSession()
        session.load(LARGE_GRAPH)
        result = session.summary_payload(max_blocks=3)
        self.assertTrue(result.get("ok"))
        summary = result["summary"]
        self.assertIn("+43 more", summary)

    def test_summary_text_no_truncate_hint_for_small(self):
        session = FlowgraphSession()
        session.load(SMALL_GRAPH)
        result = session.summary_payload()
        self.assertTrue(result.get("ok"))
        summary = result["summary"]
        self.assertNotIn("more", summary)

    def test_blocks_list_has_name_and_type(self):
        session = FlowgraphSession()
        session.load(SMALL_GRAPH)
        result = session.summary_payload(max_blocks=3)
        block = result["blocks"][0]
        self.assertIn("name", block)
        self.assertIn("type", block)
        self.assertIsInstance(block["name"], str)
        self.assertIsInstance(block["type"], str)

    def test_max_blocks_none_is_invalid(self):
        session = FlowgraphSession()
        session.load(SMALL_GRAPH)
        with self.assertRaises(ValueError):
            session.summary_payload(max_blocks=None)

    def test_max_blocks_negative_is_invalid(self):
        session = FlowgraphSession()
        session.load(SMALL_GRAPH)
        with self.assertRaises(ValueError):
            session.summary_payload(max_blocks=-1)


if __name__ == "__main__":
    unittest.main()
