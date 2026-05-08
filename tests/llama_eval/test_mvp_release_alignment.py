"""Static guards for MVP release-eval profile alignment."""

from __future__ import annotations

from unittest.mock import patch
import unittest

from tests.llama_eval.harness import LEGACY_MODEL_TOOLS, MVP_RELEASE_MODEL_TOOLS, LiveScenario
from tests.llama_eval.tier2_release import _run_case as tier2_run_case
from tests.llama_eval.tier2_release import release_cases as tier2_release_cases
from tests.llama_eval.tier3_multiturn import _run_case as tier3_run_case
from tests.llama_eval.tier3_multiturn import release_cases as tier3_release_cases
from tests.llama_eval.tier4_external_examples import _run_case as tier4_run_case
from tests.llama_eval.tier4_external_examples import release_cases as tier4_release_cases
from tests.llama_eval.tier5_adversarial import _run_case as tier5_run_case
from tests.llama_eval.tier5_adversarial import release_cases as tier5_release_cases


class MvpReleaseAlignmentTests(unittest.TestCase):
    def test_release_cases_only_expect_mvp_wrapper_tools(self) -> None:
        suites = [
            tier2_release_cases(),
            tier3_release_cases(),
            tier4_release_cases(),
            tier5_release_cases(),
        ]
        for scenarios in suites:
            for scenario in scenarios:
                for turn in scenario.turns:
                    for expectation in turn.expected_tool_calls:
                        self.assertIn(
                            expectation.name,
                            MVP_RELEASE_MODEL_TOOLS,
                            f"unexpected expected tool {expectation.name} in {scenario.name}",
                        )
                        self.assertNotIn(
                            expectation.name,
                            LEGACY_MODEL_TOOLS,
                            f"legacy expected tool leaked in {scenario.name}",
                        )

    def test_tier2_run_case_enables_mvp_profile(self) -> None:
        scenario = LiveScenario(category="c", name="n", turns=())
        with patch("tests.llama_eval.tier2_release.run_live_scenario_once", return_value={"ok": True}) as mock:
            tier2_run_case(client=object(), model="m", case=scenario)
        self.assertTrue(mock.call_args.kwargs["mvp_tool_profile"])

    def test_tier3_run_case_enables_mvp_profile(self) -> None:
        scenario = LiveScenario(category="c", name="n", turns=())
        with patch("tests.llama_eval.tier3_multiturn.run_live_scenario_once", return_value={"ok": True}) as mock:
            tier3_run_case(client=object(), model="m", case=scenario)
        self.assertTrue(mock.call_args.kwargs["mvp_tool_profile"])

    def test_tier4_run_case_enables_mvp_profile(self) -> None:
        scenario = LiveScenario(category="c", name="n", turns=())
        with patch("tests.llama_eval.tier4_external_examples.run_live_scenario_once", return_value={"ok": True}) as mock:
            tier4_run_case(client=object(), model="m", case=scenario)
        self.assertTrue(mock.call_args.kwargs["mvp_tool_profile"])

    def test_tier5_run_case_enables_mvp_profile(self) -> None:
        scenario = LiveScenario(category="c", name="n", turns=())
        with patch("tests.llama_eval.tier5_adversarial.run_live_scenario_once", return_value={"ok": True}) as mock:
            tier5_run_case(client=object(), model="m", case=scenario)
        self.assertTrue(mock.call_args.kwargs["mvp_tool_profile"])


if __name__ == "__main__":
    unittest.main()
