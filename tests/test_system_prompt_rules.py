"""Tests for the system-prompt behavioral rules.

The model-facing prompt lives in :mod:`grc_agent.runtime.model_context`.
These tests pin two short rules so they cannot regress:

- The concise-response rule: when the user asks a question, lead with
  the direct answer.
- The no-LaTeX rule: do not emit LaTeX in chat replies.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


class SystemPromptBehavioralRulesTests(unittest.TestCase):
    def test_prompt_includes_concise_response_rule(self) -> None:
        from grc_agent.runtime.model_context import build_system_prompt

        prompt = build_system_prompt()
        # The rule must be present and short — one line, not a paragraph.
        lowered = prompt.lower()
        self.assertIn("concisely", lowered)
        # The rule should explicitly tie the constraint to user questions.
        self.assertIn("user asks", lowered)

    def test_prompt_includes_no_latex_rule(self) -> None:
        from grc_agent.runtime.model_context import build_system_prompt

        prompt = build_system_prompt()
        lowered = prompt.lower()
        self.assertIn("do not use latex", lowered)


if __name__ == "__main__":
    unittest.main()
