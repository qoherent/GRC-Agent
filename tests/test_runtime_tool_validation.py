"""Tests for runtime tool-call schema validation."""

import unittest
from pathlib import Path

from grc_agent.agent import GrcAgent
from grc_agent.domain_models import ErrorCode
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime_tool_validation import build_tool_schema_map, validate_runtime_tool_call


class RuntimeToolValidationTests(unittest.TestCase):
    """Validate the narrowed model-facing tool schema contract."""

    def _fixture_path(self) -> Path:
        test_directory = Path(__file__).resolve().parent
        return test_directory / "data" / "dial_tone.grc"

    def _schema_map(self) -> dict[str, dict]:
        session = FlowgraphSession()
        session.load(self._fixture_path())
        agent = GrcAgent(session)
        return build_tool_schema_map(agent._tool_schemas)

    def test_unknown_tool_is_rejected(self) -> None:
        result = validate_runtime_tool_call("set_variable", {}, self._schema_map())

        assert result is not None
        self.assertEqual(result["error_type"], ErrorCode.UNKNOWN_TOOL)
        self.assertEqual(result["validation_errors"][0]["code"], ErrorCode.UNKNOWN_TOOL)

    def test_unsupported_extra_argument_is_rejected(self) -> None:
        result = validate_runtime_tool_call(
            "query_knowledge",
            {"query": "throttle", "domain": "catalog", "unexpected": True},
            self._schema_map(),
        )

        assert result is not None
        self.assertEqual(result["error_type"], ErrorCode.TOOL_CALL_INVALID)
        self.assertEqual(result["validation_errors"][0]["code"], "unexpected_argument")
        self.assertEqual(result["validation_errors"][0]["field"], "unexpected")
        self.assertTrue(result["schema_repair_instruction"]["no_tool_ran"])
        self.assertEqual(result["schema_repair_instruction"]["tool"], "query_knowledge")

    def test_invalid_argument_type_is_rejected(self) -> None:
        result = validate_runtime_tool_call(
            "query_knowledge",
            {"query": "samp_rate", "domain": 123},
            self._schema_map(),
        )

        assert result is not None
        self.assertEqual(result["error_type"], ErrorCode.TOOL_CALL_INVALID)
        self.assertEqual(result["validation_errors"][0]["code"], "invalid_type")
        self.assertEqual(result["validation_errors"][0]["field"], "domain")

    def test_invalid_enum_value_is_rejected(self) -> None:
        result = validate_runtime_tool_call(
            "query_knowledge",
            {"query": "samp_rate", "domain": "everywhere"},
            self._schema_map(),
        )

        assert result is not None
        self.assertEqual(result["error_type"], ErrorCode.TOOL_CALL_INVALID)
        self.assertEqual(result["validation_errors"][0]["code"], "invalid_enum")
        self.assertEqual(result["validation_errors"][0]["field"], "domain")

    def test_overview_without_filler_arguments_is_valid(self) -> None:
        result = validate_runtime_tool_call(
            "inspect_graph",
            {},
            self._schema_map(),
        )

        self.assertIsNone(result)
