# Path helps us build a test fixture location relative to this file.
from pathlib import Path
# tempfile gives us isolated directories for save tests.
import tempfile
# unittest is the standard library test framework.
import unittest

import copy
import yaml

# Import the class we want to exercise.
from grc_agent.flowgraph_session import FlowgraphSession


class FlowgraphSessionTests(unittest.TestCase):
    """Tests for loading, saving, and validating .grc flowgraphs."""

    def _fixture_path(self) -> Path:
        # __file__ is the path to this test file.
        test_directory = Path(__file__).resolve().parent
        # The copied sample graph lives under tests/data.
        return test_directory / "data" / "random_bit_generator.grc"

    def _fixture_raw_data(self) -> dict:
        # Load the YAML fixture so tests can build small temporary variants.
        fixture_path = self._fixture_path()
        return yaml.safe_load(fixture_path.read_text(encoding="utf-8"))

    def _detached_variable_block(self, name: str = "unused_var") -> dict:
        # Use a zero-port variable block because it validates while unattached.
        return {
            "name": name,
            "id": "variable",
            "parameters": {"comment": "", "value": "123"},
            "states": {
                "bus_sink": False,
                "bus_source": False,
                "bus_structure": None,
                "coordinate": [16, 16],
                "rotation": 0,
                "state": "enabled",
            },
        }

    def _write_temp_graph(self, directory: str, raw_data: dict, file_name: str) -> Path:
        # Write a temporary .grc file variant for load/save tests.
        graph_path = Path(directory) / file_name
        graph_path.write_text(
            yaml.safe_dump(raw_data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return graph_path

    def _fixture_block_parameters(self, instance_name: str) -> dict:
        # Copy an existing parameter payload so focused structural tests stay realistic.
        raw_data = self._fixture_raw_data()
        block = next(entry for entry in raw_data["blocks"] if entry["name"] == instance_name)
        return copy.deepcopy(block["parameters"])

    # The sample flowgraph is small, stable, and representative for parser coverage.
    def test_load_and_summarize_random_bit_generator(self) -> None:
        # Reuse the fixture path helper so the test stays easy to read.
        fixture_path = self._fixture_path()

        # Start from a fresh, empty session.
        session = FlowgraphSession()
        # Load the sample file into memory.
        session.load(fixture_path)

        # After load, the session should remember which file it came from.
        self.assertEqual(session.path, fixture_path)
        # Loading does not edit anything, so the session should be clean.
        self.assertFalse(session.is_dirty)
        # A successful load should produce a parsed Flowgraph object.
        self.assertIsNotNone(session.flowgraph)

        # Pull the flowgraph out so the next assertions are shorter.
        flowgraph = session.flowgraph
        # Tell the reader and type checker that the flowgraph exists here.
        self.assertIsNotNone(flowgraph)

        # This assert narrows the type after the runtime check above.
        assert flowgraph is not None

        # The sample graph contains five blocks.
        self.assertEqual(len(flowgraph.blocks), 5)
        # The sample graph contains three connections.
        self.assertEqual(len(flowgraph.connections), 3)
        # Each block should keep its instance name and GNU Radio block type.
        self.assertEqual(
            [(block.instance_name, block.block_type) for block in flowgraph.blocks],
            [
                ("samp_rate", "variable"),
                ("analog_random_source_x_0", "analog_random_source_x"),
                ("blocks_char_to_float_0", "blocks_char_to_float"),
                ("blocks_throttle2_0", "blocks_throttle2"),
                ("qtgui_time_sink_x_0", "qtgui_time_sink_x"),
            ],
        )

        # Ask the session for a short human-readable summary.
        summary = session.summarize()
        # The summary should mention the file name.
        self.assertIn("random_bit_generator.grc", summary)
        # The summary should mention the block count.
        self.assertIn("Blocks: 5", summary)
        # The summary should mention the connection count.
        self.assertIn("Connections: 3", summary)
        # The summary should include the first block line.
        self.assertIn("- samp_rate (variable)", summary)
        # The summary should include the last block line.
        self.assertIn("- qtgui_time_sink_x_0 (qtgui_time_sink_x)", summary)

    # Missing files should fail immediately so callers can surface the error directly.
    def test_load_missing_file_raises(self) -> None:
        # Create a new session so there is no previous state involved.
        session = FlowgraphSession()

        # Loading a path that does not exist should raise FileNotFoundError.
        with self.assertRaises(FileNotFoundError):
            session.load("does_not_exist.grc")

    # Malformed block sections should fail fast instead of loading a partial graph.
    def test_load_invalid_blocks_section_raises(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"] = {"not": "a list"}

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_path = self._write_temp_graph(tmpdir, raw_data, "invalid_blocks_section.grc")
            session = FlowgraphSession()

            with self.assertRaises(ValueError):
                session.load(fixture_path)

    # Malformed block entries should fail fast instead of being skipped silently.
    def test_load_malformed_block_entry_raises(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"][0] = ["not", "a", "mapping"]

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_path = self._write_temp_graph(tmpdir, raw_data, "malformed_block_entry.grc")
            session = FlowgraphSession()

            with self.assertRaises(ValueError):
                session.load(fixture_path)

    # Malformed connection sections should fail fast instead of loading a partial graph.
    def test_load_invalid_connections_section_raises(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["connections"] = {"not": "a list"}

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_path = self._write_temp_graph(
                tmpdir,
                raw_data,
                "invalid_connections_section.grc",
            )
            session = FlowgraphSession()

            with self.assertRaises(ValueError):
                session.load(fixture_path)

    # Malformed connection entries should fail fast instead of being skipped silently.
    def test_load_malformed_connection_entry_raises(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["connections"][0] = ["only", "three", "items"]

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_path = self._write_temp_graph(
                tmpdir,
                raw_data,
                "malformed_connection_entry.grc",
            )
            session = FlowgraphSession()

            with self.assertRaises(ValueError):
                session.load(fixture_path)

    # Connections should preserve the exact block/port wiring from the `.grc` file.
    def test_connections_parse_correctly(self) -> None:
        # Reuse the same fixture so this test checks the same parsed graph.
        fixture_path = self._fixture_path()

        # Load the fixture into a fresh session.
        session = FlowgraphSession()
        session.load(fixture_path)

        # Pull out the parsed flowgraph for inspection.
        flowgraph = session.flowgraph
        # The load should have produced a flowgraph object.
        self.assertIsNotNone(flowgraph)

        # Narrow the type after the runtime check above.
        assert flowgraph is not None

        # Compare the parsed connection tuples against the expected wiring.
        self.assertEqual(
            [
                (connection.src_block, connection.src_port, connection.dst_block, connection.dst_port)
                for connection in flowgraph.connections
            ],
            [
                ("analog_random_source_x_0", 0, "blocks_throttle2_0", 0),
                ("blocks_char_to_float_0", 0, "qtgui_time_sink_x_0", 0),
                ("blocks_throttle2_0", 0, "blocks_char_to_float_0", 0),
            ],
        )

    # Saving should write a reusable .grc file to the requested path.
    def test_save_writes_reusable_file(self) -> None:
        # Load the sample graph first so there is something to save.
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        # Use a temporary directory so the repository stays unchanged.
        with tempfile.TemporaryDirectory() as tmpdir:
            # Build the destination path inside the temporary directory.
            save_path = Path(tmpdir) / "saved_random_bit_generator.grc"
            # Write the current session back to disk.
            session.save(save_path)

            # Saving should update the remembered path.
            self.assertEqual(session.path, save_path)
            # Saving should not mark the session dirty.
            self.assertFalse(session.is_dirty)
            # The file should now exist on disk.
            self.assertTrue(save_path.exists())

            # Load the saved file again to prove it is reusable.
            reloaded = FlowgraphSession()
            reloaded.load(save_path)

            # The round-tripped file should still parse into a flowgraph.
            self.assertIsNotNone(reloaded.flowgraph)
            assert reloaded.flowgraph is not None
            # The round-tripped file should still contain the same block count.
            self.assertEqual(len(reloaded.flowgraph.blocks), 5)
            # The round-tripped file should still contain the same connection count.
            self.assertEqual(len(reloaded.flowgraph.connections), 3)

    # Validation should succeed for the known-good sample graph.
    def test_validate_random_bit_generator(self) -> None:
        # Load the sample graph so validate() has real data to compile.
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        # A clean sample graph should validate successfully with grcc.
        self.assertTrue(session.validate())

    # Saving without a loaded graph should fail immediately.
    def test_save_without_load_raises(self) -> None:
        # A brand-new session has no flowgraph to write.
        session = FlowgraphSession()

        # Calling save() before load() should raise ValueError.
        with self.assertRaises(ValueError):
            session.save()

    # Validating without a loaded graph should fail immediately.
    def test_validate_without_load_raises(self) -> None:
        # A brand-new session has nothing to compile.
        session = FlowgraphSession()

        # Calling validate() before load() should raise ValueError.
        with self.assertRaises(ValueError):
            session.validate()

    # After a successful validation the diagnostic fields must be populated.
    def test_validate_records_diagnostics_on_success(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)
        ok = session.validate()
        self.assertTrue(ok)
        self.assertEqual(session.last_validation_returncode, 0)
        self.assertIsNotNone(session.last_validation_stdout)
        self.assertIsNotNone(session.last_validation_stderr)

    # Diagnostics must stay None when validate() raises before subprocess runs.
    def test_validate_without_load_leaves_diagnostics_unset(self) -> None:
        session = FlowgraphSession()
        with self.assertRaises(ValueError):
            session.validate()
        self.assertIsNone(session.last_validation_stdout)
        self.assertIsNone(session.last_validation_stderr)
        self.assertIsNone(session.last_validation_returncode)

    # validate() must treat grcc-reported connection errors as failure even if exit status is 0.
    def test_validate_detects_grcc_reported_missing_block_connection(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None

        # Inject a raw connection that points at a block name not present in the graph.
        flowgraph.raw_data["connections"].append(
            ["missing_block", "0", "blocks_char_to_float_0", "0"]
        )

        self.assertFalse(session.validate())
        self.assertIsNotNone(session.last_validation_returncode)
        combined_output = "\n".join(
            filter(None, [session.last_validation_stdout, session.last_validation_stderr])
        )
        self.assertIn("missing_block", combined_output)

    # A safe mutation should update both the parsed model and the raw YAML.
    def test_set_param_updates_model_and_raw_data(self) -> None:
        # Load the sample graph so there is a block to mutate.
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        # Change the samp_rate variable from 32000 to 48000.
        session.set_param("samp_rate", "value", "48000")

        # The session should now be marked dirty because it differs from disk.
        self.assertTrue(session.is_dirty)

        # Pull out the parsed flowgraph so we can inspect the updated block.
        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None

        # Find the block we just changed in the parsed model.
        block = next(block for block in flowgraph.blocks if block.instance_name == "samp_rate")
        # The parsed model should now show the new value.
        self.assertEqual(block.params["parameters"]["value"], "48000")

        # The raw YAML should also be updated so save() and validate() see the change.
        self.assertEqual(flowgraph.raw_data["blocks"][0]["parameters"]["value"], "48000")

    # Asking for a block that does not exist should raise a clear error.
    def test_set_param_missing_block_raises(self) -> None:
        # Load the sample graph before attempting the mutation.
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        # A missing instance name should raise ValueError.
        with self.assertRaises(ValueError):
            session.set_param("does_not_exist", "value", "123")

    # Disconnecting a known wire should update both the model and raw YAML.
    def test_disconnect_updates_model_and_raw_data(self) -> None:
        # Load the sample graph so there is a connection to remove.
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        # Remove one of the three connections from the graph.
        session.disconnect("blocks_throttle2_0", 0, "blocks_char_to_float_0", 0)

        # The session should now be marked dirty because it differs from disk.
        self.assertTrue(session.is_dirty)

        # Pull out the parsed flowgraph so we can inspect the updated graph.
        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None

        # One connection should have been removed from the parsed model.
        self.assertEqual(len(flowgraph.connections), 2)
        # One connection should also have been removed from the raw YAML.
        self.assertEqual(len(flowgraph.raw_data["connections"]), 2)

        # Confirm the removed wire is no longer present in the parsed model.
        self.assertNotIn(
            ("blocks_throttle2_0", 0, "blocks_char_to_float_0", 0),
            [
                (
                    connection.src_block,
                    connection.src_port,
                    connection.dst_block,
                    connection.dst_port,
                )
                for connection in flowgraph.connections
            ],
        )

    # Asking to disconnect a missing wire should raise a clear error.
    def test_disconnect_missing_connection_raises(self) -> None:
        # Load the sample graph before attempting the mutation.
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        # A missing connection should raise ValueError.
        with self.assertRaises(ValueError):
            session.disconnect("does_not_exist", 0, "qtgui_time_sink_x_0", 0)

    # Saving a disconnect and reloading it should preserve the removed wire on disk.
    def test_disconnect_persists_after_save_and_reload(self) -> None:
        # Load the sample graph before mutating it.
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        # Remove one connection before writing the graph back to disk.
        session.disconnect("blocks_throttle2_0", 0, "blocks_char_to_float_0", 0)

        # Save into a temporary directory so the repository stays unchanged.
        with tempfile.TemporaryDirectory() as tmpdir:
            # Build a separate file name so we know this is the disconnected copy.
            save_path = Path(tmpdir) / "disconnected_random_bit_generator.grc"
            # Write the mutated graph to disk.
            session.save(save_path)

            # Load the saved graph into a fresh session to prove the disconnect persisted.
            reloaded = FlowgraphSession()
            reloaded.load(save_path)

            # The reloaded session should have a parsed flowgraph.
            self.assertIsNotNone(reloaded.flowgraph)
            assert reloaded.flowgraph is not None

            # The saved file should now contain only two connections.
            self.assertEqual(len(reloaded.flowgraph.connections), 2)

    # Connecting a known wire should update both the model and raw YAML.
    def test_connect_updates_model_and_raw_data(self) -> None:
        # Load the sample graph so there is room to add a new connection.
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        # Add a direct wire from the random source to the time sink.
        session.connect("analog_random_source_x_0", 0, "qtgui_time_sink_x_0", 0)

        # The session should now be marked dirty because it differs from disk.
        self.assertTrue(session.is_dirty)

        # Pull out the parsed flowgraph so we can inspect the updated graph.
        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None

        # One connection should have been added to the parsed model.
        self.assertEqual(len(flowgraph.connections), 4)
        # One connection should also have been added to the raw YAML.
        self.assertEqual(len(flowgraph.raw_data["connections"]), 4)

        # Confirm the new wire is present in the parsed model.
        self.assertIn(
            ("analog_random_source_x_0", 0, "qtgui_time_sink_x_0", 0),
            [
                (
                    connection.src_block,
                    connection.src_port,
                    connection.dst_block,
                    connection.dst_port,
                )
                for connection in flowgraph.connections
            ],
        )

    # Asking to connect an existing wire should raise a clear error.
    def test_connect_duplicate_connection_raises(self) -> None:
        # Load the sample graph before attempting the mutation.
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        # A duplicate connection should raise ValueError.
        with self.assertRaises(ValueError):
            session.connect("blocks_throttle2_0", 0, "blocks_char_to_float_0", 0)

    # Saving a connection mutation and reloading it should preserve the new wire on disk.
    def test_connect_persists_after_save_and_reload(self) -> None:
        # Load the sample graph before mutating it.
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        # Add a new connection before writing the graph back to disk.
        session.connect("analog_random_source_x_0", 0, "qtgui_time_sink_x_0", 0)

        # Save into a temporary directory so the repository stays unchanged.
        with tempfile.TemporaryDirectory() as tmpdir:
            # Build a separate file name so we know this is the connected copy.
            save_path = Path(tmpdir) / "connected_random_bit_generator.grc"
            # Write the mutated graph to disk.
            session.save(save_path)

            # Load the saved graph into a fresh session to prove the connection persisted.
            reloaded = FlowgraphSession()
            reloaded.load(save_path)

            # The reloaded session should have a parsed flowgraph.
            self.assertIsNotNone(reloaded.flowgraph)
            assert reloaded.flowgraph is not None

            # The saved file should now contain the new wire.
            self.assertIn(
                ("analog_random_source_x_0", 0, "qtgui_time_sink_x_0", 0),
                [
                    (
                        connection.src_block,
                        connection.src_port,
                        connection.dst_block,
                        connection.dst_port,
                    )
                    for connection in reloaded.flowgraph.connections
                ],
            )

    # Adding a detached variable block should update both the model and raw YAML.
    def test_add_block_updates_model_and_raw_data_with_generated_states(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        session.add_block("unused_var", "variable", {"value": "123"})

        self.assertTrue(session.is_dirty)
        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None

        block = next(block for block in flowgraph.blocks if block.instance_name == "unused_var")
        self.assertEqual(block.block_type, "variable")
        self.assertEqual(block.params["parameters"]["value"], "123")
        self.assertEqual(block.params["parameters"]["comment"], "")
        self.assertEqual(block.params["states"]["rotation"], 0)
        self.assertEqual(block.params["states"]["state"], "enabled")
        self.assertIn("coordinate", block.params["states"])

        raw_block = next(
            entry for entry in flowgraph.raw_data["blocks"] if entry["name"] == "unused_var"
        )
        self.assertEqual(raw_block["parameters"]["value"], "123")
        self.assertEqual(raw_block["parameters"]["comment"], "")
        self.assertEqual(set(raw_block["states"].keys()), {"coordinate", "rotation", "state"})

        self.assertTrue(session.validate())

    # Saving after add_block() should keep the new variable block present on reload.
    def test_add_block_persists_after_save_and_reload(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)
        session.add_block("unused_var", "variable", {"value": "123"})

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "with_added_variable.grc"
            session.save(save_path)

            reloaded = FlowgraphSession()
            reloaded.load(save_path)

            self.assertIsNotNone(reloaded.flowgraph)
            assert reloaded.flowgraph is not None
            block = next(
                block for block in reloaded.flowgraph.blocks if block.instance_name == "unused_var"
            )
            self.assertEqual(block.block_type, "variable")
            self.assertEqual(block.params["parameters"]["value"], "123")
            self.assertEqual(block.params["parameters"]["comment"], "")

    # add_block() should reject duplicate instance names before mutating the graph.
    def test_add_block_duplicate_name_raises(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        with self.assertRaises(ValueError):
            session.add_block("samp_rate", "variable", {"value": "123"})

    # The first implementation supports detached variable blocks only.
    def test_add_block_unsupported_block_type_raises(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        with self.assertRaises(ValueError):
            session.add_block("blocks_char_to_float_1", "blocks_char_to_float", {"value": "1"})

    # Variable blocks require a value expression before candidate validation runs.
    def test_add_block_missing_value_raises(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        with self.assertRaises(ValueError):
            session.add_block("unused_var", "variable", {"comment": ""})

    # Candidate-validation failures must leave the loaded session unchanged.
    def test_add_block_invalid_expression_rolls_back_without_mutation(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None
        original_block_count = len(flowgraph.blocks)
        original_raw_block_count = len(flowgraph.raw_data["blocks"])

        with self.assertRaises(ValueError):
            session.add_block("bad_var", "variable", {"value": "missing_rate + 1"})

        self.assertFalse(session.is_dirty)
        self.assertEqual(len(flowgraph.blocks), original_block_count)
        self.assertEqual(len(flowgraph.raw_data["blocks"]), original_raw_block_count)
        self.assertNotIn("bad_var", [block.instance_name for block in flowgraph.blocks])
        self.assertIsNone(session.last_validation_stdout)
        self.assertIsNone(session.last_validation_stderr)
        self.assertIsNone(session.last_validation_returncode)

    # Adding and connecting a copied sink block should update both model and raw YAML.
    def test_add_and_connect_qtgui_time_sink_updates_model_and_raw_data(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        parameters = self._fixture_block_parameters("qtgui_time_sink_x_0")
        session.add_and_connect_qtgui_time_sink(
            "qtgui_time_sink_x_1",
            parameters,
            "blocks_char_to_float_0",
            0,
        )

        self.assertTrue(session.is_dirty)
        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None

        block = next(block for block in flowgraph.blocks if block.instance_name == "qtgui_time_sink_x_1")
        self.assertEqual(block.block_type, "qtgui_time_sink_x")
        self.assertEqual(block.params["parameters"]["nconnections"], "1")
        self.assertEqual(block.params["states"]["rotation"], 0)
        self.assertEqual(block.params["states"]["state"], "enabled")
        self.assertIn("coordinate", block.params["states"])

        self.assertIn(
            ("blocks_char_to_float_0", 0, "qtgui_time_sink_x_1", 0),
            [
                (
                    connection.src_block,
                    connection.src_port,
                    connection.dst_block,
                    connection.dst_port,
                )
                for connection in flowgraph.connections
            ],
        )

        raw_block = next(
            entry for entry in flowgraph.raw_data["blocks"] if entry["name"] == "qtgui_time_sink_x_1"
        )
        self.assertEqual(raw_block["id"], "qtgui_time_sink_x")
        self.assertEqual(set(raw_block["states"].keys()), {"coordinate", "rotation", "state"})
        self.assertIn(
            ["blocks_char_to_float_0", "0", "qtgui_time_sink_x_1", "0"],
            flowgraph.raw_data["connections"],
        )

        self.assertTrue(session.validate())

    # Saving after sink add-plus-connect should keep the new block and wire present on reload.
    def test_add_and_connect_qtgui_time_sink_persists_after_save_and_reload(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        parameters = self._fixture_block_parameters("qtgui_time_sink_x_0")
        session.add_and_connect_qtgui_time_sink(
            "qtgui_time_sink_x_1",
            parameters,
            "blocks_char_to_float_0",
            0,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "with_added_sink.grc"
            session.save(save_path)

            reloaded = FlowgraphSession()
            reloaded.load(save_path)

            self.assertIsNotNone(reloaded.flowgraph)
            assert reloaded.flowgraph is not None
            self.assertIn(
                "qtgui_time_sink_x_1",
                [block.instance_name for block in reloaded.flowgraph.blocks],
            )
            self.assertIn(
                ("blocks_char_to_float_0", 0, "qtgui_time_sink_x_1", 0),
                [
                    (
                        connection.src_block,
                        connection.src_port,
                        connection.dst_block,
                        connection.dst_port,
                    )
                    for connection in reloaded.flowgraph.connections
                ],
            )

    # Duplicate sink block names should be rejected before any mutation happens.
    def test_add_and_connect_qtgui_time_sink_duplicate_name_raises(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        parameters = self._fixture_block_parameters("qtgui_time_sink_x_0")
        with self.assertRaises(ValueError):
            session.add_and_connect_qtgui_time_sink(
                "qtgui_time_sink_x_0",
                parameters,
                "blocks_char_to_float_0",
                0,
            )

    # The source endpoint must exist before the candidate graph is built.
    def test_add_and_connect_qtgui_time_sink_missing_source_block_raises(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        parameters = self._fixture_block_parameters("qtgui_time_sink_x_0")
        with self.assertRaises(ValueError):
            session.add_and_connect_qtgui_time_sink(
                "qtgui_time_sink_x_1",
                parameters,
                "missing_block",
                0,
            )

    # Candidate-validation failures must leave the loaded session unchanged.
    def test_add_and_connect_qtgui_time_sink_invalid_candidate_rolls_back(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        parameters = self._fixture_block_parameters("qtgui_time_sink_x_0")
        parameters["srate"] = "missing_rate"

        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None
        original_block_count = len(flowgraph.blocks)
        original_connection_count = len(flowgraph.connections)
        original_raw_block_count = len(flowgraph.raw_data["blocks"])
        original_raw_connection_count = len(flowgraph.raw_data["connections"])

        with self.assertRaises(ValueError):
            session.add_and_connect_qtgui_time_sink(
                "qtgui_time_sink_x_1",
                parameters,
                "blocks_char_to_float_0",
                0,
            )

        self.assertFalse(session.is_dirty)
        self.assertEqual(len(flowgraph.blocks), original_block_count)
        self.assertEqual(len(flowgraph.connections), original_connection_count)
        self.assertEqual(len(flowgraph.raw_data["blocks"]), original_raw_block_count)
        self.assertEqual(len(flowgraph.raw_data["connections"]), original_raw_connection_count)
        self.assertNotIn(
            "qtgui_time_sink_x_1",
            [block.instance_name for block in flowgraph.blocks],
        )
        self.assertIsNone(session.last_validation_stdout)
        self.assertIsNone(session.last_validation_stderr)
        self.assertIsNone(session.last_validation_returncode)

    # Adding a transform tap should update the model, the sink config, and raw YAML together.
    def test_add_and_connect_char_to_float_to_qtgui_time_sink_updates_model_and_raw_data(
        self,
    ) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        parameters = self._fixture_block_parameters("blocks_char_to_float_0")
        session.add_and_connect_char_to_float_to_qtgui_time_sink(
            "blocks_char_to_float_1",
            parameters,
            "blocks_throttle2_0",
            0,
            "qtgui_time_sink_x_0",
        )

        self.assertTrue(session.is_dirty)
        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None

        block = next(
            block for block in flowgraph.blocks if block.instance_name == "blocks_char_to_float_1"
        )
        self.assertEqual(block.block_type, "blocks_char_to_float")
        self.assertEqual(block.params["parameters"]["scale"], "1")
        self.assertEqual(block.params["states"]["rotation"], 0)
        self.assertEqual(block.params["states"]["state"], "enabled")
        self.assertIn("coordinate", block.params["states"])

        sink = next(block for block in flowgraph.blocks if block.instance_name == "qtgui_time_sink_x_0")
        self.assertEqual(sink.params["parameters"]["nconnections"], "2")

        self.assertIn(
            ("blocks_throttle2_0", 0, "blocks_char_to_float_1", 0),
            [
                (
                    connection.src_block,
                    connection.src_port,
                    connection.dst_block,
                    connection.dst_port,
                )
                for connection in flowgraph.connections
            ],
        )
        self.assertIn(
            ("blocks_char_to_float_1", 0, "qtgui_time_sink_x_0", 1),
            [
                (
                    connection.src_block,
                    connection.src_port,
                    connection.dst_block,
                    connection.dst_port,
                )
                for connection in flowgraph.connections
            ],
        )

        raw_block = next(
            entry for entry in flowgraph.raw_data["blocks"] if entry["name"] == "blocks_char_to_float_1"
        )
        self.assertEqual(raw_block["id"], "blocks_char_to_float")
        self.assertEqual(set(raw_block["states"].keys()), {"coordinate", "rotation", "state"})

        raw_sink = next(
            entry for entry in flowgraph.raw_data["blocks"] if entry["name"] == "qtgui_time_sink_x_0"
        )
        self.assertEqual(raw_sink["parameters"]["nconnections"], "2")
        self.assertIn(
            ["blocks_throttle2_0", "0", "blocks_char_to_float_1", "0"],
            flowgraph.raw_data["connections"],
        )
        self.assertIn(
            ["blocks_char_to_float_1", "0", "qtgui_time_sink_x_0", "1"],
            flowgraph.raw_data["connections"],
        )

        self.assertTrue(session.validate())

    # Saving after a coordinated transform add should keep the new block and sink input on reload.
    def test_add_and_connect_char_to_float_to_qtgui_time_sink_persists_after_save_and_reload(
        self,
    ) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        parameters = self._fixture_block_parameters("blocks_char_to_float_0")
        session.add_and_connect_char_to_float_to_qtgui_time_sink(
            "blocks_char_to_float_1",
            parameters,
            "blocks_throttle2_0",
            0,
            "qtgui_time_sink_x_0",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "with_added_transform_tap.grc"
            session.save(save_path)

            reloaded = FlowgraphSession()
            reloaded.load(save_path)

            self.assertIsNotNone(reloaded.flowgraph)
            assert reloaded.flowgraph is not None
            self.assertIn(
                "blocks_char_to_float_1",
                [block.instance_name for block in reloaded.flowgraph.blocks],
            )
            sink = next(
                block
                for block in reloaded.flowgraph.blocks
                if block.instance_name == "qtgui_time_sink_x_0"
            )
            self.assertEqual(sink.params["parameters"]["nconnections"], "2")
            self.assertIn(
                ("blocks_char_to_float_1", 0, "qtgui_time_sink_x_0", 1),
                [
                    (
                        connection.src_block,
                        connection.src_port,
                        connection.dst_block,
                        connection.dst_port,
                    )
                    for connection in reloaded.flowgraph.connections
                ],
            )

    # Duplicate transform block names should be rejected before any mutation happens.
    def test_add_and_connect_char_to_float_to_qtgui_time_sink_duplicate_name_raises(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        parameters = self._fixture_block_parameters("blocks_char_to_float_0")
        with self.assertRaises(ValueError):
            session.add_and_connect_char_to_float_to_qtgui_time_sink(
                "blocks_char_to_float_0",
                parameters,
                "blocks_throttle2_0",
                0,
                "qtgui_time_sink_x_0",
            )

    # The source endpoint must exist before the coordinated candidate graph is built.
    def test_add_and_connect_char_to_float_to_qtgui_time_sink_missing_source_block_raises(
        self,
    ) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        parameters = self._fixture_block_parameters("blocks_char_to_float_0")
        with self.assertRaises(ValueError):
            session.add_and_connect_char_to_float_to_qtgui_time_sink(
                "blocks_char_to_float_1",
                parameters,
                "missing_block",
                0,
                "qtgui_time_sink_x_0",
            )

    # The coordinated workflow remains specific to an existing qtgui_time_sink_x block.
    def test_add_and_connect_char_to_float_to_qtgui_time_sink_rejects_non_qtgui_sink(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        parameters = self._fixture_block_parameters("blocks_char_to_float_0")
        with self.assertRaises(ValueError):
            session.add_and_connect_char_to_float_to_qtgui_time_sink(
                "blocks_char_to_float_1",
                parameters,
                "blocks_throttle2_0",
                0,
                "blocks_throttle2_0",
            )

    # Candidate-validation failures must leave the loaded session unchanged.
    def test_add_and_connect_char_to_float_to_qtgui_time_sink_invalid_candidate_rolls_back(
        self,
    ) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        parameters = self._fixture_block_parameters("blocks_char_to_float_0")
        parameters["scale"] = "missing_rate"

        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None
        original_block_count = len(flowgraph.blocks)
        original_connection_count = len(flowgraph.connections)
        original_raw_block_count = len(flowgraph.raw_data["blocks"])
        original_raw_connection_count = len(flowgraph.raw_data["connections"])
        sink = next(block for block in flowgraph.blocks if block.instance_name == "qtgui_time_sink_x_0")
        original_nconnections = sink.params["parameters"]["nconnections"]

        with self.assertRaises(ValueError):
            session.add_and_connect_char_to_float_to_qtgui_time_sink(
                "blocks_char_to_float_1",
                parameters,
                "blocks_throttle2_0",
                0,
                "qtgui_time_sink_x_0",
            )

        self.assertFalse(session.is_dirty)
        self.assertEqual(len(flowgraph.blocks), original_block_count)
        self.assertEqual(len(flowgraph.connections), original_connection_count)
        self.assertEqual(len(flowgraph.raw_data["blocks"]), original_raw_block_count)
        self.assertEqual(len(flowgraph.raw_data["connections"]), original_raw_connection_count)
        self.assertEqual(sink.params["parameters"]["nconnections"], original_nconnections)
        self.assertNotIn(
            "blocks_char_to_float_1",
            [block.instance_name for block in flowgraph.blocks],
        )
        self.assertIsNone(session.last_validation_stdout)
        self.assertIsNone(session.last_validation_stderr)
        self.assertIsNone(session.last_validation_returncode)

    # Adding the smallest passing source pipeline should update model, sink config, and raw YAML together.
    def test_add_and_connect_analog_random_source_to_qtgui_time_sink_updates_model_and_raw_data(
        self,
    ) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        source_parameters = self._fixture_block_parameters("analog_random_source_x_0")
        transform_parameters = self._fixture_block_parameters("blocks_char_to_float_0")
        session.add_and_connect_analog_random_source_to_qtgui_time_sink(
            "analog_random_source_x_1",
            source_parameters,
            "blocks_char_to_float_1",
            transform_parameters,
            "qtgui_time_sink_x_0",
        )

        self.assertTrue(session.is_dirty)
        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None

        source_block = next(
            block for block in flowgraph.blocks if block.instance_name == "analog_random_source_x_1"
        )
        self.assertEqual(source_block.block_type, "analog_random_source_x")
        self.assertEqual(source_block.params["parameters"]["type"], "byte")
        self.assertEqual(source_block.params["states"]["rotation"], 0)
        self.assertEqual(source_block.params["states"]["state"], "enabled")
        self.assertIn("coordinate", source_block.params["states"])

        transform_block = next(
            block for block in flowgraph.blocks if block.instance_name == "blocks_char_to_float_1"
        )
        self.assertEqual(transform_block.block_type, "blocks_char_to_float")
        self.assertEqual(transform_block.params["parameters"]["scale"], "1")
        self.assertEqual(transform_block.params["states"]["rotation"], 0)
        self.assertEqual(transform_block.params["states"]["state"], "enabled")
        self.assertIn("coordinate", transform_block.params["states"])

        sink = next(block for block in flowgraph.blocks if block.instance_name == "qtgui_time_sink_x_0")
        self.assertEqual(sink.params["parameters"]["nconnections"], "2")

        self.assertIn(
            ("analog_random_source_x_1", 0, "blocks_char_to_float_1", 0),
            [
                (
                    connection.src_block,
                    connection.src_port,
                    connection.dst_block,
                    connection.dst_port,
                )
                for connection in flowgraph.connections
            ],
        )
        self.assertIn(
            ("blocks_char_to_float_1", 0, "qtgui_time_sink_x_0", 1),
            [
                (
                    connection.src_block,
                    connection.src_port,
                    connection.dst_block,
                    connection.dst_port,
                )
                for connection in flowgraph.connections
            ],
        )

        raw_source = next(
            entry for entry in flowgraph.raw_data["blocks"] if entry["name"] == "analog_random_source_x_1"
        )
        self.assertEqual(raw_source["id"], "analog_random_source_x")
        self.assertEqual(set(raw_source["states"].keys()), {"coordinate", "rotation", "state"})

        raw_transform = next(
            entry for entry in flowgraph.raw_data["blocks"] if entry["name"] == "blocks_char_to_float_1"
        )
        self.assertEqual(raw_transform["id"], "blocks_char_to_float")
        self.assertEqual(set(raw_transform["states"].keys()), {"coordinate", "rotation", "state"})

        raw_sink = next(
            entry for entry in flowgraph.raw_data["blocks"] if entry["name"] == "qtgui_time_sink_x_0"
        )
        self.assertEqual(raw_sink["parameters"]["nconnections"], "2")
        self.assertIn(
            ["analog_random_source_x_1", "0", "blocks_char_to_float_1", "0"],
            flowgraph.raw_data["connections"],
        )
        self.assertIn(
            ["blocks_char_to_float_1", "0", "qtgui_time_sink_x_0", "1"],
            flowgraph.raw_data["connections"],
        )

        self.assertTrue(session.validate())

    # Saving after a source-pipeline add should keep the new blocks and sink input on reload.
    def test_add_and_connect_analog_random_source_to_qtgui_time_sink_persists_after_save_and_reload(
        self,
    ) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        source_parameters = self._fixture_block_parameters("analog_random_source_x_0")
        transform_parameters = self._fixture_block_parameters("blocks_char_to_float_0")
        session.add_and_connect_analog_random_source_to_qtgui_time_sink(
            "analog_random_source_x_1",
            source_parameters,
            "blocks_char_to_float_1",
            transform_parameters,
            "qtgui_time_sink_x_0",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = Path(tmpdir) / "with_added_source_pipeline.grc"
            session.save(save_path)

            reloaded = FlowgraphSession()
            reloaded.load(save_path)

            self.assertIsNotNone(reloaded.flowgraph)
            assert reloaded.flowgraph is not None
            self.assertIn(
                "analog_random_source_x_1",
                [block.instance_name for block in reloaded.flowgraph.blocks],
            )
            self.assertIn(
                "blocks_char_to_float_1",
                [block.instance_name for block in reloaded.flowgraph.blocks],
            )
            sink = next(
                block
                for block in reloaded.flowgraph.blocks
                if block.instance_name == "qtgui_time_sink_x_0"
            )
            self.assertEqual(sink.params["parameters"]["nconnections"], "2")
            self.assertIn(
                ("blocks_char_to_float_1", 0, "qtgui_time_sink_x_0", 1),
                [
                    (
                        connection.src_block,
                        connection.src_port,
                        connection.dst_block,
                        connection.dst_port,
                    )
                    for connection in reloaded.flowgraph.connections
                ],
            )

    # Duplicate source block names should be rejected before any mutation happens.
    def test_add_and_connect_analog_random_source_to_qtgui_time_sink_duplicate_source_name_raises(
        self,
    ) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        source_parameters = self._fixture_block_parameters("analog_random_source_x_0")
        transform_parameters = self._fixture_block_parameters("blocks_char_to_float_0")
        with self.assertRaises(ValueError):
            session.add_and_connect_analog_random_source_to_qtgui_time_sink(
                "analog_random_source_x_0",
                source_parameters,
                "blocks_char_to_float_1",
                transform_parameters,
                "qtgui_time_sink_x_0",
            )

    # The source workflow remains specific to an existing qtgui_time_sink_x block.
    def test_add_and_connect_analog_random_source_to_qtgui_time_sink_rejects_non_qtgui_sink(
        self,
    ) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        source_parameters = self._fixture_block_parameters("analog_random_source_x_0")
        transform_parameters = self._fixture_block_parameters("blocks_char_to_float_0")
        with self.assertRaises(ValueError):
            session.add_and_connect_analog_random_source_to_qtgui_time_sink(
                "analog_random_source_x_1",
                source_parameters,
                "blocks_char_to_float_1",
                transform_parameters,
                "blocks_throttle2_0",
            )

    # The destination sink must exist before the coordinated candidate graph is built.
    def test_add_and_connect_analog_random_source_to_qtgui_time_sink_missing_sink_block_raises(
        self,
    ) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        source_parameters = self._fixture_block_parameters("analog_random_source_x_0")
        transform_parameters = self._fixture_block_parameters("blocks_char_to_float_0")
        with self.assertRaises(ValueError):
            session.add_and_connect_analog_random_source_to_qtgui_time_sink(
                "analog_random_source_x_1",
                source_parameters,
                "blocks_char_to_float_1",
                transform_parameters,
                "missing_sink",
            )

    # Candidate-validation failures must leave the loaded session unchanged.
    def test_add_and_connect_analog_random_source_to_qtgui_time_sink_invalid_candidate_rolls_back(
        self,
    ) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        source_parameters = self._fixture_block_parameters("analog_random_source_x_0")
        source_parameters["max"] = "missing_rate"
        transform_parameters = self._fixture_block_parameters("blocks_char_to_float_0")

        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)
        assert flowgraph is not None
        original_block_count = len(flowgraph.blocks)
        original_connection_count = len(flowgraph.connections)
        original_raw_block_count = len(flowgraph.raw_data["blocks"])
        original_raw_connection_count = len(flowgraph.raw_data["connections"])
        sink = next(block for block in flowgraph.blocks if block.instance_name == "qtgui_time_sink_x_0")
        original_nconnections = sink.params["parameters"]["nconnections"]

        with self.assertRaises(ValueError):
            session.add_and_connect_analog_random_source_to_qtgui_time_sink(
                "analog_random_source_x_1",
                source_parameters,
                "blocks_char_to_float_1",
                transform_parameters,
                "qtgui_time_sink_x_0",
            )

        self.assertFalse(session.is_dirty)
        self.assertEqual(len(flowgraph.blocks), original_block_count)
        self.assertEqual(len(flowgraph.connections), original_connection_count)
        self.assertEqual(len(flowgraph.raw_data["blocks"]), original_raw_block_count)
        self.assertEqual(len(flowgraph.raw_data["connections"]), original_raw_connection_count)
        self.assertEqual(sink.params["parameters"]["nconnections"], original_nconnections)
        self.assertNotIn(
            "analog_random_source_x_1",
            [block.instance_name for block in flowgraph.blocks],
        )
        self.assertNotIn(
            "blocks_char_to_float_1",
            [block.instance_name for block in flowgraph.blocks],
        )
        self.assertIsNone(session.last_validation_stdout)
        self.assertIsNone(session.last_validation_stderr)
        self.assertIsNone(session.last_validation_returncode)

    # Removing a detached, unreferenced zero-port block should update model and raw YAML.
    def test_remove_block_updates_model_and_raw_data_for_detached_block(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block())

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_path = self._write_temp_graph(tmpdir, raw_data, "with_unused_var.grc")
            session = FlowgraphSession()
            session.load(fixture_path)

            session.remove_block("unused_var")

            self.assertTrue(session.is_dirty)
            flowgraph = session.flowgraph
            self.assertIsNotNone(flowgraph)
            assert flowgraph is not None
            self.assertNotIn(
                "unused_var",
                [block.instance_name for block in flowgraph.blocks],
            )
            self.assertNotIn(
                "unused_var",
                [entry["name"] for entry in flowgraph.raw_data["blocks"]],
            )

    # Removing a missing block should raise a clear error.
    def test_remove_block_missing_block_raises(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        with self.assertRaises(ValueError):
            session.remove_block("does_not_exist")

    # Removing a block with attached connections is rejected by the first contract.
    def test_remove_block_connected_block_raises(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        with self.assertRaises(ValueError):
            session.remove_block("blocks_throttle2_0")

    # Removing a block name that is still used in parameter expressions is rejected.
    def test_remove_block_referenced_block_raises(self) -> None:
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        with self.assertRaises(ValueError):
            session.remove_block("samp_rate")

    # Saving after a detached block removal should keep the block absent on reload.
    def test_remove_block_persists_after_save_and_reload(self) -> None:
        raw_data = self._fixture_raw_data()
        raw_data["blocks"].append(self._detached_variable_block())

        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_path = self._write_temp_graph(tmpdir, raw_data, "with_unused_var.grc")
            session = FlowgraphSession()
            session.load(fixture_path)
            session.remove_block("unused_var")

            save_path = Path(tmpdir) / "without_unused_var.grc"
            session.save(save_path)

            reloaded = FlowgraphSession()
            reloaded.load(save_path)

            self.assertIsNotNone(reloaded.flowgraph)
            assert reloaded.flowgraph is not None
            self.assertNotIn(
                "unused_var",
                [block.instance_name for block in reloaded.flowgraph.blocks],
            )

    # Saving a mutation and reloading it should preserve the new value on disk.
    def test_set_param_persists_after_save_and_reload(self) -> None:
        # Load the sample graph so there is something to mutate and save.
        fixture_path = self._fixture_path()
        session = FlowgraphSession()
        session.load(fixture_path)

        # Change the sample rate before writing the graph back to disk.
        session.set_param("samp_rate", "value", "48000")

        # Save into a temporary directory so the repository stays untouched.
        with tempfile.TemporaryDirectory() as tmpdir:
            # Build a separate file name so we know this is the mutated copy.
            save_path = Path(tmpdir) / "mutated_random_bit_generator.grc"
            # Write the mutated graph to disk.
            session.save(save_path)

            # Load the saved graph into a fresh session to prove the change persisted.
            reloaded = FlowgraphSession()
            reloaded.load(save_path)

            # The reloaded session should have a parsed flowgraph.
            self.assertIsNotNone(reloaded.flowgraph)
            assert reloaded.flowgraph is not None

            # Find the same block in the reloaded graph.
            block = next(
                block for block in reloaded.flowgraph.blocks if block.instance_name == "samp_rate"
            )
            # The saved file should contain the mutated value.
            self.assertEqual(block.params["parameters"]["value"], "48000")