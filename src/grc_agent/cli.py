"""Command-line entry point for GRC Agent."""


def main() -> int:
    # This stays intentionally small until real subcommands are added later.
    print("GRC Agent CLI placeholder")
    return 0


if __name__ == "__main__":
    # Allow `python -m grc_agent.cli` to exit with the CLI status code.
    raise SystemExit(main())