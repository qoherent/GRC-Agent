"""Direct tests for retrieval provenance helpers."""

from pathlib import Path
import unittest

from grc_agent.retrieval.provenance import (
    Provenance,
    catalog_provenance,
    session_provenance,
)


class ProvenanceTests(unittest.TestCase):
    """Exercise the provenance dataclass and factory helpers directly."""

    def test_provenance_to_dict_returns_serializable_shape(self) -> None:
        provenance = Provenance(
            kind="catalog_block",
            path="/tmp/analog.block.yml",
            pointer="blocks[analog_agc_xx]",
        )

        self.assertEqual(
            provenance.to_dict(),
            {
                "kind": "catalog_block",
                "path": "/tmp/analog.block.yml",
                "pointer": "blocks[analog_agc_xx]",
            },
        )

    def test_catalog_provenance_preserves_real_path_text(self) -> None:
        provenance = catalog_provenance(
            Path("/usr/share/gnuradio/grc/blocks/analog_agc_xx.block.yml"),
            "blocks[analog_agc_xx]",
            kind="catalog_block",
        )

        self.assertEqual(provenance.kind, "catalog_block")
        self.assertTrue(provenance.path.endswith("analog_agc_xx.block.yml"))
        self.assertEqual(provenance.pointer, "blocks[analog_agc_xx]")

    def test_session_provenance_handles_missing_session_path(self) -> None:
        provenance = session_provenance(
            None,
            "blocks[samp_rate]",
            kind="session_block",
        )

        self.assertEqual(provenance.kind, "session_block")
        self.assertEqual(provenance.path, "<in-memory-flowgraph>")
        self.assertEqual(provenance.pointer, "blocks[samp_rate]")


if __name__ == "__main__":
    unittest.main()
