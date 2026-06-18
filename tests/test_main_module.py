"""Smoke tests for the `python -m grc_agent_gui` entry point.

The CLI ``python -m grc_agent`` entry point was removed when the CLI was
deleted; only the GUI entry point remains. This test verifies the GUI
``__main__.py`` imports the expected ``main`` symbol. Running the GUI in a
test process is out of scope (it requires a display server).
"""

import importlib
import unittest


class MainModuleEntryPointTests(unittest.TestCase):
    """The GUI package must be runnable as `python -m grc_agent_gui`."""

    def test_grc_agent_gui_main_module_imports_app_main(self) -> None:
        """`python -m grc_agent_gui` should resolve to the GUI main function."""
        main_module = importlib.import_module("grc_agent_gui.__main__")
        self.assertTrue(callable(getattr(main_module, "main", None)))


if __name__ == "__main__":
    unittest.main()
