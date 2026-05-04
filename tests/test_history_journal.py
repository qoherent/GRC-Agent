"""Tests for local graph checkpointing and CLI-only restore."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.cli import main as cli_main
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.history import GraphHistoryJournal


FIXTURE = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"
MESSAGE_REWIRE_FIXTURE = Path(__file__).resolve().parent / "data" / "rewire_message_ambiguous.grc"


def _load_agent(journal_path: Path, fixture: Path = FIXTURE) -> tuple[GrcAgent, FlowgraphSession]:
    session = FlowgraphSession()
    session.load(fixture)
    return GrcAgent(session, history_journal_path=journal_path), session


def _records(journal_path: Path, *, accepted_only: bool = False) -> list[dict]:
    return GraphHistoryJournal(journal_path).list_records(accepted_only=accepted_only)


class GraphHistoryJournalTests(unittest.TestCase):
    def test_baseline_checkpoint_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.jsonl"
            _agent, session = _load_agent(journal_path)

            records = _records(journal_path, accepted_only=True)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["tool_name"], "initial_session")
        self.assertEqual(records[0]["operation_type"], "load")
        self.assertEqual(records[0]["state_revision"], session.state_revision)
        self.assertIn("graph_snapshot", records[0])
        self.assertEqual(records[0]["graph_delta"]["baseline"], True)

    def test_checkpoint_after_successful_apply_edit_has_delta_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.jsonl"
            agent, _session = _load_agent(journal_path)

            result = agent.execute_tool(
                "apply_edit",
                {
                    "transaction": {
                        "op_type": "update_params",
                        "instance_name": "samp_rate",
                        "params": {"value": "48000"},
                    }
                },
            )
            records = _records(journal_path, accepted_only=True)

        self.assertTrue(result["ok"], result)
        self.assertEqual(len(records), 2)
        edit_record = records[-1]
        self.assertEqual(edit_record["tool_name"], "apply_edit")
        self.assertEqual(edit_record["operation_type"], "update_params")
        self.assertNotEqual(edit_record["before_hash"], edit_record["after_hash"])
        self.assertEqual(edit_record["validation_result"]["status"], "valid")
        changed_blocks = edit_record["graph_delta"]["changed_blocks"]
        self.assertEqual(len(changed_blocks), 1)
        self.assertEqual(changed_blocks[0]["param_changes"]["value"]["after"], "48000")

    def test_checkpoint_after_successful_rewire_connection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.jsonl"
            agent, _session = _load_agent(journal_path, MESSAGE_REWIRE_FIXTURE)

            result = agent.execute_tool(
                "rewire_connection",
                {
                    "old_connection_id": "strobe_0:strobe->debug_0:print",
                    "new_src_block": "strobe_0",
                    "new_src_port": "strobe",
                    "new_dst_block": "debug_1",
                    "new_dst_port": "print",
                },
            )
            records = _records(journal_path, accepted_only=True)

        self.assertTrue(result["ok"], result)
        self.assertEqual(records[-1]["tool_name"], "rewire_connection")
        self.assertEqual(records[-1]["operation_type"], "remove_connection+add_connection")
        self.assertEqual(
            records[-1]["graph_delta"]["removed_connections"],
            ["strobe_0:strobe->debug_0:print"],
        )
        self.assertEqual(
            records[-1]["graph_delta"]["added_connections"],
            ["strobe_0:strobe->debug_1:print"],
        )

    def test_propose_edit_does_not_create_committed_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.jsonl"
            agent, _session = _load_agent(journal_path)

            result = agent.execute_tool(
                "propose_edit",
                {
                    "transaction": {
                        "op_type": "update_params",
                        "instance_name": "samp_rate",
                        "params": {"value": "48000"},
                    }
                },
            )
            accepted = _records(journal_path, accepted_only=True)
            all_records = _records(journal_path)

        self.assertTrue(result["ok"], result)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(all_records), 1)

    def test_failed_mutation_records_failure_but_no_accepted_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.jsonl"
            agent, session = _load_agent(journal_path)
            before_revision = session.state_revision

            result = agent.execute_tool(
                "apply_edit",
                {
                    "transaction": {
                        "op_type": "remove_block",
                        "instance_name": "blocks_throttle2_0",
                    }
                },
            )
            accepted = _records(journal_path, accepted_only=True)
            all_records = _records(journal_path)

        self.assertFalse(result["ok"], result)
        self.assertEqual(session.state_revision, before_revision)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(all_records), 2)
        self.assertFalse(all_records[-1]["accepted"])
        self.assertEqual(all_records[-1]["record_type"], "failure")

    def test_retention_keeps_last_100_accepted_versions_per_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.jsonl"
            agent, session = _load_agent(journal_path)
            journal = GraphHistoryJournal(journal_path)
            lineage = records = _records(journal_path, accepted_only=True)
            self.assertEqual(len(records), 1)
            lineage_key = records[0]["lineage_key"]

            for index in range(105):
                journal.record_checkpoint(
                    lineage_key=lineage_key,
                    session=session,
                    before=None,
                    request_text=f"checkpoint {index}",
                    tool_name="test",
                    operation_type="test",
                    validation_result=session.validation_state(),
                )
            accepted = _records(journal_path, accepted_only=True)

        self.assertIsNotNone(agent)
        self.assertIsNotNone(lineage)
        self.assertEqual(len(accepted), 100)
        self.assertEqual(accepted[0]["request_text"], "checkpoint 5")

    def test_restore_writes_only_explicit_copy_path_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.jsonl"
            restore_path = Path(tmpdir) / "restored.grc"
            agent, _session = _load_agent(journal_path)
            result = agent.execute_tool(
                "apply_edit",
                {
                    "transaction": {
                        "op_type": "update_params",
                        "instance_name": "samp_rate",
                        "params": {"value": "48000"},
                    }
                },
            )
            self.assertTrue(result["ok"], result)
            edit_record = _records(journal_path, accepted_only=True)[-1]

            restore = GraphHistoryJournal(journal_path).restore_record(
                edit_record["id"],
                restore_path,
            )
            reloaded = FlowgraphSession()
            reloaded.load(restore_path)

        self.assertTrue(restore["ok"], restore)
        self.assertTrue(restore["valid"], restore)
        self.assertEqual(
            next(
                block.params["parameters"]["value"]
                for block in reloaded.flowgraph.blocks
                if block.instance_name == "samp_rate"
            ),
            "48000",
        )

    def test_restore_refuses_to_overwrite_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.jsonl"
            existing_path = Path(tmpdir) / "existing.grc"
            shutil.copy2(FIXTURE, existing_path)
            original_text = existing_path.read_text(encoding="utf-8")
            agent, _session = _load_agent(journal_path)
            record_id = _records(journal_path, accepted_only=True)[0]["id"]

            restore = GraphHistoryJournal(journal_path).restore_record(record_id, existing_path)
            current_text = existing_path.read_text(encoding="utf-8")

        self.assertFalse(restore["ok"])
        self.assertEqual(restore["error_type"], "restore_target_exists")
        self.assertEqual(current_text, original_text)
        self.assertIsNotNone(agent)

    def test_history_cli_list_show_diff_restore(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.jsonl"
            restore_path = Path(tmpdir) / "cli_restore.grc"
            agent, _session = _load_agent(journal_path)
            edit = agent.execute_tool(
                "apply_edit",
                {
                    "transaction": {
                        "op_type": "update_params",
                        "instance_name": "samp_rate",
                        "params": {"value": "48000"},
                    }
                },
            )
            self.assertTrue(edit["ok"], edit)
            baseline_id, edit_id = [
                record["id"] for record in _records(journal_path, accepted_only=True)
            ]

            stdout = StringIO()
            with redirect_stdout(stdout):
                list_code = cli_main([
                    "history",
                    "--journal-path",
                    str(journal_path),
                    "list",
                    "--json",
                ])
            listed = json.loads(stdout.getvalue())

            stdout = StringIO()
            with redirect_stdout(stdout):
                show_code = cli_main([
                    "history",
                    "--journal-path",
                    str(journal_path),
                    "show",
                    edit_id,
                    "--json",
                ])
            shown = json.loads(stdout.getvalue())

            stdout = StringIO()
            with redirect_stdout(stdout):
                diff_code = cli_main([
                    "history",
                    "--journal-path",
                    str(journal_path),
                    "diff",
                    baseline_id,
                    edit_id,
                    "--json",
                ])
            diffed = json.loads(stdout.getvalue())

            stdout = StringIO()
            with redirect_stdout(stdout):
                restore_code = cli_main([
                    "history",
                    "--journal-path",
                    str(journal_path),
                    "restore",
                    edit_id,
                    "--to",
                    str(restore_path),
                    "--json",
                ])
            restored = json.loads(stdout.getvalue())

        self.assertEqual(list_code, 0)
        self.assertEqual(show_code, 0)
        self.assertEqual(diff_code, 0)
        self.assertEqual(restore_code, 0)
        self.assertEqual(len(listed["records"]), 2)
        self.assertEqual(shown["id"], edit_id)
        self.assertTrue(diffed["graph_delta"]["changed"])
        self.assertTrue(restored["valid"])


if __name__ == "__main__":
    unittest.main()
