"""Focused coverage for active copied-file save and hash integrity."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from grc_agent._payload import ErrorCode
from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession

FIXTURE = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_temp_session(tmpdir: str) -> FlowgraphSession:
    dst = Path(tmpdir) / FIXTURE.name
    shutil.copy2(FIXTURE, dst)
    session = FlowgraphSession()
    session.load(dst)
    return session


def _mark_session_valid(session: FlowgraphSession) -> None:
    session.last_validation_ok = True
    session.last_validation_revision = session.state_revision
    session.last_validation_returncode = 0
    session.last_validation_stdout = ""
    session.last_validation_stderr = ""


def _mark_agent_valid(agent: GrcAgent) -> None:
    _mark_session_valid(agent.session)
    agent._last_validation_ok = True
    agent._last_validated_state_revision = agent.session.state_revision


def _block_param_value(session: FlowgraphSession, instance_name: str, param_key: str) -> object:
    assert session.flowgraph is not None
    for block in session.flowgraph.blocks:
        if block.name == instance_name:
            return block.params["parameters"][param_key]
    raise AssertionError(f"Block not found: {instance_name}")


class SaveIntegrityTests(unittest.TestCase):
    def test_session_tracks_loaded_and_saved_active_file_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = _load_temp_session(tmpdir)
            assert session.path is not None
            loaded_hash = _sha256(session.path)
            original_text = session.path.read_text(encoding="utf-8")

            self.assertEqual(session.persisted_file_sha256, loaded_hash)
            self.assertEqual(session.file_integrity_state()["status"], "clean")

            session.set_param("samp_rate", "value", "48000")
            _mark_session_valid(session)
            session.save()

            saved_hash = _sha256(session.path)
            self.assertEqual(session.persisted_file_sha256, saved_hash)
            self.assertNotEqual(saved_hash, loaded_hash)
            self.assertFalse(session.is_dirty)
            self.assertEqual(session.file_integrity_state()["status"], "clean")
            backup_dir = session.path.parent / ".grc_agent" / "backups"
            backups = list(backup_dir.glob(f"*-{loaded_hash[:16]}.grc"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), original_text)

    def test_session_save_refuses_externally_modified_active_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = _load_temp_session(tmpdir)
            assert session.path is not None
            original_hash = session.persisted_file_sha256
            session.set_param("samp_rate", "value", "48000")
            _mark_session_valid(session)
            session.path.write_text(
                session.path.read_text(encoding="utf-8") + "\n# external edit\n",
                encoding="utf-8",
            )

            with self.assertRaises(OSError) as caught:
                session.save()

            self.assertIn("changed on disk", str(caught.exception))
            self.assertTrue(session.is_dirty)
            self.assertEqual(session.persisted_file_sha256, original_hash)
            self.assertEqual(session.file_integrity_state()["status"], "modified")

    def test_session_save_refuses_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = _load_temp_session(tmpdir)
            assert session.path is not None
            real_target = session.path.with_name("real_target.grc")
            shutil.copy2(session.path, real_target)
            symlink_target = session.path.with_name("symlink_target.grc")
            symlink_target.symlink_to(real_target.name)
            session.path = symlink_target
            session._persisted_file_sha256 = _sha256(real_target)
            session.set_param("samp_rate", "value", "48000")
            _mark_session_valid(session)

            with self.assertRaises(OSError) as caught:
                session.save()

            self.assertIn("symlink", str(caught.exception))
            self.assertTrue(session.is_dirty)

    def test_manual_save_refuses_externally_modified_active_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = _load_temp_session(tmpdir)
            assert session.path is not None
            agent = GrcAgent(session)
            _mark_agent_valid(agent)
            before = session.path.read_text(encoding="utf-8")
            session.path.write_text(before + "\n# external edit\n", encoding="utf-8")

            result = agent.execute_tool("save_graph", {})

            self.assertFalse(result.get("ok"), result)
            self.assertEqual(result.get("error_type"), ErrorCode.STALE_REVISION)
            self.assertEqual(result.get("file_integrity", {}).get("status"), "modified")
            self.assertIn("# external edit", session.path.read_text(encoding="utf-8"))

    def test_change_graph_commit_refuses_externally_modified_active_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = _load_temp_session(tmpdir)
            assert session.path is not None
            agent = GrcAgent(session)
            before_revision = session.state_revision
            before_value = _block_param_value(session, "samp_rate", "value")
            session.path.write_text(
                session.path.read_text(encoding="utf-8") + "\n# external edit\n",
                encoding="utf-8",
            )

            result = agent.execute_tool(
                "change_graph",
                {"update_params": [{"instance_name": "samp_rate", "params": {"value": "48000"}}]},
            )

            self.assertFalse(result.get("ok"), result)
            self.assertFalse(result.get("committed"), result)
            self.assertEqual(result.get("error_type"), ErrorCode.STALE_REVISION)
            self.assertEqual(result.get("file_integrity", {}).get("status"), "modified")
            self.assertEqual(session.state_revision, before_revision)
            self.assertEqual(
                _block_param_value(session, "samp_rate", "value"),
                before_value,
            )

    def test_failed_atomic_write_preserves_session_save_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = _load_temp_session(tmpdir)
            assert session.path is not None
            original_text = session.path.read_text(encoding="utf-8")
            original_hash = session.persisted_file_sha256
            session.set_param("samp_rate", "value", "48000")
            _mark_session_valid(session)

            with mock.patch.object(
                FlowgraphSession,
                "_atomic_write_text",
                side_effect=OSError("simulated save failure"),
            ):
                with self.assertRaises(OSError):
                    session.save()

            self.assertTrue(session.is_dirty)
            self.assertEqual(session.persisted_file_sha256, original_hash)
            self.assertEqual(session.path.read_text(encoding="utf-8"), original_text)

    def test_change_graph_surfaces_autosave_failure_and_keeps_dirty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = _load_temp_session(tmpdir)
            assert session.path is not None
            agent = GrcAgent(session)
            original_hash = session.persisted_file_sha256

            with mock.patch.object(
                FlowgraphSession,
                "_atomic_write_text",
                side_effect=OSError("simulated autosave failure"),
            ):
                result = agent.execute_tool(
                    "change_graph",
                    {
                        "update_params": [
                            {"instance_name": "samp_rate", "params": {"value": "48000"}}
                        ],
                    },
                )

            self.assertTrue(result.get("ok"), result)
            self.assertTrue(session.is_dirty)
            self.assertEqual(session.persisted_file_sha256, original_hash)
            self.assertEqual(_block_param_value(session, "samp_rate", "value"), "48000")


if __name__ == "__main__":
    unittest.main()
