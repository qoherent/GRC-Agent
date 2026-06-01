import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication

from grc_agent.agent import GrcAgent
from grc_agent.config import load_app_config
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.session.load import load_grc
from grc_agent.toolagents_runtime import ToolAgentsLlamaProviderConfig
from grc_agent_gui.main_window import MainWindow


def main() -> None:
    """Launch the GRC Agent PySide6 GUI application.

    Usage:
        uv run grc-agent-gui [path/to/copy.grc]
    """
    app = QApplication(sys.argv)

    config = load_app_config()
    llama_cfg = config.llama
    provider_config = ToolAgentsLlamaProviderConfig(
        base_url=llama_cfg.server_url,
        model=llama_cfg.model,
        api_key=None,
        timeout_seconds=llama_cfg.request_timeout_seconds,
        max_tokens=llama_cfg.max_tokens,
        temperature=llama_cfg.temperature,
        enable_thinking=llama_cfg.enable_thinking,
    )

    session: FlowgraphSession | None = None
    if len(sys.argv) > 1:
        grc_path = Path(sys.argv[1])
        if not grc_path.is_file():
            print(f"Error: graph file not found: {grc_path}", file=sys.stderr)
            sys.exit(2)
        loaded = load_grc(grc_path)
        if isinstance(loaded, dict):
            print(f"Error: failed to load graph: {loaded.get('message', 'unknown error')}",
                  file=sys.stderr)
            sys.exit(2)
        if not loaded.validate():
            print(
                f"Error: refusing to load graph because validation failed "
                f"(state={loaded.validation_state().get('state', 'unknown')}).",
                file=sys.stderr,
            )
            sys.exit(2)
        session = loaded

    agent = GrcAgent(session=session)

    window = MainWindow(agent, provider_config)
    app.aboutToQuit.connect(window.process_manager.shutdown)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

