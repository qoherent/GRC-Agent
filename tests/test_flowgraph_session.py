from pathlib import Path
import unittest

from grc_agent.flowgraph_session import FlowgraphSession


class FlowgraphSessionTests(unittest.TestCase):
    def test_load_and_summarize_random_bit_generator(self) -> None:
        fixture_path = Path(__file__).resolve().parent / "data" / "random_bit_generator.grc"

        session = FlowgraphSession()
        session.load(fixture_path)

        self.assertEqual(session.path, fixture_path)
        self.assertFalse(session.is_dirty)
        self.assertIsNotNone(session.flowgraph)

        flowgraph = session.flowgraph
        self.assertIsNotNone(flowgraph)

        assert flowgraph is not None

        self.assertEqual(len(flowgraph.blocks), 5)
        self.assertEqual(len(flowgraph.connections), 3)
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

        summary = session.summarize()
        self.assertIn("random_bit_generator.grc", summary)
        self.assertIn("Blocks: 5", summary)
        self.assertIn("Connections: 3", summary)
        self.assertIn("- samp_rate (variable)", summary)
        self.assertIn("- qtgui_time_sink_x_0 (qtgui_time_sink_x)", summary)