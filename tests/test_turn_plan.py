"""Tests for typed turn planning and route policy."""

from __future__ import annotations

import unittest

from grc_agent.runtime.turn_plan import (
    DEFAULT_TURN_TOOLS,
    INTENT_DISCONNECT,
    INTENT_PARAM_EDIT,
    INTENT_AMBIGUOUS,
    INTENT_INSERTION,
    INTENT_LOAD,
    INTENT_ADD_VARIABLE,
    INTENT_REMOVE_BLOCK,
    INTENT_REWIRE,
    INTENT_STATE_EDIT,
    INTENT_UNCERTAIN_MUTATION,
    UNCERTAIN_MUTATION_TOOLS,
    build_turn_plan,
)


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
        self.assertEqual(plan.expected_op_types, ())
        self.assertEqual(plan.allowed_tools, ("apply_edit",))

    def test_delete_disabled_block_maps_to_remove_block(self) -> None:
        plan = build_turn_plan("Delete the disabled block blocks_message_debug_0.")

        self.assertEqual(plan.intent, INTENT_REMOVE_BLOCK)
        self.assertEqual(plan.expected_op_types, ())

    def test_disable_then_remove_requires_clarification(self) -> None:
        plan = build_turn_plan("Disable blocks_message_debug_0 and remove it.")

        self.assertEqual(plan.intent, INTENT_AMBIGUOUS)
        self.assertTrue(plan.requires_clarification)
        self.assertEqual(plan.allowed_tools, ())

    def test_parameter_change_maps_to_param_edit(self) -> None:
        plan = build_turn_plan("Change samp_rate to 48000 and save it.")

        self.assertEqual(plan.intent, INTENT_PARAM_EDIT)
        self.assertEqual(plan.expected_op_types, ("update_params",))
        self.assertEqual(plan.allowed_tools, DEFAULT_TURN_TOOLS)
        self.assertIn("apply_edit", plan.required_actions)
        self.assertIn("save_graph", plan.required_actions)

    def test_add_variable_is_not_treated_as_param_update(self) -> None:
        plan = build_turn_plan("Add a variable called noise_level set to 0.1.")

        self.assertEqual(plan.intent, INTENT_ADD_VARIABLE)
        self.assertEqual(plan.expected_op_types, ("add_block",))
        self.assertEqual(plan.allowed_tools, ("apply_edit",))
        self.assertEqual(plan.required_actions, ("apply_edit",))

    def test_unknown_intent_keeps_full_tool_surface(self) -> None:
        plan = build_turn_plan("What GNU Radio block should I use for spectrum display?")

        self.assertEqual(plan.allowed_tools, DEFAULT_TURN_TOOLS)
        self.assertEqual(plan.expected_op_types, ())

    def test_unclassified_edit_requires_clarification_without_tool_surface(self) -> None:
        plan = build_turn_plan("Swap the signal chain around and validate it.")

        self.assertEqual(plan.intent, INTENT_UNCERTAIN_MUTATION)
        self.assertEqual(plan.allowed_tools, UNCERTAIN_MUTATION_TOOLS)
        self.assertEqual(plan.allowed_tools, ())
        self.assertTrue(plan.requires_clarification)
        self.assertEqual(plan.unsupported_reason, "uncertain_mutation")
        self.assertNotIn("apply_edit", plan.allowed_tools)
        self.assertNotIn("propose_edit", plan.allowed_tools)
        self.assertNotIn("save_graph", plan.allowed_tools)
        self.assertNotIn("remove_connection", plan.allowed_tools)

    def test_uncertain_mutation_does_not_expose_save_even_when_user_says_save(self) -> None:
        plan = build_turn_plan("Swap the signal chain around and save it.")

        self.assertEqual(plan.intent, INTENT_UNCERTAIN_MUTATION)
        self.assertEqual(plan.allowed_tools, UNCERTAIN_MUTATION_TOOLS)
        self.assertEqual(plan.allowed_tools, ())
        self.assertTrue(plan.requires_clarification)
        self.assertNotIn("save_graph", plan.allowed_tools)

    def test_natural_insert_compatible_routes_to_auto_insert(self) -> None:
        plan = build_turn_plan("Insert a compatible block into the main signal path.")

        self.assertEqual(plan.intent, INTENT_INSERTION)
        self.assertEqual(plan.allowed_tools, ("auto_insert_block",))
        self.assertEqual(plan.required_actions, ("auto_insert_block",))

    def test_switch_flowgraph_routes_to_load_only(self) -> None:
        plan = build_turn_plan("Switch over to this other flowgraph: /tmp/other.grc")

        self.assertEqual(plan.intent, INTENT_LOAD)
        self.assertEqual(plan.allowed_tools, ("load_grc",))
        self.assertEqual(plan.required_actions, ("load_grc",))

    def test_load_then_search_keeps_session_search_action(self) -> None:
        plan = build_turn_plan("Load /tmp/other.grc and search the session for alt_rate.")

        self.assertEqual(plan.intent, INTENT_LOAD)
        self.assertEqual(plan.allowed_tools, ("load_grc", "search_grc"))
        self.assertEqual(plan.required_actions, ("load_grc", "search_grc"))

    def test_exact_disconnect_prefers_wrapper_without_apply_edit_fallback(self) -> None:
        plan = build_turn_plan(
            "Disconnect connection_id analog_random_source_x_0:0->blocks_throttle2_0:0."
        )

        self.assertEqual(plan.intent, INTENT_DISCONNECT)
        self.assertEqual(plan.expected_op_types, ("remove_connection",))
        self.assertIn("remove_connection", plan.allowed_tools)
        self.assertNotIn("apply_edit", plan.allowed_tools)
        self.assertEqual(plan.required_actions, ("remove_connection",))

    def test_remove_exact_connection_id_prefers_disconnect_not_block_removal(self) -> None:
        plan = build_turn_plan(
            "Remove the exact connection_id "
            "pdu_random_pdu_0:pdus->blocks_message_debug_0:print_pdu."
        )

        self.assertEqual(plan.intent, INTENT_DISCONNECT)
        self.assertEqual(plan.expected_op_types, ("remove_connection",))
        self.assertEqual(plan.allowed_tools, ("remove_connection",))
        self.assertEqual(plan.required_actions, ("remove_connection",))

    def test_exact_rewire_allows_one_ordered_apply_edit_transaction(self) -> None:
        plan = build_turn_plan(
            "Rewire connection_id strobe_0:strobe->debug_0:print "
            "to strobe_0:strobe->debug_1:print."
        )

        self.assertEqual(plan.intent, INTENT_REWIRE)
        self.assertEqual(plan.expected_op_types, ("remove_connection", "add_connection"))
        self.assertEqual(plan.allowed_tools, ("rewire_connection",))
        self.assertEqual(plan.required_actions, ("rewire_connection",))

    def test_bounded_rewire_new_source_hints_allow_rewire_wrapper(self) -> None:
        plan = build_turn_plan(
            "Rewire connection_id strobe_0:strobe->debug_0:print "
            "to destination debug_1:print using source port strobe."
        )

        self.assertEqual(plan.intent, INTENT_REWIRE)
        self.assertEqual(plan.allowed_tools, ("rewire_connection",))
        self.assertEqual(plan.required_actions, ("rewire_connection",))

    def test_explicit_rewire_wrapper_args_allow_rewire_wrapper(self) -> None:
        plan = build_turn_plan(
            "Call rewire_connection with old_connection_id "
            "strobe_0:strobe->debug_0:print, new_src_port strobe, "
            "new_dst_block debug_1, and new_dst_port print. Do not provide "
            "new_src_block."
        )

        self.assertEqual(plan.intent, INTENT_REWIRE)
        self.assertEqual(plan.allowed_tools, ("rewire_connection",))
        self.assertEqual(plan.required_actions, ("rewire_connection",))

    def test_bounded_rewire_old_endpoint_hints_allow_rewire_wrapper(self) -> None:
        plan = build_turn_plan(
            "Move the old connection from strobe_0 port strobe "
            "to new endpoint strobe_1:strobe->debug_1:print."
        )

        self.assertEqual(plan.intent, INTENT_REWIRE)
        self.assertEqual(plan.allowed_tools, ("rewire_connection",))
        self.assertEqual(plan.required_actions, ("rewire_connection",))

    def test_incomplete_rewire_connection_id_still_clarifies_without_tools(self) -> None:
        plan = build_turn_plan("Rewire connection_id strobe_0:strobe->debug_0:print.")

        self.assertEqual(plan.intent, INTENT_UNCERTAIN_MUTATION)
        self.assertTrue(plan.requires_clarification)
        self.assertEqual(plan.allowed_tools, ())

    def test_vague_rewire_requires_clarification_without_tool_surface(self) -> None:
        plan = build_turn_plan("Rewire this graph so it works better.")

        self.assertEqual(plan.intent, INTENT_UNCERTAIN_MUTATION)
        self.assertTrue(plan.requires_clarification)
        self.assertEqual(plan.allowed_tools, ())

    def test_preview_apply_validate_keeps_both_mutation_actions(self) -> None:
        plan = build_turn_plan("Preview setting samp_rate to 48000, apply it, and validate.")

        self.assertEqual(plan.intent, "preview")
        self.assertIn("propose_edit", plan.required_actions)
        self.assertIn("apply_edit", plan.required_actions)
        self.assertIn("validate_graph", plan.required_actions)

    def test_preview_do_not_apply_does_not_require_apply_edit(self) -> None:
        plan = build_turn_plan(
            "Preview changing samp_rate to 48000. Do not apply it."
        )

        self.assertEqual(plan.intent, "preview")
        self.assertEqual(plan.required_actions, ("propose_edit",))
        self.assertNotIn("apply_edit", plan.allowed_tools)

    def test_preview_without_applying_does_not_require_apply_edit(self) -> None:
        plan = build_turn_plan(
            "Preview changing samp_rate to 48000 without applying it."
        )

        self.assertEqual(plan.intent, "preview")
        self.assertEqual(plan.required_actions, ("propose_edit",))
        self.assertNotIn("apply_edit", plan.allowed_tools)

    def test_preview_before_applying_does_not_require_apply_edit(self) -> None:
        plan = build_turn_plan(
            "Preview changing samp_rate to 64000 before applying anything."
        )

        self.assertEqual(plan.intent, "preview")
        self.assertEqual(plan.required_actions, ("propose_edit",))
        self.assertNotIn("apply_edit", plan.allowed_tools)

    def test_context_then_edit_keeps_context_action(self) -> None:
        plan = build_turn_plan("Show me what uses the samp_rate block, then change its value to 22050.")

        self.assertEqual(plan.intent, INTENT_PARAM_EDIT)
        self.assertIn("get_grc_context", plan.required_actions)
        self.assertIn("apply_edit", plan.required_actions)

    def test_explicit_auto_insert_tool_takes_precedence_over_incidental_validate(self) -> None:
        plan = build_turn_plan(
            "Use auto_insert_block to insert a compatible block; "
            "if multiple safe choices validate, ask me to choose."
        )

        self.assertEqual(plan.intent, INTENT_INSERTION)
        self.assertEqual(plan.allowed_tools, ("auto_insert_block",))
        self.assertEqual(plan.required_actions, ("auto_insert_block",))

    def test_summarize_then_validate_allows_both_tools(self) -> None:
        plan = build_turn_plan("Summarize the graph then validate it.")

        self.assertEqual(plan.allowed_tools, ("summarize_graph", "validate_graph"))
        self.assertEqual(plan.required_actions, ("summarize_graph", "validate_graph"))


if __name__ == "__main__":
    unittest.main()
