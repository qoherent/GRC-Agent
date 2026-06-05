"""Allow `python -m grc_agent_gui` as an alias for the `grc-agent-gui` script."""

from grc_agent_gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
