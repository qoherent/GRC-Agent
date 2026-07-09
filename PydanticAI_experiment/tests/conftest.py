import sys
from pathlib import Path

# Add PydanticAI_experiment/src to sys.path so tests can find grc_adapter and run
src_path = Path(__file__).resolve().parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))
