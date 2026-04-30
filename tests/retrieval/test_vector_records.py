"""Tests for safe vector retrieval record construction."""

import json
from pathlib import Path
import tempfile
import unittest

from grc_agent.retrieval.vector import (
    CATALOG_SEMANTIC_METADATA,
    DEFAULT_VECTOR_COLLECTION_ALIAS,
    INDEX_SCHEMA_VERSION,
    MAX_QUERY_CHARS,
    MISS_INTAKE_SCHEMA_VERSION,
    SOURCE_TYPE_MANUAL_CHUNK,
    SOURCE_TYPE_TUTORIAL_CHUNK,
    VALID_MISS_SOURCES,
    VectorRecord,
    _corpus_hash,
    build_manual_vector_records,
    point_id_for_record,
    record_vector_miss,
    render_vector_result,
    propose_vector_metadata,
    summarize_vector_misses,
)


class VectorRecordTests(unittest.TestCase):
    _NEGATIVE_TRAP_MARKERS = (
        "delete",
        "disable",
        "insert",
        "insert_tool_args",
        "save",
        "repair",
        "transaction",
        "apply",
        "remove",
        "block recipe",
    )

    def test_point_id_is_stable_uuid5(self) -> None:
        first = point_id_for_record("catalog_block:blocks_throttle2")
        second = point_id_for_record("catalog_block:blocks_throttle2")

        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f-]{36}$")

    def test_corpus_hash_changes_when_embedded_text_changes(self) -> None:
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

        self.assertNotEqual(_corpus_hash([base]), _corpus_hash([changed]))

    def test_catalog_semantic_metadata_entries_have_governance(self) -> None:
        self.assertIn("analog_agc_xx", CATALOG_SEMANTIC_METADATA)
        self.assertIn("blocks_file_source", CATALOG_SEMANTIC_METADATA)
        self.assertIn("blocks_head", CATALOG_SEMANTIC_METADATA)
        self.assertIn("blocks_null_sink", CATALOG_SEMANTIC_METADATA)
        self.assertIn("high_pass_filter", CATALOG_SEMANTIC_METADATA)
        for block_id, entry in CATALOG_SEMANTIC_METADATA.items():
            with self.subTest(block_id=block_id):
                self.assertIsInstance(entry.get("aliases"), tuple)
                self.assertGreater(len(entry["aliases"]), 0)
                self.assertEqual(entry.get("field"), "aliases")
                self.assertIsInstance(entry.get("reason"), str)
                self.assertGreater(len(entry["reason"]), 20)
                self.assertIsInstance(entry.get("helped_queries"), tuple)
                self.assertGreater(len(entry["helped_queries"]), 0)
                self.assertIsInstance(entry.get("false_positive_checks"), tuple)
                self.assertGreater(len(entry["false_positive_checks"]), 0)
                joined_traps = " ".join(entry["false_positive_checks"]).lower()
                self.assertTrue(
                    any(marker in joined_traps for marker in self._NEGATIVE_TRAP_MARKERS),
                    f"{block_id} must include a mutation-shaped negative trap",
                )

    def test_rendered_result_excludes_mutation_shaped_fields(self) -> None:
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
                "save_graph": "/tmp/out.grc",
            },
            source_hash="abc123",
            corpus_version="test",
            index_schema_version=INDEX_SCHEMA_VERSION,
        )

        result = render_vector_result(record, vector_score_raw=0.91)

        self.assertEqual(
            set(result),
            {
                "record_id",
                "source_type",
                "canonical_block_id",
                "title",
                "excerpt",
                "provenance",
                "vector_score_raw",
                "match_reason",
            },
        )
        self.assertEqual(result["canonical_block_id"], "blocks_throttle2")
        self.assertIn("vector_similarity", result["match_reason"])
        self.assertNotIn("transaction", result)
        self.assertNotIn("params", result)
        self.assertNotIn("insert_tool_args", result)
        self.assertNotIn("save_graph", result)

    def test_rendered_result_strips_forbidden_nested_payload_fields(self) -> None:
        record = VectorRecord(
            record_id="manual_chunk:example",
            source_type="manual_chunk",
            canonical_block_id=None,
            title="Manual",
            normalized_text="Manual explanation only.",
            provenance={
                "path": "/docs/manual.md",
                "params": {"value": "bad"},
                "nested": {"apply_edit": {"transaction": "bad"}},
            },
            metadata={},
            source_hash="abc123",
            corpus_version="test",
            index_schema_version=INDEX_SCHEMA_VERSION,
        )

        result = render_vector_result(record, vector_score_raw=0.5)

        self.assertEqual(result["provenance"], {"path": "/docs/manual.md", "nested": {}})

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

    def test_empty_tutorial_manifest_produces_no_tutorial_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            page = root / "Tutorial_Page.md"
            manifest = root / "tutorial_manifest.txt"
            page.write_text("# Tutorial Page\n\nText.\n", encoding="utf-8")
            manifest.write_text("", encoding="utf-8")

            records = build_manual_vector_records(
                corpus_root=root,
                tutorial_manifest_path=manifest,
                corpus_version="test-corpus",
            )

        self.assertEqual({record.source_type for record in records}, {SOURCE_TYPE_MANUAL_CHUNK})

    def test_default_alias_name_is_public_v1_alias(self) -> None:
        self.assertEqual(DEFAULT_VECTOR_COLLECTION_ALIAS, "grc_agent_retrieval_v1")

    def test_record_vector_miss_writes_sanitized_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "misses.jsonl"
            payload = record_vector_miss(
                "leveler block",
                expected_block_ids=["analog_agc_xx"],
                actual_top_ids=["blocks_xor_xx", "blocks_selector"],
                category="ambiguous_wording",
                source="real_user",
                notes="User expected AGC-like candidates.",
                intake_path=intake_path,
            )

            lines = intake_path.read_text(encoding="utf-8").splitlines()

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["record"]["query"], "leveler block")
        self.assertEqual(payload["record"]["schema_version"], MISS_INTAKE_SCHEMA_VERSION)
        self.assertEqual(payload["record"]["source"], "real_user")
        self.assertEqual(payload["record"]["expected_block_ids"], ["analog_agc_xx"])
        self.assertEqual(payload["record"]["actual_top_ids"], ["blocks_xor_xx", "blocks_selector"])
        self.assertEqual(len(lines), 1)
        stored = json.loads(lines[0])
        self.assertEqual(
            set(stored),
            {
                "actual_top_ids",
                "category",
                "expected_block_ids",
                "notes",
                "query",
                "query_key",
                "schema_version",
                "scope",
                "source",
                "timestamp",
            },
        )

    def test_record_vector_miss_redacts_paths_and_graph_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "misses.jsonl"
            payload = record_vector_miss(
                "in /home/alice/private/radio_demo.grc show waveform",
                expected_block_ids=[
                    "qtgui_time_sink_x",
                    "/home/alice/private/custom_instance.grc",
                ],
                actual_top_ids=[
                    "blocks_probe_signal_x",
                    "C:\\Users\\Alice\\secret\\top_instance.grc",
                ],
                notes="C:\\Users\\Alice\\secret\\flow.grc missed the target.",
                intake_path=intake_path,
            )

            stored = json.loads(intake_path.read_text(encoding="utf-8"))

        self.assertTrue(payload["ok"], payload)
        self.assertNotIn("/home/alice", stored["query"])
        self.assertNotIn("radio_demo.grc", stored["query"])
        self.assertNotIn("Alice", stored["notes"])
        self.assertNotIn("flow.grc", stored["notes"])
        self.assertEqual(stored["expected_block_ids"][0], "qtgui_time_sink_x")
        self.assertEqual(stored["actual_top_ids"][0], "blocks_probe_signal_x")
        self.assertNotIn("/home/alice", " ".join(stored["expected_block_ids"]))
        self.assertNotIn("custom_instance.grc", " ".join(stored["expected_block_ids"]))
        self.assertNotIn("Alice", " ".join(stored["actual_top_ids"]))
        self.assertNotIn("top_instance.grc", " ".join(stored["actual_top_ids"]))
        self.assertIn("<path>", stored["query"])
        self.assertIn("<path>", stored["notes"])
        self.assertIn("<path>", stored["expected_block_ids"])
        self.assertIn("<path>", stored["actual_top_ids"])

    def test_record_vector_miss_rejects_unbounded_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = record_vector_miss(
                "x" * (MAX_QUERY_CHARS + 1),
                intake_path=Path(tmpdir) / "misses.jsonl",
            )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "invalid_request")

    def test_record_vector_miss_rejects_unknown_source(self) -> None:
        self.assertIn("real_user", VALID_MISS_SOURCES)
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = record_vector_miss(
                "leveler block",
                source="random_blob",
                intake_path=Path(tmpdir) / "misses.jsonl",
            )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "invalid_request")

    def test_summarize_vector_misses_clusters_repeated_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "misses.jsonl"
            record_vector_miss(
                "show waveform",
                expected_block_ids=["qtgui_time_sink_x"],
                actual_top_ids=["blocks_probe_signal_x"],
                category="missing_metadata",
                source="real_user",
                intake_path=intake_path,
            )
            record_vector_miss(
                "waveform viewer",
                expected_block_ids=["qtgui_time_sink_x"],
                actual_top_ids=["analog_random_source_x"],
                category="missing_metadata",
                source="manual_review",
                intake_path=intake_path,
            )
            record_vector_miss(
                "make it prettier",
                expected_block_ids=[],
                actual_top_ids=["qtgui_time_sink_x"],
                category="ambiguous_wording",
                source="real_user",
                intake_path=intake_path,
            )

            payload = summarize_vector_misses(intake_path=intake_path)

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["total_records"], 3)
        self.assertEqual(payload["cluster_count"], 2)
        first = payload["clusters"][0]
        self.assertEqual(first["count"], 2)
        self.assertEqual(first["expected_block_ids"], ["qtgui_time_sink_x"])
        self.assertEqual(first["recommendation"], "metadata_candidate")
        self.assertEqual(first["notes_count"], 0)
        self.assertIn("cluster_key", first)
        second = payload["clusters"][1]
        self.assertEqual(second["recommendation"], "ambiguity")

    def test_summarize_vector_misses_does_not_merge_unrelated_same_target_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "misses.jsonl"
            record_vector_miss(
                "signal level",
                expected_block_ids=["analog_agc_xx"],
                actual_top_ids=["blocks_probe_signal_x"],
                category="missing_metadata",
                source="real_user",
                intake_path=intake_path,
            )
            record_vector_miss(
                "gain control",
                expected_block_ids=["analog_agc_xx"],
                actual_top_ids=["blocks_multiply_const_xx"],
                category="missing_metadata",
                source="real_user",
                intake_path=intake_path,
            )

            payload = summarize_vector_misses(intake_path=intake_path)

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["total_records"], 2)
        self.assertEqual(payload["cluster_count"], 2)
        self.assertTrue(all(cluster["count"] == 1 for cluster in payload["clusters"]))
        self.assertTrue(
            all(cluster["recommendation"] == "needs_more_evidence" for cluster in payload["clusters"])
        )

    def test_summarize_vector_misses_handles_empty_intake(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = summarize_vector_misses(intake_path=Path(tmpdir) / "missing.jsonl")

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["total_records"], 0)
        self.assertEqual(payload["cluster_count"], 0)
        self.assertEqual(payload["clusters"], [])
        self.assertIn("miss_intake_empty", payload["warnings"])

    def test_summarize_vector_misses_blocks_one_off_metadata_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "misses.jsonl"
            record_vector_miss(
                "waveform viewer",
                expected_block_ids=["qtgui_time_sink_x"],
                actual_top_ids=["analog_random_source_x"],
                category="missing_metadata",
                source="real_user",
                intake_path=intake_path,
            )

            payload = summarize_vector_misses(intake_path=intake_path)

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["clusters"][0]["recommendation"], "needs_more_evidence")

    def test_summarize_vector_misses_redacts_displayed_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "misses.jsonl"
            record_vector_miss(
                "/home/alice/private/radio_demo.grc waveform",
                expected_block_ids=["/home/alice/private/expected.grc"],
                actual_top_ids=["C:\\Users\\Alice\\actual.grc"],
                notes="See /tmp/private_notes.grc",
                category="missing_metadata",
                source="real_user",
                intake_path=intake_path,
            )

            payload = summarize_vector_misses(intake_path=intake_path)
            rendered = json.dumps(payload)

        self.assertTrue(payload["ok"], payload)
        self.assertNotIn("/home/alice", rendered)
        self.assertNotIn("radio_demo.grc", rendered)
        self.assertNotIn("Alice", rendered)
        self.assertNotIn("private_notes.grc", rendered)
        self.assertIn("<path>", rendered)

    def test_metadata_proposal_report_is_threshold_gated_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "misses.jsonl"
            before_metadata = json.dumps(CATALOG_SEMANTIC_METADATA, sort_keys=True)
            record_vector_miss(
                "show waveform",
                expected_block_ids=["qtgui_time_sink_x"],
                actual_top_ids=["blocks_probe_signal_x"],
                category="missing_metadata",
                source="real_user",
                notes="First source.",
                intake_path=intake_path,
            )
            record_vector_miss(
                "waveform viewer",
                expected_block_ids=["qtgui_time_sink_x"],
                actual_top_ids=["analog_random_source_x"],
                category="missing_metadata",
                source="manual_review",
                notes="Second source.",
                intake_path=intake_path,
            )

            payload = propose_vector_metadata(intake_path=intake_path)
            after_metadata = json.dumps(CATALOG_SEMANTIC_METADATA, sort_keys=True)

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["candidate_count"], 1)
        candidate = payload["candidates"][0]
        self.assertEqual(candidate["proposed_block"], "qtgui_time_sink_x")
        self.assertIn("proposed_stable_capability_phrase", candidate)
        self.assertIn("required_negative_trap", candidate)
        self.assertEqual(before_metadata, after_metadata)

    def test_metadata_proposal_report_blocks_one_off_and_ambiguous_clusters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            intake_path = Path(tmpdir) / "misses.jsonl"
            record_vector_miss(
                "waveform viewer",
                expected_block_ids=["qtgui_time_sink_x"],
                actual_top_ids=["analog_random_source_x"],
                category="missing_metadata",
                source="real_user",
                intake_path=intake_path,
            )
            record_vector_miss(
                "make it prettier",
                expected_block_ids=["qtgui_time_sink_x"],
                actual_top_ids=["qtgui_time_sink_x"],
                category="ambiguous_wording",
                source="real_user",
                intake_path=intake_path,
            )

            payload = propose_vector_metadata(intake_path=intake_path)

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["candidate_count"], 0)
        self.assertGreaterEqual(len(payload["blocked_clusters"]), 1)


if __name__ == "__main__":
    unittest.main()
