"""Allow `python -m grc_agent` as an alias for the `grc-agent` console script."""

from grc_agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
