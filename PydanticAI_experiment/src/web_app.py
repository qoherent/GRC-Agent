import os
import sys
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, ModelSettings
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.capabilities import ProcessHistory

# Local imports
from grc_adapter import load_flow_graph
from run import grc_tools, build_system_prompt, prune_history, MODEL, OLLAMA_V1

# 1. Load active GRC flowgraph fixture
PROJECT_ROOT = Path(__file__).resolve().parent.parent
default_fixture = str(PROJECT_ROOT / "tests" / "data" / "dial_tone.grc")
fixture_path = os.environ.get("GRC_FIXTURE_PATH", default_fixture)

if not Path(fixture_path).is_absolute():
    fixture_path = str(PROJECT_ROOT / fixture_path)

print(f"==================================================")
print(f"Starting GRC Web GUI Session")
print(f"Loading GRC flowgraph fixture: {fixture_path}")
print(f"==================================================")

try:
    fg = load_flow_graph(fixture_path)
except Exception as e:
    print(f"Error loading GRC flowgraph: {e}")
    sys.exit(1)

# 2. Build local Ollama Model & Agent
model = OllamaModel(MODEL, provider=OllamaProvider(base_url=OLLAMA_V1))
agent = Agent(
    model=model,
    deps_type=type(fg),
    instructions=build_system_prompt("pai-web-chat"),
    tools=grc_tools(),
    capabilities=[ProcessHistory(prune_history)],
    model_settings=ModelSettings(extra_body={"think": True})
)

# Set base URL for Ollama provider discovery
os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"

# 3. Expose the agent via the built-in web chat Starlette application
# Exposes the tool calling, streaming responses, and real-time validations.
app = agent.to_web(models=[f"ollama:{MODEL}"], deps=fg)
