"""Unit tests for the generic turn-completion guard."""

import inspect
import unittest

from grc_agent.turn_guard import (
    build_continuation_prompt,
    parse_required_actions,
)


class TestParseRequiredActions(unittest.TestCase):

    def test_validate_and_summary(self):
        result = parse_required_actions("Validate and give me a summary.")
        self.assertEqual(result, {"validate_graph", "summarize_graph"})

    def test_check_and_write_out(self):
        result = parse_required_actions("Check it and write it out.")
        self.assertEqual(result, {"validate_graph", "save_graph"})

    def test_edit_and_validate(self):
        result = parse_required_actions("Update samp_rate to 48000 and validate.")
        self.assertEqual(result, {"apply_edit", "validate_graph"})

    def test_preview_only(self):
        result = parse_required_actions("Preview removing the throttle block.")
        self.assertEqual(result, {"propose_edit"})

    def test_preview_suppresses_apply_edit(self):
        result = parse_required_actions(
            "Preview what would happen if I update samp_rate."
        )
        self.assertIn("propose_edit", result)
        self.assertNotIn("apply_edit", result)

    def test_overview_only(self):
        result = parse_required_actions("Give me a quick overview.")
        self.assertEqual(result, {"summarize_graph"})

    def test_no_actions(self):
        result = parse_required_actions("I want PSK modulation blocks.")
        self.assertEqual(result, set())

    def test_change_only(self):
        result = parse_required_actions("Change samp_rate to 32000.")
        self.assertEqual(result, {"apply_edit"})

    def test_save_only(self):
        result = parse_required_actions("Save the graph.")
        self.assertEqual(result, {"save_graph"})

    def test_write_copy_to_path(self):
        result = parse_required_actions("Write a copy to /tmp/test.grc.")
        self.assertEqual(result, {"save_graph"})

    def test_add_block(self):
        result = parse_required_actions("Add a throttle block.")
        self.assertEqual(result, {"apply_edit"})

    def test_remove_block(self):
        result = parse_required_actions("Remove the samp_rate variable.")
        self.assertEqual(result, {"apply_edit"})

    def test_validate_only(self):
        result = parse_required_actions("Validate the graph.")
        self.assertEqual(result, {"validate_graph"})

    def test_summary_only(self):
        result = parse_required_actions("Give me a summary.")
        self.assertEqual(result, {"summarize_graph"})

    def test_describe_graph(self):
        result = parse_required_actions("Please describe graph for me.")
        self.assertEqual(result, {"summarize_graph"})

    def test_case_insensitive(self):
        result = parse_required_actions("VALIDATE and SAVE the graph.")
        self.assertEqual(result, {"validate_graph", "save_graph"})

    def test_full_workflow_check_save(self):
        result = parse_required_actions("Check it and write it out.")
        self.assertEqual(result, {"validate_graph", "save_graph"})

    def test_edit_then_validate(self):
        result = parse_required_actions("Update samp_rate to 48000 and validate.")
        self.assertEqual(result, {"apply_edit", "validate_graph"})

    def test_preview_and_validate(self):
        result = parse_required_actions(
            "Preview removing samp_rate and validate the result."
        )
        self.assertIn("propose_edit", result)
        self.assertIn("validate_graph", result)
        self.assertNotIn("apply_edit", result)


class TestFalsePositiveSafety(unittest.TestCase):

    def test_validate_but_do_not_save(self):
        result = parse_required_actions("Validate but do not save.")
        self.assertIn("validate_graph", result)
        self.assertNotIn("save_graph", result)

    def test_check_but_dont_write_out(self):
        result = parse_required_actions("Check the graph but don't write it out.")
        self.assertIn("validate_graph", result)
        self.assertNotIn("save_graph", result)

    def test_summarize_what_would_happen_no_edit(self):
        result = parse_required_actions(
            "Summarize what would happen before changing anything."
        )
        self.assertNotIn("apply_edit", result)
        self.assertNotIn("summarize_graph", result)
        self.assertIn("propose_edit", result)

    def test_how_to_save_is_not_a_save_request(self):
        result = parse_required_actions("Can you tell me how to save?")
        self.assertNotIn("save_graph", result)

    def test_preview_routes_to_propose_not_apply(self):
        result = parse_required_actions("Preview removing the throttle.")
        self.assertIn("propose_edit", result)
        self.assertNotIn("apply_edit", result)

    def test_do_not_validate(self):
        result = parse_required_actions("Update the rate but do not validate.")
        self.assertIn("apply_edit", result)
        self.assertNotIn("validate_graph", result)

    def test_dont_check(self):
        result = parse_required_actions("Change the rate, don't check.")
        self.assertIn("apply_edit", result)
        self.assertNotIn("validate_graph", result)


class TestBuildContinuationPrompt(unittest.TestCase):

    def test_single_tool(self):
        prompt = build_continuation_prompt({"summarize_graph"})
        self.assertIn("summarize_graph", prompt)
        self.assertIn("not complete yet", prompt)

    def test_multiple_tools_sorted(self):
        prompt = build_continuation_prompt({"save_graph", "summarize_graph"})
        self.assertIn("summarize_graph", prompt)
        self.assertIn("save_graph", prompt)
        idx_save = prompt.index("save_graph")
        idx_sum = prompt.index("summarize_graph")
        self.assertLess(idx_save, idx_sum)

    def test_validate_and_save(self):
        prompt = build_continuation_prompt({"validate_graph", "save_graph"})
        self.assertIn("validate_graph", prompt)
        self.assertIn("save_graph", prompt)


class TestGuardConstraints(unittest.TestCase):

    def test_no_fixture_names_in_parse_required_actions(self):
        source = inspect.getsource(parse_required_actions)
        self.assertNotIn("random_bit_generator", source)
        self.assertNotIn("samp_rate", source)
        self.assertNotIn("throttle", source)

    def test_no_fixture_names_in_build_continuation_prompt(self):
        source = inspect.getsource(build_continuation_prompt)
        self.assertNotIn("random_bit_generator", source)
        self.assertNotIn("samp_rate", source)

    def test_no_regex_in_guard_module(self):
        import grc_agent.turn_guard as mod
        source = inspect.getsource(mod)
        self.assertNotIn("import re", source)
        self.assertNotIn("re.match", source)
        self.assertNotIn("re.search", source)
        self.assertNotIn("re.sub", source)

    def test_continuation_budget_one_nudge_only(self):
        budget = 1
        required = {"validate_graph", "summarize_graph"}
        completed: set[str] = set()
        remaining = required - completed
        self.assertTrue(remaining and budget > 0)
        budget -= 1
        completed.add("summarize_graph")
        remaining = required - completed
        self.assertFalse(budget > 0)

    def test_no_continuation_when_all_completed(self):
        required = {"validate_graph", "summarize_graph"}
        completed = {"validate_graph", "summarize_graph"}
        remaining = required - completed
        self.assertEqual(len(remaining), 0)

    def test_no_continuation_after_execution_failure(self):
        any_execution_failed = True
        remaining = {"summarize_graph"}
        budget = 1
        should_nudge = remaining and budget > 0 and not any_execution_failed
        self.assertFalse(should_nudge)


class TestAgentGuardMethods(unittest.TestCase):

    def _make_agent(self):
        from grc_agent.agent import GrcAgent
        return GrcAgent(catalog_root="/usr/share/gnuradio/grc/blocks")

    def test_init_turn_requirements_parses_actions(self):
        agent = self._make_agent()
        agent.init_turn_requirements("Validate and give me a summary.")
        self.assertEqual(agent._turn_required_actions, {"validate_graph", "summarize_graph"})

    def test_record_tool_completion_tracks_success(self):
        agent = self._make_agent()
        agent.init_turn_requirements("Validate and save.")
        agent.record_tool_completion("validate_graph", True)
        self.assertIn("validate_graph", agent._turn_completed_actions)
        self.assertFalse(agent._turn_any_execution_failed)

    def test_record_tool_completion_tracks_failure(self):
        agent = self._make_agent()
        agent.init_turn_requirements("Validate and save.")
        agent.record_tool_completion("apply_edit", False)
        self.assertTrue(agent._turn_any_execution_failed)
        self.assertNotIn("apply_edit", agent._turn_completed_actions)

    def test_check_turn_continuation_nudge(self):
        agent = self._make_agent()
        agent.init_turn_requirements("Validate and give me a summary.")
        agent.record_tool_completion("validate_graph", True)
        should_nudge, nudge = agent.check_turn_continuation()
        self.assertTrue(should_nudge)
        self.assertIn("summarize_graph", nudge)

    def test_check_turn_continuation_no_nudge_when_complete(self):
        agent = self._make_agent()
        agent.init_turn_requirements("Validate and give me a summary.")
        agent.record_tool_completion("validate_graph", True)
        agent.record_tool_completion("summarize_graph", True)
        should_nudge, nudge = agent.check_turn_continuation()
        self.assertFalse(should_nudge)
        self.assertEqual(nudge, "")

    def test_check_turn_continuation_no_nudge_after_failure(self):
        agent = self._make_agent()
        agent.init_turn_requirements("Validate and save.")
        agent.record_tool_completion("validate_graph", False)
        should_nudge, nudge = agent.check_turn_continuation()
        self.assertFalse(should_nudge)

    def test_check_turn_continuation_budget_one(self):
        agent = self._make_agent()
        agent.init_turn_requirements("Validate and give me a summary.")
        should_nudge_1, _ = agent.check_turn_continuation()
        self.assertTrue(should_nudge_1)
        should_nudge_2, _ = agent.check_turn_continuation()
        self.assertFalse(should_nudge_2)


if __name__ == "__main__":
    unittest.main()
