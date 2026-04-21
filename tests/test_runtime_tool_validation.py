"""Tests for runtime tool-call schema validation."""

from pathlib import Path
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime_tool_validation import build_tool_schema_map, validate_runtime_tool_call


class RuntimeToolValidationTests(unittest.TestCase):
    """Validate the narrowed model-facing tool schema contract."""

    def _fixture_path(self) -> Path:
        test_directory = Path(__file__).resolve().parent
        return test_directory / "data" / "random_bit_generator.grc"

    def _schema_map(self) -> dict[str, dict]:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        agent = GrcAgent(session)
        return build_tool_schema_map(agent.get_tool_schemas())

    def test_unknown_tool_is_rejected(self) -> None:
        result = validate_runtime_tool_call("set_variable", {}, self._schema_map())

        assert result is not None
        self.assertEqual(result["error_type"], "unknown_tool")
        self.assertEqual(result["validation_errors"][0]["code"], "unknown_tool")

    def test_missing_required_argument_is_rejected(self) -> None:
        result = validate_runtime_tool_call("load_grc", {}, self._schema_map())

        assert result is not None
        self.assertEqual(result["error_type"], "tool_call_invalid")
        self.assertEqual(result["validation_errors"][0]["code"], "missing_required")
        self.assertEqual(result["validation_errors"][0]["field"], "file_path")

    def test_unsupported_extra_argument_is_rejected(self) -> None:
        result = validate_runtime_tool_call(
            "search_grc",
            {"query": "samp_rate", "scope": "session", "unexpected": True},
            self._schema_map(),
        )

        assert result is not None
        self.assertEqual(result["error_type"], "tool_call_invalid")
        self.assertEqual(result["validation_errors"][0]["code"], "unexpected_argument")
        self.assertEqual(result["validation_errors"][0]["field"], "unexpected")

    def test_invalid_argument_type_is_rejected(self) -> None:
        result = validate_runtime_tool_call(
            "search_grc",
            {"query": "samp_rate", "scope": "session", "k": "five"},
            self._schema_map(),
        )

        assert result is not None
        self.assertEqual(result["error_type"], "tool_call_invalid")
        self.assertEqual(result["validation_errors"][0]["code"], "invalid_type")
        self.assertEqual(result["validation_errors"][0]["field"], "k")

    def test_invalid_enum_value_is_rejected(self) -> None:
        result = validate_runtime_tool_call(
            "search_grc",
            {"query": "samp_rate", "scope": "everywhere"},
            self._schema_map(),
        )

        assert result is not None
        self.assertEqual(result["error_type"], "tool_call_invalid")
        self.assertEqual(result["validation_errors"][0]["code"], "invalid_enum")
        self.assertEqual(result["validation_errors"][0]["field"], "scope")
