"""Tests for read-only GNU Radio manual cleaning and search."""

from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import unittest

from grc_agent.cli import main
from grc_agent.agent import GrcAgent
from grc_agent.manual import clean_manual_page, search_manual
from grc_agent.runtime.tool_schemas import build_tool_schemas


WIKI_ROOT = Path(__file__).resolve().parents[2] / "docs" / "wiki_gnuradio_org"


class ManualCleanerTests(unittest.TestCase):
    def test_cleaner_removes_mediawiki_boilerplate_and_preserves_provenance(self) -> None:
        page = clean_manual_page(WIKI_ROOT / "Your_First_Flowgraph.md")

        joined = "\n".join(chunk.text for chunk in page.chunks)
        self.assertEqual(page.title, "Your First Flowgraph")
        self.assertIn("wiki.gnuradio.org", page.source_url)
        self.assertEqual(page.oldid, "12960")
        self.assertIn("Starting GNU Radio Companion", joined)
        self.assertNotIn("Jump to navigation", joined)
        self.assertNotIn("Navigation menu", joined)
        self.assertTrue(all(chunk.line_start <= chunk.line_end for chunk in page.chunks))

    def test_search_manual_returns_bounded_cited_results_without_mutation_fields(self) -> None:
        payload = search_manual("sample rate", k=3, corpus_root=WIKI_ROOT)

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["query"], "sample rate")
        self.assertGreaterEqual(len(payload["results"]), 1)
        self.assertLessEqual(len(payload["results"]), 3)
        first = payload["results"][0]
        self.assertIn("excerpt", first)
        self.assertIn("citation", first)
        self.assertIn("path", first["citation"])
        self.assertIn("line_start", first["citation"])
        self.assertNotIn("transaction", first)
        self.assertNotIn("params", first)
        self.assertNotIn("block_id", first)
        self.assertNotIn("insert_tool_args", first)

    def test_cli_manual_search_outputs_json(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["manual", "search", "message passing", "--k", "2", "--json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"], payload)
        self.assertLessEqual(len(payload["results"]), 2)

    def test_model_tool_schema_places_manual_search_after_describe_block(self) -> None:
        names = [schema["function"]["name"] for schema in build_tool_schemas()]

        self.assertIn("search_manual", names)
        self.assertGreater(names.index("search_manual"), names.index("describe_block"))
        self.assertLess(names.index("search_manual"), names.index("apply_edit"))

    def test_agent_search_manual_tool_is_read_only_and_cited(self) -> None:
        agent = GrcAgent()
        result = agent.execute_tool("search_manual", {"query": "stream tags", "k": 2})

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool"], "search_manual")
        self.assertLessEqual(len(result["results"]), 2)
        self.assertIn("citation", result["results"][0])
        self.assertNotIn("transaction", result["results"][0])
        self.assertNotIn("insert_tool_args", result["results"][0])


if __name__ == "__main__":
    unittest.main()
