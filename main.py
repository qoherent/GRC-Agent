# Keep the top-level entry point thin so the real logic stays in the package.
from grc_agent.cli import main as cli_main


def main() -> int:
    # This wrapper lets `python main.py` reuse the same code path as the package CLI.
    return cli_main()


if __name__ == "__main__":
    # Return the CLI status code to the shell when the script is executed directly.
    raise SystemExit(main())
