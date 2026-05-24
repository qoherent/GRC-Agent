"""Compact safety tests for vector retrieval record construction."""

import json
from pathlib import Path
import tempfile
import unittest

from grc_agent.manual import clean_manual_page
from grc_agent.retrieval import vector as vector_module
from grc_agent.retrieval.vector import (
    CATALOG_SEMANTIC_METADATA,
    INDEX_SCHEMA_VERSION,
    MISS_INTAKE_SCHEMA_VERSION,
    SOURCE_TYPE_CATALOG_BLOCK,
    SOURCE_TYPE_MANUAL_CHUNK,
    SOURCE_TYPE_TUTORIAL_CHUNK,
    VectorRecord,
    _corpus_hash,
    build_manual_vector_records,
    point_id_for_record,
    propose_vector_metadata,
    record_vector_miss,
    render_vector_result,
)


class VectorRecordTests(unittest.TestCase):
    def test_point_id_and_corpus_hash_are_stable_but_content_sensitive(self) -> None:
        first_id = point_id_for_record("catalog_block:blocks_throttle2")
        second_id = point_id_for_record("catalog_block:blocks_throttle2")
        base = VectorRecord(
            record_id="catalog_block:analog_agc_xx",
            source_type="catalog_block",
            canonical_block_id="analog_agc_xx",
            title="AGC",
            normalized_text="automatic gain control",
            provenance={"path": "/catalog/analog_agc_xx.block.yml"},
            metadata={"aliases": ["automatic gain control"]},
            source_hash="same-source",
            corpus_version="test",
            index_schema_version=INDEX_SCHEMA_VERSION,
        )
        changed = VectorRecord(
            record_id=base.record_id,
            source_type=base.source_type,
            canonical_block_id=base.canonical_block_id,
            title=base.title,
            normalized_text="automatic gain control normalize signal level",
            provenance=base.provenance,
            metadata={"aliases": ["automatic gain control", "normalize signal level"]},
            source_hash=base.source_hash,
            corpus_version=base.corpus_version,
            index_schema_version=base.index_schema_version,
        )

        self.assertEqual(first_id, second_id)
        self.assertRegex(first_id, r"^[0-9a-f-]{36}$")
        self.assertNotEqual(_corpus_hash([base]), _corpus_hash([changed]))

    def test_catalog_semantic_metadata_is_governed(self) -> None:
        self.assertIn("analog_agc_xx", CATALOG_SEMANTIC_METADATA)
        self.assertIn("blocks_head", CATALOG_SEMANTIC_METADATA)
        self.assertIn("high_pass_filter", CATALOG_SEMANTIC_METADATA)
        for block_id, entry in CATALOG_SEMANTIC_METADATA.items():
            with self.subTest(block_id=block_id):
                self.assertEqual(entry.get("field"), "aliases")
                self.assertGreater(len(entry.get("aliases", ())), 0)
                self.assertGreater(len(entry.get("reason", "")), 20)
                self.assertGreater(len(entry.get("helped_queries", ())), 0)
                self.assertGreater(len(entry.get("false_positive_checks", ())), 0)

    def test_rendered_result_strips_mutation_shaped_fields(self) -> None:
        record = VectorRecord(
            record_id="catalog_block:blocks_throttle2",
            source_type="catalog_block",
            canonical_block_id="blocks_throttle2",
            title="Throttle",
            normalized_text="Throttle block limits sample rate.",
            provenance={"path": "/catalog/blocks_throttle2.block.yml"},
            metadata={
                "parameter_names": ["samples_per_second"],
                "transaction": {"op_type": "remove_block"},
                "params": {"value": "bad"},
                "insert_tool_args": {"block_type": "blocks_throttle2"},
            },
            source_hash="abc123",
            corpus_version="test",
            index_schema_version=INDEX_SCHEMA_VERSION,
        )

        result = render_vector_result(record, vector_score_raw=0.91)

        self.assertEqual(result["canonical_block_id"], "blocks_throttle2")
        self.assertIn("vector_similarity", result["match_reason"])
        self.assertNotIn("transaction", result)
        self.assertNotIn("params", result)
        self.assertNotIn("insert_tool_args", result)

    def test_vector_rerank_prefers_exact_identity_over_nearby_vector_score(self) -> None:
        audio_sink = VectorRecord(
            record_id="catalog_block:audio_sink",
            source_type=SOURCE_TYPE_CATALOG_BLOCK,
            canonical_block_id="audio_sink",
            title="Audio Sink",
            normalized_text="Audio sink plays samples through speakers.",
            provenance={"path": "/catalog/audio_sink.block.yml"},
            metadata={"aliases": []},
            source_hash="audio",
            corpus_version="test",
            index_schema_version=INDEX_SCHEMA_VERSION,
        )
        low_pass = VectorRecord(
            record_id="catalog_block:low_pass_filter",
            source_type=SOURCE_TYPE_CATALOG_BLOCK,
            canonical_block_id="low_pass_filter",
            title="Low Pass Filter",
            normalized_text="Low pass filter audio smoother removes high frequency noise.",
            provenance={"path": "/catalog/low_pass_filter.block.yml"},
            metadata={"aliases": ["audio smoother", "smoothing filter"]},
            source_hash="low-pass",
            corpus_version="test",
            index_schema_version=INDEX_SCHEMA_VERSION,
        )

        candidates = vector_module._rerank_vector_records(
            "low_pass_filter",
            [(audio_sink, 0.91), (low_pass, 0.84)],
            limit=1,
            scope="catalog",
        )

        self.assertEqual(candidates[0].record.canonical_block_id, "low_pass_filter")

    def test_docs_rerank_diversifies_repeated_source_chunks(self) -> None:
        def manual_record(record_id: str, title: str, path: str) -> VectorRecord:
            return VectorRecord(
                record_id=record_id,
                source_type=SOURCE_TYPE_MANUAL_CHUNK,
                canonical_block_id=None,
                title=title,
                normalized_text="message ports PMT stream tags metadata",
                provenance={"path": path},
                metadata={"section": title},
                source_hash=record_id,
                corpus_version="test",
                index_schema_version=INDEX_SCHEMA_VERSION,
            )

        same_source = [
            manual_record(f"manual_chunk:message_passing:{index}", "Message Passing", "/docs/msg.md")
            for index in range(3)
        ]
        other_source = manual_record(
            "manual_chunk:python_blocks:0",
            "Python Block Message Passing",
            "/docs/python.md",
        )

        candidates = vector_module._rerank_vector_records(
            "message ports",
            [
                (same_source[0], 0.90),
                (same_source[1], 0.89),
                (same_source[2], 0.88),
                (other_source, 0.82),
            ],
            limit=3,
            scope="manual",
        )

        self.assertEqual(len(candidates), 3)
        self.assertIn("manual_chunk:python_blocks:0", [item.record.record_id for item in candidates])

    def test_manual_records_use_manifest_for_tutorial_classification(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manual = root / "Manual_Page.md"
            tutorial = root / "Tutorial_Page.md"
            manifest = root / "tutorial_manifest.txt"
            manual.write_text("# Manual Page\n\nGeneral PMT concepts.\n", encoding="utf-8")
            tutorial.write_text("# Tutorial Page\n\nStep-by-step learning text.\n", encoding="utf-8")
            manifest.write_text("Tutorial_Page.md\n", encoding="utf-8")

            records = build_manual_vector_records(
                corpus_root=root,
                tutorial_manifest_path=manifest,
                corpus_version="test-corpus",
            )

        source_types = {record.title: record.source_type for record in records}
        self.assertEqual(source_types["Manual Page"], SOURCE_TYPE_MANUAL_CHUNK)
        self.assertEqual(source_types["Tutorial Page"], SOURCE_TYPE_TUTORIAL_CHUNK)
        self.assertTrue(all(record.index_schema_version == INDEX_SCHEMA_VERSION for record in records))

    def test_manual_retrieved_url_parser_handles_markdown_links_with_parentheses(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            page_path = Path(tmpdir) / "Polymorphic_Types_(PMTs).md"
            page_path.write_text(
                "\n".join(
                    [
                        "# Polymorphic Types (PMTs)",
                        "",
                        "PMT dictionaries are lists of key:value pairs.",
                        "",
                        (
                            'Retrieved from "[https://wiki.gnuradio.org/index.php?'
                            "title=Polymorphic_Types_(PMTs)&oldid=14604]"
                            "(https://wiki.gnuradio.org/index.php?"
                            r"title=Polymorphic_Types_\(PMTs\)&oldid=14604)\""
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            page = clean_manual_page(page_path)

        self.assertEqual(
            page.source_url,
            "https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_(PMTs)&oldid=14604",
        )
        self.assertEqual(page.oldid, "14604")
        self.assertNotIn("](", page.source_url)

    def test_vector_miss_intake_redacts_paths_and_metadata_proposals_are_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "misses.jsonl"
            before_metadata = json.dumps(CATALOG_SEMANTIC_METADATA, sort_keys=True)
            first = record_vector_miss(
                "/home/alice/private/radio_demo.grc waveform",
                expected_block_ids=["qtgui_time_sink_x"],
                actual_top_ids=["blocks_probe_signal_x"],
                category="missing_metadata",
                source="real_user",
                notes="See /tmp/private_notes.grc",
                intake_path=intake_path,
            )
            second = record_vector_miss(
                "waveform viewer",
                expected_block_ids=["qtgui_time_sink_x"],
                actual_top_ids=["analog_random_source_x"],
                category="missing_metadata",
                source="manual_review",
                intake_path=intake_path,
            )
            proposal = propose_vector_metadata(intake_path=intake_path)
            stored = json.loads(intake_path.read_text(encoding="utf-8").splitlines()[0])
            after_metadata = json.dumps(CATALOG_SEMANTIC_METADATA, sort_keys=True)

        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(stored["schema_version"], MISS_INTAKE_SCHEMA_VERSION)
        self.assertNotIn("/home/alice", json.dumps(stored))
        self.assertNotIn("radio_demo.grc", json.dumps(stored))
        self.assertTrue(proposal["ok"], proposal)
        self.assertIn("candidate_count", proposal)
        self.assertEqual(before_metadata, after_metadata)


if __name__ == "__main__":
    unittest.main()
