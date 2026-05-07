"""Current-contract tests for TurnPlan advisor shadow integrations."""

from __future__ import annotations

import json
import unittest

from grc_agent.agent import GrcAgent
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.llama_server import run_bounded_llama_turn
from grc_agent.runtime.turn_plan import build_turn_plan
from grc_agent.runtime.turnplan_advisor import (
    AdvisorValidationError,
    build_mode_advisor_messages,
    compile_mode_advisor_plan,
    run_turnplan_mode_advisor,
    validate_mode_advisor_payload,
)


class _FakeModeAdvisorClient:
    def __init__(
        self,
        payload: dict[str, str] | None = None,
        *,
        malformed: bool = False,
        raise_error: Exception | None = None,
    ):
        self.payload = payload if payload is not None else {"mode": "read_only"}
        self.malformed = malformed
        self.raise_error = raise_error
        self.requests: list[dict[str, object]] = []
        self.timeout_seconds = 60.0

    def create_chat_completion(self, **kwargs):
        self.requests.append(kwargs)
        if self.raise_error is not None:
            raise self.raise_error
        content = "{bad-json" if self.malformed else json.dumps(self.payload)
        return {"choices": [{"message": {"content": content}}]}


class _FakeBoundedTurnClient:
    def __init__(
        self,
        advisor_payload: dict[str, str],
        *,
        malformed: bool = False,
        raise_error: Exception | None = None,
    ):
        self.advisor_payload = advisor_payload
        self.malformed = malformed
        self.raise_error = raise_error
        self.requests: list[dict[str, object]] = []
        self.timeout_seconds = 60.0

    def get_model_id(self):
        return "test-model"

    def require_model_alias(self, model):
        if model != "test-model":
            raise AssertionError(model)

    def create_chat_completion(self, **kwargs):
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            if self.raise_error is not None:
                raise self.raise_error
            content = "{bad-json" if self.malformed else json.dumps(self.advisor_payload)
            return {"choices": [{"message": {"content": content}}]}
        return {"choices": [{"message": {"content": "Handled safely."}}]}

    def parse_assistant_message(
        self,
        response,
        *,
        fallback_transaction_checker=None,
        allowed_tool_names=None,
        assistant_text_fallback_enabled=True,
    ):
        _ = (
            fallback_transaction_checker,
            allowed_tool_names,
            assistant_text_fallback_enabled,
        )
        message = response["choices"][0]["message"]
        return message.get("content"), []


def _request_tool_names(request: dict[str, object]) -> list[str]:
    tools = request.get("tools", [])
    return [tool["function"]["name"] for tool in tools]  # type: ignore[index]


class AdvisorModeSchemaTests(unittest.TestCase):
    def test_schema_accepts_strict_mode_payload(self):
        result = validate_mode_advisor_payload({"mode": "read_only"})
        self.assertEqual(result.mode, "read_only")

    def test_schema_rejects_unknown_mode(self):
        with self.assertRaises(AdvisorValidationError):
            validate_mode_advisor_payload({"mode": "search"})

    def test_schema_rejects_extra_fields(self):
        with self.assertRaises(AdvisorValidationError):
            validate_mode_advisor_payload({"mode": "read_only", "reason": "extra"})

    def test_prompt_contract_is_one_field_only(self):
        messages = build_mode_advisor_messages(
            user_message="Preview changing samp_rate to 64000.",
            session_summary={"session_loaded": False},
            prompt_version="v13",
        )
        prompt = messages[1]["content"]
        self.assertIn('"required_json": {"mode": "one allowed mode"}', prompt)
        self.assertNotIn("risk_flags", prompt)
        self.assertNotIn("target_mentions", prompt)


class AdvisorModeCompilerTests(unittest.TestCase):
    def test_mode_compiler_maps_preview_without_tool_args(self):
        compiled = compile_mode_advisor_plan(
            validate_mode_advisor_payload({"mode": "preview"}),
            user_message="Any prompt text.",
        )
        self.assertEqual(compiled.mode, "preview")
        self.assertEqual(compiled.allowed_tools, ("propose_edit",))

    def test_mode_compiler_maps_clarify_to_no_tools(self):
        compiled = compile_mode_advisor_plan(
            validate_mode_advisor_payload({"mode": "clarify"}),
            user_message="Any prompt text.",
        )
        self.assertTrue(compiled.requires_clarification)
        self.assertEqual(compiled.allowed_tools, ())

    def test_mode_compiler_maps_unsupported_to_no_tools(self):
        compiled = compile_mode_advisor_plan(
            validate_mode_advisor_payload({"mode": "unsupported"}),
            user_message="Any prompt text.",
        )
        self.assertEqual(compiled.allowed_tools, ())


class AdvisorModeRuntimeTests(unittest.TestCase):
    def test_mode_advisor_malformed_payload_falls_back_safely(self):
        client = _FakeModeAdvisorClient(malformed=True)
        observation = run_turnplan_mode_advisor(
            client=client,
            model="test-model",
            user_message="Preview changing samp_rate to 64000.",
            session=FlowgraphSession(),
            deterministic_plan=build_turn_plan("Preview changing samp_rate to 64000."),
            prompt_version="v13",
        )
        self.assertFalse(observation.parse_success)
        self.assertFalse(observation.schema_valid)
        self.assertIsNone(observation.candidate_plan)

    def test_mode_advisor_timeout_falls_back_safely(self):
        client = _FakeModeAdvisorClient(raise_error=TimeoutError("timeout"))
        observation = run_turnplan_mode_advisor(
            client=client,
            model="test-model",
            user_message="Preview changing samp_rate to 64000.",
            session=FlowgraphSession(),
            deterministic_plan=build_turn_plan("Preview changing samp_rate to 64000."),
            prompt_version="v13",
            timeout_seconds=0.01,
        )
        self.assertFalse(observation.parse_success)
        self.assertFalse(observation.schema_valid)
        self.assertIsNone(observation.candidate_plan)


class AdvisorShadowIntegrationTests(unittest.TestCase):
    def test_disabled_advisor_keeps_deterministic_path(self):
        agent = GrcAgent()
        client = _FakeBoundedTurnClient({"mode": "read_only"})
        result = run_bounded_llama_turn(
            agent,
            client,
            "Change samp_rate to 48000.",
            model="test-model",
            advisor_enabled=False,
            advisor_limited_advisory=False,
        )
        self.assertTrue(result["ok"])
        self.assertNotIn("turnplan_advisor_mode_shadow", result)
        self.assertNotIn("turnplan_advisor_permission_shadow", result)

    def test_limited_advisory_is_shadow_only_for_read_only_prompt(self):
        agent = GrcAgent()
        client = _FakeBoundedTurnClient({"mode": "unsupported"})
        result = run_bounded_llama_turn(
            agent,
            client,
            "Find throttle blocks.",
            model="test-model",
            advisor_enabled=True,
            advisor_limited_advisory=True,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(_request_tool_names(client.requests[0]), [])
        self.assertIn("search_grc", _request_tool_names(client.requests[1]))

    def test_limited_advisory_keeps_preview_tool_exposure_safe(self):
        agent = GrcAgent()
        client = _FakeBoundedTurnClient({"mode": "edit"})
        result = run_bounded_llama_turn(
            agent,
            client,
            "Preview changing samp_rate to 48000; do not apply.",
            model="test-model",
            advisor_enabled=True,
            advisor_limited_advisory=True,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(_request_tool_names(client.requests[1]), ["propose_edit"])

    def test_malformed_advisor_output_falls_back_to_deterministic(self):
        agent = GrcAgent()
        client = _FakeBoundedTurnClient({"mode": "read_only"}, malformed=True)
        result = run_bounded_llama_turn(
            agent,
            client,
            "Change samp_rate to 48000.",
            model="test-model",
            advisor_enabled=True,
            advisor_limited_advisory=True,
        )
        self.assertTrue(result["ok"])
        self.assertNotIn("turnplan_advisor_permission_shadow", result)

    def test_advisor_timeout_falls_back_to_deterministic(self):
        agent = GrcAgent()
        client = _FakeBoundedTurnClient({"mode": "read_only"}, raise_error=TimeoutError("timeout"))
        result = run_bounded_llama_turn(
            agent,
            client,
            "Change samp_rate to 48000.",
            model="test-model",
            advisor_enabled=True,
            advisor_limited_advisory=True,
        )
        self.assertTrue(result["ok"])
        self.assertNotIn("turnplan_advisor_permission_shadow", result)


if __name__ == "__main__":
    unittest.main()
