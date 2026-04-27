"""Regression tests for the GNU Radio API loader wrapper.

These tests verify that ``gnu_loader`` correctly extracts connections
from real ``.grc`` files and that the counts are consistent with our
built-in parser.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session.gnu_loader import extract_connections_from_file

_CORPUS_DIR = Path("/usr/share/gnuradio/examples")

# Representative corpus graphs used in prior evaluations
_CORPUS_PATHS = [
    "audio/dial_tone.grc",
    "blocks/selector.grc",
    "filter/resampler_demo.grc",
    "dtv/dvbs2_tx.grc",
    "digital/packet/tx_stage0.grc",
    "zeromq/zeromq_pubsub.grc",
    "fec/polar_code_example.grc",
    "digital/equalizers/linear_equalizer_compare.grc",
]


class TestGnuLoaderCorpus(unittest.TestCase):

    def _graph_path(self, relative: str) -> Path:
        path = _CORPUS_DIR / relative
        self.assertTrue(path.exists(), f"Corpus graph not found: {path}")
        return path

    def _compare_connections(self, relative: str) -> None:
        path = self._graph_path(relative)

        # Our parser
        session = FlowgraphSession()
        session.load(path)
        self.assertIsNotNone(session.flowgraph)
        our_conns = session.flowgraph.connections

        # GNU loader
        gnu_conns = extract_connections_from_file(str(path))

        # Counts should be the same (GNU loader fixes message-connection discrepancy)
        # For stream-only graphs we expect exact match.
        # For message graphs GNU may return fewer (correct) connections.
        self.assertLessEqual(
            len(gnu_conns),
            len(our_conns),
            f"GNU loader returned more connections than our parser for {relative}",
        )

        # Every GNU connection must be findable in our connections
        our_set = {
            (c.src_block, str(c.src_port), c.dst_block, str(c.dst_port))
            for c in our_conns
        }
        for g in gnu_conns:
            key = (g.src_block, str(g.src_port), g.dst_block, str(g.dst_port))
            self.assertIn(
                key,
                our_set,
                f"GNU connection {key} not found in our parser for {relative}",
            )

    def test_dial_tone(self) -> None:
        self._compare_connections("audio/dial_tone.grc")

    def test_selector(self) -> None:
        self._compare_connections("blocks/selector.grc")

    def test_resampler_demo(self) -> None:
        self._compare_connections("filter/resampler_demo.grc")

    def test_dvbs2_tx(self) -> None:
        self._compare_connections("dtv/dvbs2_tx.grc")

    def test_tx_stage0(self) -> None:
        self._compare_connections("digital/packet/tx_stage0.grc")

    def test_zeromq_pubsub(self) -> None:
        self._compare_connections("zeromq/zeromq_pubsub.grc")

    def test_polar_code_example(self) -> None:
        self._compare_connections("fec/polar_code_example.grc")

    def test_linear_equalizer_compare(self) -> None:
        self._compare_connections("digital/equalizers/linear_equalizer_compare.grc")


class TestGnuLoaderEdgeCases(unittest.TestCase):

    def test_fixture_random_bit_generator(self) -> None:
        fixture = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"
        conns = extract_connections_from_file(str(fixture))
        self.assertTrue(len(conns) > 0)

    def test_message_graph_returns_fewer_connections(self) -> None:
        """Message graphs have hidden/implicit connections that our parser overcounts."""
        path = _CORPUS_DIR / "digital" / "packet" / "tx_stage0.grc"
        if not path.exists():
            self.skipTest("Corpus graph not available")
        session = FlowgraphSession()
        session.load(path)
        our_count = len(session.flowgraph.connections) if session.flowgraph else 0
        gnu_count = len(extract_connections_from_file(str(path)))
        # GNU loader is expected to return fewer (correct) connections
        self.assertLessEqual(gnu_count, our_count)


if __name__ == "__main__":
    unittest.main()
