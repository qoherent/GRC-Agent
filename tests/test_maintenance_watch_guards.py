"""Regression guards for architecture-audit maintenance watch items."""

from __future__ import annotations

import unittest
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession


def _fixture_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "dial_tone.grc"


def _load_agent() -> tuple[GrcAgent, FlowgraphSession]:
    session = FlowgraphSession()
    session.load(_fixture_path())
    return GrcAgent(session), session


def _raw_snapshot(session: FlowgraphSession) -> tuple[int, bool, str]:
    assert session.flowgraph is not None
    return (
        session.state_revision,
        session.is_dirty,
        session._serialize_raw_data(session.flowgraph.export_data()),
    )


class RuntimeRefactorGuardTests(unittest.TestCase):
    """Deleted adapter symbols and text recovery paths must stay absent."""

    def test_old_llama_adapter_symbols_are_absent_from_repo_code(self) -> None:
        root = Path(__file__).resolve().parents[1]
        needles = (
            "LlamaServerClient",
            "LlamaToolCall",
            "run_bounded_llama_turn",
        )
        offenders: list[str] = []
        for path in [*root.glob("src/**/*.py"), *root.glob("scripts/**/*.py")]:
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                if needle in text:
                    offenders.append(f"{path.relative_to(root)}:{needle}")
        self.assertEqual(offenders, [])

    def test_unknown_tool_call_is_rejected_by_runtime_schema(self) -> None:
        agent, session = _load_agent()
        before = _raw_snapshot(session)

        result = agent.validate_tool_call("raw_yaml_edit", {"path": "graph.grc"})

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "unknown_tool")
        self.assertEqual(_raw_snapshot(session), before)


if __name__ == "__main__":
    unittest.main()
