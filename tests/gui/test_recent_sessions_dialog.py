"""Tests for the recent-sessions dialog and the GUI's session loader."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from grc_agent.sessions_store import (
    MessageRecord,
    SessionRecord,
    append_message_sync,
    list_sessions_sync,
    open_session_sync,
)
from grc_agent_gui.recent_sessions_dialog import (
    RecentSessionsDialog,
    _render_preview,
)
from PySide6.QtWidgets import QApplication


def _make_session(id: int = 1, **overrides: object) -> SessionRecord:
    base = dict(
        id=id,
        graph_path="/tmp/example.grc",
        graph_hash="grc:abc",
        started_at="2026-06-01T00:00:00.000000Z",
        ended_at=None,
        model_alias="test-model",
        backend="ollama",
        title=f"Session {id}",
        message_count=0,
        graph_exists=True,
    )
    base.update(overrides)
    return SessionRecord(**base)


class PreviewRenderTests(unittest.TestCase):
    def test_render_includes_role_headings(self) -> None:
        messages = [
            MessageRecord(
                id="m1", session_id=1, sequence=0, role="user",
                text="hi", payload=None, created_at="t1",
            ),
            MessageRecord(
                id="m2", session_id=1, sequence=1, role="assistant",
                text="hello", payload=None, created_at="t2",
            ),
        ]
        out = _render_preview(messages)
        self.assertIn("## You", out)
        self.assertIn("hi", out)
        self.assertIn("## Agent", out)
        self.assertIn("hello", out)

    def test_render_caps_at_max_lines(self) -> None:
        messages = [
            MessageRecord(
                id=f"m{i}", session_id=1, sequence=i, role="user",
                text=f"msg{i}\n" * 5, payload=None, created_at="t",
            )
            for i in range(50)
        ]
        out = _render_preview(messages, max_lines=10)
        self.assertIn("truncated", out)


class RecentSessionsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_dialog_builds_with_sessions(self) -> None:
        sessions = [
            _make_session(id=1, title="Alpha"),
            _make_session(id=2, title="Beta"),
        ]
        dialog = RecentSessionsDialog(
            sessions=sessions,
            message_preview_loader=lambda sid: [],
        )
        self.assertEqual(dialog.list_widget.count(), 2)
        # The first row is auto-selected.
        self.assertEqual(dialog.list_widget.currentRow(), 0)
        dialog.close()

    def test_dialog_handles_empty_sessions(self) -> None:
        dialog = RecentSessionsDialog(
            sessions=[],
            message_preview_loader=lambda sid: [],
        )
        self.assertEqual(dialog.list_widget.count(), 0)
        dialog.close()

    def test_double_click_emits_session_opened(self) -> None:
        sessions = [_make_session(id=42, title="target")]
        captured: list[int] = []
        dialog = RecentSessionsDialog(
            sessions=sessions,
            message_preview_loader=lambda sid: [],
        )
        dialog.session_opened.connect(captured.append)
        # Simulate a double-click on the first row.
        dialog.list_widget.setCurrentRow(0)
        dialog._on_open_clicked()
        self.assertEqual(captured, [42])
        dialog.close()

    def test_preview_loader_called_on_selection(self) -> None:
        sessions = [_make_session(id=1), _make_session(id=2)]
        calls: list[int] = []
        def loader(sid: int) -> list:
            calls.append(sid)
            return []
        dialog = RecentSessionsDialog(
            sessions=sessions,
            message_preview_loader=loader,
        )
        dialog.list_widget.setCurrentRow(1)
        # Setting the row fires ``itemSelectionChanged`` which calls
        # ``_refresh_preview``; the loader is invoked with the new
        # session id.
        self.assertIn(2, calls)
        dialog.close()


class SessionListIntegrationTests(unittest.TestCase):
    """Round-trip via the sync API. Exercises the data path the
    ``Recent Sessions...`` dialog reads from."""

    def test_list_and_open_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "sessions.db"
            sid = open_session_sync(
                db,
                graph_path="/x.grc",
                graph_hash="g:1",
                model_alias="m",
                title="T",
            )
            append_message_sync(
                db, session_id=sid, role="user", text="hello"
            )
            sessions = list_sessions_sync(db, limit=10)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].title, "T")
            self.assertEqual(sessions[0].message_count, 0)  # writer hasn't run
            # The sync API does not update message_count; that
            # is the writer's job. We assert the row is present.


if __name__ == "__main__":
    unittest.main()
