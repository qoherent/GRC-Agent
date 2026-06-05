"""Smoke tests for the `python -m grc_agent` and `python -m grc_agent_gui` entry points.

These tests only verify that the `__main__.py` files exist and import the
expected `main` symbol. Running the full CLI/GUI in a test process is out of
scope (the CLI tries to start llama.cpp; the GUI requires a display server).
"""

import importlib
import unittest


class MainModuleEntryPointTests(unittest.TestCase):
    """Both packages must be runnable as `python -m <pkg>`."""

    def test_grc_agent_main_module_imports_cli_main(self) -> None:
        """`python -m grc_agent` should resolve to the CLI main function."""
        main_module = importlib.import_module("grc_agent.__main__")
        self.assertTrue(callable(getattr(main_module, "main", None)))

    def test_grc_agent_gui_main_module_imports_app_main(self) -> None:
        """`python -m grc_agent_gui` should resolve to the GUI main function."""
        main_module = importlib.import_module("grc_agent_gui.__main__")
        self.assertTrue(callable(getattr(main_module, "main", None)))


if __name__ == "__main__":
    unittest.main()
