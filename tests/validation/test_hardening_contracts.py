"""Contract tests that harden internal session and transaction coupling."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from grc_agent.session_ops import FLOWGRAPH_SESSION_SHARED_PRIVATE_METHODS


class HardeningContractTests(unittest.TestCase):
    """Catch drift between the staged-validation and apply paths."""

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _module_path(self, *parts: str) -> Path:
        return self._repo_root().joinpath("src", "grc_agent", *parts)

    def _parse_module(self, path: Path) -> ast.AST:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def _collect_private_flowgraph_accesses(self, path: Path) -> set[str]:
        tree = self._parse_module(path)
        accesses: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "FlowgraphSession"
                and node.attr.startswith("_")
            ):
                accesses.add(node.attr)
        return accesses

    def _collect_op_types(self, path: Path, *, left_name: str) -> set[str]:
        tree = self._parse_module(path)
        op_types: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare) or len(node.ops) != 1:
                continue
            if not isinstance(node.ops[0], ast.Eq):
                continue

            left = node.left
            is_match = (
                isinstance(left, ast.Name)
                and left.id == left_name
                or isinstance(left, ast.Attribute)
                and isinstance(left.value, ast.Name)
                and left.value.id == "operation"
                and left.attr == left_name
            )
            if not is_match:
                continue

            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(
                    comparator.value, str
                ):
                    op_types.add(comparator.value)
        return op_types

    def test_shared_session_protocol_lists_the_extracted_private_methods(self) -> None:
        self.assertEqual(
            set(FLOWGRAPH_SESSION_SHARED_PRIVATE_METHODS),
            {
                "_parse_blocks",
                "_parse_connections",
                "_default_block_states",
                "_block_name_is_referenced_elsewhere",
                "_connection_entry_to_tuple",
                "_raw_connection_entry",
            },
        )

    def test_checks_module_no_longer_reaches_into_flowgraphsession_privates(self) -> None:
        accesses = self._collect_private_flowgraph_accesses(
            self._module_path("validation", "checks.py")
        )

        self.assertEqual(
            accesses,
            set(),
            "validation.checks should use grc_agent.session_ops instead of "
            f"FlowgraphSession private helpers. Shared protocol: {sorted(FLOWGRAPH_SESSION_SHARED_PRIVATE_METHODS)}",
        )

    def test_preflight_and_apply_support_the_same_operation_types(self) -> None:
        validation_op_types = self._collect_op_types(
            self._module_path("validation", "checks.py"),
            left_name="op_type",
        )
        apply_op_types = self._collect_op_types(
            self._module_path("transaction", "edit.py"),
            left_name="op_type",
        )

        self.assertEqual(validation_op_types, apply_op_types)
        self.assertEqual(
            validation_op_types,
            {
                "update_params",
                "update_states",
                "add_connection",
                "remove_connection",
                "remove_block",
                "add_block",
                "insert_block_on_connection",
            },
        )


if __name__ == "__main__":
    unittest.main()
