# Path helps us build a test fixture location relative to this file.
from pathlib import Path
# tempfile gives us isolated directories for save tests.
import tempfile
# unittest is the standard library test framework.
import unittest

# Import the class we want to exercise.
from grc_agent.flowgraph_session import FlowgraphSession


class FlowgraphSessionTests(unittest.TestCase):
    """Tests for loading, saving, and validating .grc flowgraphs."""

    def _fixture_path(self) -> Path:
        # __file__ is the path to this test file.
        test_directory = Path(__file__).resolve().parent
        # The copied sample graph lives under tests/data.
        return test_directory / "data" / "random_bit_generator.grc"

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