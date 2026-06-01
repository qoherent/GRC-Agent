import sys
from PySide6.QtWidgets import QApplication

from grc_agent.agent import GrcAgent
from grc_agent.config import load_config
from grc_agent.toolagents_runtime import ToolAgentsLlamaProviderConfig
from grc_agent_gui.main_window import MainWindow


def main() -> None:
    """Launch the GRC Agent PySide6 GUI application."""
    app = QApplication(sys.argv)
    
    # Initialize core agent configuration
    config = load_config()
    agent = GrcAgent()
    provider_config = ToolAgentsLlamaProviderConfig.from_config(config)
    
    # Start main application frame
    window = MainWindow(agent, provider_config)
    app.aboutToQuit.connect(window.process_manager.shutdown)
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
