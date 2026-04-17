"""Direct tests for the lightweight flowgraph dataclasses."""

import unittest

from grc_agent import Block, Connection, Flowgraph


class ModelDataclassTests(unittest.TestCase):
    """Exercise direct construction and mutation of the public dataclasses."""

    def test_block_preserves_fields_and_nested_params(self) -> None:
        block = Block(
            instance_name="samp_rate",
            block_type="variable",
            params={"parameters": {"value": "32000"}},
        )

        self.assertEqual(block.instance_name, "samp_rate")
        self.assertEqual(block.block_type, "variable")
        self.assertEqual(block.params["parameters"]["value"], "32000")

        block.params["parameters"]["value"] = "48000"
        self.assertEqual(block.params["parameters"]["value"], "48000")

    def test_connection_preserves_endpoint_fields(self) -> None:
        connection = Connection(
            src_block="src",
            src_port=0,
            dst_block="dst",
            dst_port=1,
        )

        self.assertEqual(connection.src_block, "src")
        self.assertEqual(connection.src_port, 0)
        self.assertEqual(connection.dst_block, "dst")
        self.assertEqual(connection.dst_port, 1)

    def test_flowgraph_defaults_are_empty_and_mutable(self) -> None:
        flowgraph = Flowgraph()

        self.assertEqual(flowgraph.blocks, [])
        self.assertEqual(flowgraph.connections, [])
        self.assertEqual(flowgraph.metadata, {})
        self.assertEqual(flowgraph.raw_data, {})

        flowgraph.blocks.append(Block("clock", "variable", {"parameters": {"value": "1"}}))
        flowgraph.connections.append(Connection("src", 0, "dst", 0))
        flowgraph.metadata["options"] = {"id": "demo"}
        flowgraph.raw_data["blocks"] = [{"name": "clock", "id": "variable"}]

        self.assertEqual(flowgraph.blocks[0].instance_name, "clock")
        self.assertEqual(flowgraph.connections[0].dst_block, "dst")
        self.assertEqual(flowgraph.metadata["options"]["id"], "demo")
        self.assertEqual(flowgraph.raw_data["blocks"][0]["name"], "clock")


if __name__ == "__main__":
    unittest.main()
