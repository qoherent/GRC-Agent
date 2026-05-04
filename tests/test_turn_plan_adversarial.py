"""Adversarial deterministic coverage for typed turn routing."""

from __future__ import annotations

import unittest

from grc_agent.runtime.turn_plan import (
    INTENT_ADD_VARIABLE,
    INTENT_DISCONNECT,
    INTENT_INSERTION,
    INTENT_PARAM_EDIT,
    INTENT_PREVIEW,
    INTENT_REMOVE_BLOCK,
    INTENT_STATE_EDIT,
    INTENT_UNCERTAIN_MUTATION,
    UNCERTAIN_MUTATION_TOOLS,
    build_turn_plan,
)


class AdversarialTurnPlanTests(unittest.TestCase):
    def test_adversarial_prompt_matrix_has_release_scale(self) -> None:
        self.assertGreaterEqual(len(_CASES), 100)

    def test_adversarial_turn_plan_matrix(self) -> None:
        for prompt, expected_intent, expected_tool in _CASES:
            with self.subTest(prompt=prompt):
                plan = build_turn_plan(prompt)

                self.assertEqual(plan.intent, expected_intent)
                if expected_tool is not None:
                    self.assertIn(expected_tool, plan.allowed_tools)

                if expected_intent == INTENT_UNCERTAIN_MUTATION:
                    self.assertEqual(plan.allowed_tools, UNCERTAIN_MUTATION_TOOLS)
                    self.assertNotIn("apply_edit", plan.allowed_tools)
                    self.assertNotIn("save_graph", plan.allowed_tools)
                    self.assertNotIn("remove_connection", plan.allowed_tools)


_STATE_PROMPTS = (
    "Disable blocks_throttle2_0.",
    "Enable blocks_throttle2_0.",
    "Turn off blocks_throttle2_0.",
    "Turn on blocks_throttle2_0.",
    "Shut off blocks_throttle2_0.",
    "Switch off blocks_throttle2_0.",
    "Switch on blocks_throttle2_0.",
    "Deactivate blocks_throttle2_0.",
    "Activate blocks_throttle2_0.",
    "Re-enable blocks_throttle2_0.",
    "Reenable blocks_throttle2_0.",
    "Mute blocks_throttle2_0.",
    "Unmute blocks_throttle2_0.",
    "Do not remove blocks_throttle2_0, disable it.",
    "Disable the block named remove_block.",
    "Please turn off the throttle block.",
    "Temporarily deactivate the throttle block.",
    "Make blocks_throttle2_0 disabled.",
    "Make blocks_throttle2_0 enabled.",
    "Switch blocks_throttle2_0 off and validate it.",
)

_REMOVE_PROMPTS = (
    "Remove blocks_throttle2_0.",
    "Remove the blocks_throttle2_0 block.",
    "Delete blocks_throttle2_0.",
    "Delete the throttle block.",
    "Get rid of blocks_throttle2_0.",
    "Drop blocks_throttle2_0.",
    "Drop the throttle block.",
    "Remove variable samp_rate.",
    "Delete the variable samp_rate.",
    "Get rid of the samp_rate variable.",
    "Remove the disabled block blocks_throttle2_0.",
    "Delete disabled block blocks_throttle2_0.",
    "Remove blocks_throttle2_0 and validate it.",
    "Delete blocks_throttle2_0 then summarize the graph.",
    "Drop the block named blocks_throttle2_0.",
)

_DISCONNECT_PROMPTS = (
    "Disconnect analog_random_source_x_0 output 0 from blocks_throttle2_0 input 0.",
    "Disconnect from analog_random_source_x_0 to blocks_throttle2_0.",
    "Disconnect analog_random_source_x_0:0->blocks_throttle2_0:0.",
    "Disconnect connection_id analog_random_source_x_0:0->blocks_throttle2_0:0.",
    "Disconnect the wire from blocks_throttle2_0 to blocks_char_to_float_0.",
    "Disconnect qtgui_time_sink_x_0 input from blocks_char_to_float_0 output.",
    "Disconnect the connection from source to throttle and validate it.",
    "Disconnect the edge analog_random_source_x_0:0->blocks_throttle2_0:0.",
    "Disconnect output 0 from input 0 between source and throttle.",
)

_DISCONNECT_PREVIEW_PROMPTS = (
    "Preview disconnecting analog_random_source_x_0:0->blocks_throttle2_0:0.",
)

_ADD_VARIABLE_PROMPTS = (
    "Add variable debug_flag set to 0.",
    "Add a variable debug_flag set to 0.",
    "Add the variable debug_flag set to 0.",
    "Create variable debug_flag with value 0.",
    "Create a variable debug_flag with value 0.",
    "Create the variable debug_flag.",
    "Add a variable called noise_level set to 0.1.",
    "Create a variable named gain set to 2.",
    "Add variable center_freq set to 915e6 and validate.",
)

_ADD_VARIABLE_PREVIEW_PROMPTS = (
    "Preview add variable debug_flag set to 0.",
)

_INSERT_WITH_ANCHOR_PROMPTS = (
    "Insert a compatible block into the main signal path.",
    "Add a compatible block into the main path.",
    "Insert a filter into the main path.",
    "Add a filter into the signal path.",
    "Place a throttle between analog_random_source_x_0 and blocks_char_to_float_0.",
    "Put a head block after blocks_throttle2_0.",
    "Insert a throttle before qtgui_time_sink_x_0.",
    "Add a head into connection analog_random_source_x_0:0->blocks_throttle2_0:0.",
    "Insert a compatible block from source to throttle.",
    "Place a filter on the stream path.",
)

_INSERT_MISSING_ANCHOR_PROMPTS = (
    "Insert a compatible block.",
    "Add a compatible block.",
    "Insert a filter.",
    "Add a filter.",
    "Put a throttle block in the graph.",
    "Place a head block somewhere.",
    "Add a throttle.",
    "Insert head.",
    "Put filter there.",
    "Add a compatible filter and save it.",
)

_PARAM_PROMPTS = (
    "Change samp_rate to 48000.",
    "Set samp_rate to 48000.",
    "Update samp_rate to 48000.",
    "Bump samp_rate to 96k.",
    "Change blocks_throttle2_0 samples_per_second to 48000.",
    "Set qtgui_time_sink_x_0 srate to 48000.",
    "Update the sample rate variable to 44100.",
    "Change samp_rate to 48000 and validate it.",
    "Set samp_rate to 48000 and save it.",
    "Preview changing samp_rate to 48000.",
)

_READONLY_PROMPTS = (
    "Validate the graph.",
    "Check the graph.",
    "Summarize the graph.",
    "Give me an overview.",
    "Find a spectrum display block.",
    "Search for an audio smoother.",
    "Look up automatic gain control.",
    "Describe blocks_throttle2.",
    "What uses samp_rate?",
    "Show context around blocks_throttle2_0.",
)

_VAGUE_MUTATION_PROMPTS = (
    "Fix the signal chain.",
    "Repair the graph.",
    "Swap the signal chain around.",
    "Replace the bad block.",
    "Rewire the audio path.",
    "Wire this correctly.",
    "Connect it to the sink.",
    "Disconnect the source.",
    "Move the sink over there.",
    "Clean up this graph and save it.",
    "Make it better.",
    "Remove the disable flag.",
    "Drop samples at the front.",
    "Turn this into a spectrum analyzer.",
    "Use an audio smoother here.",
    "Add something useful.",
    "Put a rate limiter in.",
    "Patch the broken path.",
    "Make this production-ready.",
    "Auto-fix whatever is wrong.",
    "Change the topology.",
    "Replace this path with a better one.",
    "Connect the remaining blocks.",
    "Disconnect whatever is extra.",
    "Swap the filter and sink.",
)

_BLOCK_UID_MUTATION_PROMPTS = (
    "Use the block_uid for analog_sig_source_x_0 to mutate that block.",
    "Mutate by block_uid.",
    "Change the block with block_uid abc123.",
    "Disable the block_uid abc123 block.",
    "Remove by block_uid abc123.",
)

_CASES = (
    *((prompt, INTENT_STATE_EDIT, "apply_edit") for prompt in _STATE_PROMPTS),
    *((prompt, INTENT_REMOVE_BLOCK, "apply_edit") for prompt in _REMOVE_PROMPTS),
    *((prompt, INTENT_DISCONNECT, "remove_connection") for prompt in _DISCONNECT_PROMPTS),
    *((prompt, INTENT_DISCONNECT, "propose_edit") for prompt in _DISCONNECT_PREVIEW_PROMPTS),
    *((prompt, INTENT_ADD_VARIABLE, "apply_edit") for prompt in _ADD_VARIABLE_PROMPTS),
    *((prompt, INTENT_ADD_VARIABLE, "propose_edit") for prompt in _ADD_VARIABLE_PREVIEW_PROMPTS),
    *((prompt, INTENT_INSERTION, "auto_insert_block") for prompt in _INSERT_WITH_ANCHOR_PROMPTS),
    *((prompt, INTENT_UNCERTAIN_MUTATION, None) for prompt in _INSERT_MISSING_ANCHOR_PROMPTS),
    *((prompt, INTENT_PARAM_EDIT, "apply_edit") for prompt in _PARAM_PROMPTS[:-1]),
    *((_PARAM_PROMPTS[-1], INTENT_PREVIEW, "propose_edit"),),
    *((prompt, INTENT_UNCERTAIN_MUTATION, None) for prompt in _VAGUE_MUTATION_PROMPTS),
    *((prompt, INTENT_UNCERTAIN_MUTATION, None) for prompt in _BLOCK_UID_MUTATION_PROMPTS),
)


if __name__ == "__main__":
    unittest.main()
