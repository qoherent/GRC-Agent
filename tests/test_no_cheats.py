"""Static checks that prevent eval-specific cheats from reappearing."""

from __future__ import annotations

from pathlib import Path
import unittest


class NoCheatsTests(unittest.TestCase):
    def test_production_code_excludes_removed_cheat_patterns(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        prod_root = repo_root / "src" / "grc_agent"
        forbidden_fragments = {
            "random_bit_generator.grc",
            "required_next_tools",
            "internal_compile_check_passed",
            "_canonical_samp_rate_repair_operations",
            "_build_samp_rate_repair_hint",
            "_parse_variable_add_request_from_prompt",
            "_remaining_follow_up_tools",
            "_attach_read_only_follow_up_hint",
            "_requested_follow_up_tools_for_current_user",
            "_read_only_follow_up_tools_for_current_user",
            "_text_requests_",
        }

        offenders: list[str] = []
        for path in sorted(prod_root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for fragment in sorted(forbidden_fragments):
                if fragment in text:
                    offenders.append(f"{path.relative_to(repo_root)}: {fragment}")

        self.assertEqual(offenders, [])

    def test_manual_retrieval_is_not_imported_by_mutation_paths(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        mutation_paths = [
            repo_root / "src" / "grc_agent" / "transaction",
            repo_root / "src" / "grc_agent" / "validation",
            repo_root / "src" / "grc_agent" / "session" / "auto_insert.py",
            repo_root / "src" / "grc_agent" / "session" / "insertion_suggestions.py",
        ]

        offenders: list[str] = []
        for path in mutation_paths:
            files = [path] if path.is_file() else sorted(path.rglob("*.py"))
            for file_path in files:
                text = file_path.read_text(encoding="utf-8")
                if "grc_agent.manual" in text:
                    offenders.append(str(file_path.relative_to(repo_root)))

        self.assertEqual(offenders, [])
