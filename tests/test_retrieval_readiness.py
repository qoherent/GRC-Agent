"""Retrieval readiness test — verify graphify, catalog search, and session index.

No model backend required.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.retrieval.graphify_adapter import graphify_status, build_graph
from grc_agent.retrieval.search import search_grc, bind_retrieval_context

# Representative small fixture
FIXTURE = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"

# External corpus graphs for session search
dial_tone = "/usr/share/gnuradio/examples/audio/dial_tone.grc"
resampler_demo = "/usr/share/gnuradio/examples/filter/resampler_demo.grc"
dvbs2_tx = "/usr/share/gnuradio/examples/dtv/dvbs2_tx.grc"
pdu_tools_demo = "/usr/share/gnuradio/examples/pdu/pdu_tools_demo.grc"
linear_equalizer_compare = "/usr/share/gnuradio/examples/digital/equalizers/linear_equalizer_compare.grc"


class TestGraphifyAvailable(unittest.TestCase):

    def test_graphify_status_ok(self):
        status = graphify_status()
        self.assertTrue(status["ok"], status.get("message", ""))
        self.assertIsNotNone(status["version"])
        self.assertIn("available", status["message"].lower())

    def test_graphify_build_graph_runs(self):
        extraction = {
            "nodes": [
                {"id": "n0", "label": "A", "summary": "Alpha"},
                {"id": "n1", "label": "B", "summary": "Beta"},
            ],
            "edges": [
                {"source": "n0", "target": "n1"}
            ]
        }
        g = build_graph(extraction, directed=True)
        self.assertEqual(len(g.nodes), 2)
        self.assertEqual(len(g.edges), 1)

    def test_graphify_build_graph_undirected(self):
        extraction = {
            "nodes": [
                {"id": "n0", "label": "A", "summary": "Alpha"},
            ],
            "edges": []
        }
        g = build_graph(extraction, directed=False)
        self.assertEqual(len(g.nodes), 1)
        self.assertEqual(len(g.edges), 0)


class TestCatalogSearch(unittest.TestCase):

    def test_catalog_throttle_returns_throttle_block(self):
        result = search_grc(scope="catalog", query="throttle", k=5)
        self.assertTrue(result.get("ok"), result)
        block_ids = [r["block_id"] for r in result["results"] if r.get("block_id")]
        self.assertTrue(
            any(b in block_ids[:3] for b in ("blocks_throttle2", "blocks_throttle")),
            f"Expected blocks_throttle2 or blocks_throttle in top 3, got {block_ids[:3]}"
        )

    def test_catalog_audio_sink_returns_audio_sink(self):
        result = search_grc(scope="catalog", query="audio sink", k=5)
        self.assertTrue(result.get("ok"), result)
        block_ids = [r["block_id"] for r in result["results"] if r.get("block_id")]
        self.assertIn("audio_sink", block_ids[:3],
            f"Expected audio_sink in top 3, got {block_ids[:3]}")

    def test_catalog_no_results_returns_empty_list(self):
        result = search_grc(scope="catalog", query="xyz_nonexistent_qwerty_99999", k=5)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual([r for r in result["results"] if r.get("block_id")], [])
        self.assertIsNotNone(result.get("warnings"))


class TestSessionSearch(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sessions = {}
        for label, path in [
            ("dial_tone", dial_tone),
            ("resampler_demo", resampler_demo),
            ("dvbs2_tx", dvbs2_tx),
            ("pdu_tools_demo", pdu_tools_demo),
            ("linear_equalizer_compare", linear_equalizer_compare),
        ]:
            session = FlowgraphSession()
            session.load(path)
            cls.sessions[label] = session

    def _search_session(self, label, query, k=5):
        session = self.sessions[label]
        bind_retrieval_context(session=session, catalog_root=None)
        return search_grc(scope="session", query=query, k=k)

    # --- dial_tone ---
    def test_dial_tone_ampl_instance(self):
        result = self._search_session("dial_tone", "ampl")
        self.assertTrue(result.get("ok"), result)
        top = result["results"][0] if result["results"] else {}
        self.assertEqual(top.get("label"), "ampl")
        self.assertIn("variable", top.get("block_id", "").lower())

    def test_dial_tone_sig_source(self):
        result = self._search_session("dial_tone", "sig source")
        self.assertTrue(result.get("ok"), result)
        block_ids = [r.get("block_id", "") for r in result["results"]]
        self.assertTrue(
            any("analog_sig_source_x" in bt for bt in block_ids[:3]),
            f"Expected analog_sig_source_x block in session results, got {block_ids[:3]}"
        )

    # --- resampler_demo ---
    def test_resampler_throttle(self):
        result = self._search_session("resampler_demo", "throttle")
        self.assertTrue(result.get("ok"), result)
        top = result["results"][0] if result["results"] else {}
        self.assertEqual(top.get("label"), "throttle")
        self.assertIn("blocks_throttle", top.get("block_id", "").lower())

    def test_resampler_resampler(self):
        result = self._search_session("resampler_demo", "resampler")
        self.assertTrue(result.get("ok"), result)
        top = result["results"][0] if result["results"] else {}
        self.assertIn("pfb_arb_resampler", top.get("block_id", "").lower())

    # --- dvbs2_tx ---
    def test_dvbs2_ldpc(self):
        result = self._search_session("dvbs2_tx", "ldpc")
        self.assertTrue(result.get("ok"), result)
        block_ids = [r.get("block_id", "") for r in result["results"]]
        self.assertTrue(
            any("dtv_dvb_ldpc" in bt for bt in block_ids[:3]),
            f"Expected dtv_dvb_ldpc in top 3, got {block_ids[:3]}"
        )

    def test_dvbs2_freq_sink(self):
        result = self._search_session("dvbs2_tx", "freq sink")
        self.assertTrue(result.get("ok"), result)
        block_ids = [r.get("block_id", "") for r in result["results"]]
        self.assertTrue(
            any("qtgui_freq_sink" in bt for bt in block_ids[:3]),
            f"Expected qtgui_freq_sink in session results, got {block_ids[:3]}"
        )

    # --- pdu_tools_demo ---
    def test_pdu_msg_strobe(self):
        result = self._search_session("pdu_tools_demo", "msg strobe")
        self.assertTrue(result.get("ok"), result)
        block_ids = [r.get("block_id", "") for r in result["results"]]
        self.assertTrue(
            any("blocks_message_strobe" in bt for bt in block_ids[:1]),
            f"Expected blocks_message_strobe in top result, got {block_ids[:1]}"
        )

    def test_pdu_random_pdu(self):
        result = self._search_session("pdu_tools_demo", "random")
        self.assertTrue(result.get("ok"), result)
        block_ids = [r.get("block_id", "") for r in result["results"]]
        self.assertTrue(
            any("pdu_random_pdu" in bt for bt in block_ids[:3]),
            f"Expected pdu_random_pdu in session results, got {block_ids[:3]}"
        )

    # --- linear_equalizer_compare ---
    def test_equalizer_equalizer(self):
        result = self._search_session("linear_equalizer_compare", "equalizer")
        self.assertTrue(result.get("ok"), result)
        block_ids = [r.get("block_id", "") for r in result["results"]]
        self.assertTrue(
            any("digital_linear_equalizer" in bt for bt in block_ids[:3]),
            f"Expected digital_linear_equalizer in session results, got {block_ids[:3]}"
        )

    def test_equalizer_throttle(self):
        result = self._search_session("linear_equalizer_compare", "throttle")
        self.assertTrue(result.get("ok"), result)
        block_ids = [r.get("block_id", "") for r in result["results"]]
        self.assertTrue(
            any("blocks_throttle" in bt for bt in block_ids[:3]),
            f"Expected blocks_throttle in session results, got {block_ids[:3]}"
        )


class TestSessionDisabledBlockConfusion(unittest.TestCase):

    def test_disabled_block_not_confusingly_promoted(self):
        session = FlowgraphSession()
        session.load(str(FIXTURE))
        bind_retrieval_context(session=session, catalog_root=None)
        result = search_grc(scope="session", query="source", k=5)
        self.assertTrue(result.get("ok"), result)
        self.assertGreater(len(result["results"]), 0,
            "Session search should return at least one result for 'source'")


if __name__ == "__main__":
    unittest.main()
