"""Generated documentation guard for the model-facing prompt and tools."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from grc_agent.runtime.model_context import (
    MVP_MODEL_TOOL_NAMES,
    build_system_prompt,
)
from grc_agent.runtime.model_context import (
    __version__ as PROMPT_VERSION,
)
from grc_agent.runtime.tool_schemas import build_tool_schemas

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = REPO_ROOT / "docs" / "MODEL_CONTEXT_BIBLE.md"
UPDATE_ENV = "UPDATE_MODEL_CONTEXT_BIBLE"


def render_model_context_bible() -> str:
    """Render the generated model-context documentation from runtime sources."""
    schemas = build_tool_schemas(MVP_MODEL_TOOL_NAMES)
    tool_names = [str(schema.get("function", {}).get("name", "")) for schema in schemas]
    tool_bullets = "\n".join(f"- `{name}`" for name in tool_names)
    schema_json = json.dumps(schemas, indent=2)
    prompt = build_system_prompt()
    return (
        "# Model Context Bible\n"
        "\n"
        "<!-- GENERATED: do not edit by hand. -->\n"
        "\n"
        "This file is generated from the runtime prompt and model-facing tool "
        "schemas. To update it after changing `src/grc_agent/runtime/model_context.py` "
        "or `src/grc_agent/runtime/tool_schemas.py`, run:\n"
        "\n"
        "```bash\n"
        f"{UPDATE_ENV}=1 uv run python -m unittest tests.test_model_context_bible\n"
        "```\n"
        "\n"
        "Normal test mode fails when this file is stale.\n"
        "\n"
        f"Prompt version: `{PROMPT_VERSION}`\n"
        "\n"
        "## Model-Facing Surface\n"
        "\n"
        "The default MVP chat surface exposes these wrapper tools, in order:\n"
        "\n"
        f"{tool_bullets}\n"
        "\n"
        "The model does not see lifecycle tools, shell/filesystem tools, raw YAML "
        "tools, direct transaction primitives, or low-level graph APIs.\n"
        "\n"
        "## Injected System Prompt\n"
        "\n"
        "```text\n"
        f"{prompt}\n"
        "```\n"
        "\n"
        "## Tool Schemas\n"
        "\n"
        "These are the exact schemas returned by "
        "`build_tool_schemas(MVP_MODEL_TOOL_NAMES)`.\n"
        "\n"
        "```json\n"
        f"{schema_json}\n"
        "```\n"
    )


class ModelContextBibleTests(unittest.TestCase):
    def test_model_context_bible_is_generated_from_runtime_sources(self) -> None:
        rendered = render_model_context_bible()
        if os.environ.get(UPDATE_ENV) in {"1", "true", "TRUE", "yes", "YES"}:
            DOC_PATH.write_text(rendered, encoding="utf-8")
        self.assertEqual(
            DOC_PATH.read_text(encoding="utf-8"),
            rendered,
            (
                f"{DOC_PATH.relative_to(REPO_ROOT)} is stale. Regenerate it with:\n"
                f"{UPDATE_ENV}=1 uv run python -m unittest tests.test_model_context_bible"
            ),
        )


if __name__ == "__main__":
    unittest.main()
