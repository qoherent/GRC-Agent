"""Tests for GraphHistoryJournal database operations."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from grc_agent.history import (
    GraphHistoryJournal,
    lineage_key_for_session,
    snapshot_session,
)
from grc_agent.session import load_grc


class HistoryJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="grc_hist_")
        self.tmp = Path(self._tmp.name)
        self.journal_path = self.tmp / "journal.db"
        self.fixture_path = Path(__file__).resolve().parent / "data" / "dial_tone.grc"

    def tearDown(self) -> None:
        # Tear down the temp dir. If there was a connection leak, unlinking the journal database
        # file might raise permission/busy errors on some platforms, or we can check file existence.
        self._tmp.cleanup()

    def test_journal_record_and_retrieve(self) -> None:
        session = load_grc(self.fixture_path)
        journal = GraphHistoryJournal(path=self.journal_path)

        before = snapshot_session(session)
        lineage = lineage_key_for_session(session)

        # Record a checkpoint
        rec = journal.record_checkpoint(
            lineage_key=lineage,
            session=session,
            before=before,
            request_text="add block",
            tool_name="change_graph",
            operation_type="add",
        )

        self.assertEqual(rec["record_type"], "checkpoint")
        self.assertTrue(rec["accepted"])

        # Confirm the record actually persisted (no production reader exists
        # for a single record by id, so query the journal file directly).
        with sqlite3.connect(self.journal_path) as conn:
            row = conn.execute(
                "SELECT payload FROM history_records WHERE id=?", (rec["id"],)
            ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(json.loads(row[0])["id"], rec["id"])

    def test_journal_no_connection_leak(self) -> None:
        session = load_grc(self.fixture_path)
        journal = GraphHistoryJournal(path=self.journal_path)
        before = snapshot_session(session)
        lineage = lineage_key_for_session(session)

        # Run multiple records & queries to ensure no file handle exhaustion
        for i in range(20):
            rec = journal.record_checkpoint(
                lineage_key=lineage,
                session=session,
                before=before,
                request_text=f"add block {i}",
                tool_name="change_graph",
                operation_type="add",
            )
            with sqlite3.connect(self.journal_path) as conn:
                conn.execute(
                    "SELECT payload FROM history_records WHERE id=?", (rec["id"],)
                ).fetchone()

        # Unlink the database file. If a connection was leaked and left open, unlinking would fail
        # on Windows or we can check active handles.
        self.journal_path.unlink()
