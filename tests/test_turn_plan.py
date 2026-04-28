"""Tests for typed turn planning and route policy."""

from __future__ import annotations

import unittest

from grc_agent.runtime.turn_plan import (
    INTENT_PARAM_EDIT,
    INTENT_REMOVE_BLOCK,
    INTENT_STATE_EDIT,
    build_turn_plan,
)
from grc_agent.runtime.tool_schemas import PUBLIC_TOOL_NAMES


class TurnPlanTests(unittest.TestCase):
    def test_disable_block_maps_to_state_edit(self) -> None:
        plan = build_turn_plan("Disable blocks_message_debug_0, then validate it.")

        self.assertEqual(plan.intent, INTENT_STATE_EDIT)
        self.assertEqual(plan.expected_op_types, ("update_states",))
        self.assertIn("apply_edit", plan.allowed_tools)
        self.assertIn("validate_graph", plan.allowed_tools)
        self.assertIn("apply_edit", plan.required_actions)
        self.assertIn("validate_graph", plan.required_actions)

    def test_do_not_remove_disable_it_maps_to_state_edit(self) -> None:
        plan = build_turn_plan("Do not remove blocks_message_debug_0, disable it.")

        self.assertEqual(plan.intent, INTENT_STATE_EDIT)
        self.assertEqual(plan.expected_op_types, ("update_states",))
        self.assertNotIn("remove_connection", plan.allowed_tools)

    def test_remove_block_maps_to_remove_block(self) -> None:
        plan = build_turn_plan("Remove the blocks_message_debug_0 block.")

        self.assertEqual(plan.intent, INTENT_REMOVE_BLOCK)
        self.assertEqual(plan.expected_op_types, ("remove_block",))
        self.assertEqual(plan.allowed_tools, ("apply_edit",))

    def test_parameter_change_maps_to_param_edit(self) -> None:
        plan = build_turn_plan("Change samp_rate to 48000 and save it.")

        self.assertEqual(plan.intent, INTENT_PARAM_EDIT)
        self.assertEqual(plan.expected_op_types, ("update_params",))
        self.assertEqual(plan.allowed_tools, ("apply_edit", "save_graph"))

    def test_unknown_intent_keeps_full_tool_surface(self) -> None:
        plan = build_turn_plan("What GNU Radio block should I use for spectrum display?")

        self.assertEqual(plan.allowed_tools, PUBLIC_TOOL_NAMES)
        self.assertEqual(plan.expected_op_types, ())


if __name__ == "__main__":
    unittest.main()
