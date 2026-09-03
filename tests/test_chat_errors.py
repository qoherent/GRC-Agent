"""Tests for grc_agent.chat.errors — error-message shaping for a failed agent
turn. No GTK import: runs under the fast gate without xvfb.
"""


def test_format_turn_error_covers_each_exception_type():
    """_run_agent_turn collapsed 4 near-duplicate except blocks (ModelHTTPError,
    UsageLimitExceeded, ModelAPIError, UnexpectedModelBehavior) plus the
    generic Exception fallback into one handler backed by this message
    builder. Each branch's message shape must survive the refactor exactly,
    including ModelHTTPError's extra status/body-vs-model_name distinction."""
    from pydantic_ai.exceptions import (
        ModelAPIError,
        ModelHTTPError,
        UnexpectedModelBehavior,
        UsageLimitExceeded,
    )

    from grc_agent.chat.errors import _format_turn_error

    assert (
        _format_turn_error(ModelHTTPError(500, "gpt-x", body="server exploded"))
        == "Model HTTP 500 Error: server exploded"
    )
    assert (
        _format_turn_error(
            ModelHTTPError(403, "gpt-x", body={"message": "Key limit exceeded", "code": 403})
        )
        == "Model HTTP 403 Error: Key limit exceeded"
    )
    assert _format_turn_error(ModelHTTPError(503, "gpt-x")) == "Model HTTP 503 Error from gpt-x"
    assert _format_turn_error(UsageLimitExceeded("too many tokens")).startswith(
        "Usage Limit Exceeded: too many tokens"
    )
    assert (
        _format_turn_error(ModelAPIError("gpt-x", "bad request")) == "Model API Error: bad request"
    )
    assert (
        _format_turn_error(UnexpectedModelBehavior("no tool call"))
        == "Unexpected Model Behavior: no tool call"
    )
    assert _format_turn_error(RuntimeError("boom")) == "Agent Error: boom"

    # Deep cause extraction from HTTPStatusError
    import httpx

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp_json = httpx.Response(
        401, request=req, json={"error": {"message": "Invalid API key provided"}}
    )
    try:
        resp_json.raise_for_status()
    except Exception as c:
        try:
            raise ModelAPIError("gpt-5.6-sol", "Connection error.") from c
        except Exception as exc:
            assert (
                _format_turn_error(exc)
                == "Model API Error: Connection error. (Cause: Invalid API key provided)"
            )


def test_friendly_exhaustion_message():
    """Retry-budget turn deaths render a continuation message, not pydantic-
    ai's developer-aimed "Consider raising the max retry limit" text."""
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    from grc_agent.chat.errors import _friendly_exhaustion_message

    tool_msg = _friendly_exhaustion_message(
        UnexpectedModelBehavior(
            "Tool 'change_graph' exceeded max retries count of 3. Consider raising the max retry limit."
        )
    )
    assert tool_msg is not None
    assert "change_graph" in tool_msg and "safe" in tool_msg and "Continue" in tool_msg

    out_msg = _friendly_exhaustion_message(
        UnexpectedModelBehavior("Exceeded maximum output retries (3).")
    )
    assert out_msg is not None and "validation" in out_msg

    assert _friendly_exhaustion_message(UnexpectedModelBehavior("other")) is None
    assert _friendly_exhaustion_message(ValueError("other")) is None


def test_extract_message_helpers():
    """The three lower-level extractors _format_turn_error/_extract_cause_message
    build on: httpx response JSON, a ModelHTTPError.body dict, and the
    combined cause-chain resolution."""
    from grc_agent.chat.errors import (
        _extract_body_message,
        _extract_cause_message,
        _extract_httpx_message,
    )

    class FakeResp:
        def json(self):
            return {"error": {"message": "nested error"}}

    assert _extract_httpx_message(FakeResp()) == "nested error"

    class BadResp:
        def json(self):
            raise ValueError("not json")

        text = "raw text body that is quite long " * 20

    assert _extract_httpx_message(BadResp())[:10] == BadResp.text[:10]

    assert _extract_body_message({"error": {"message": "m"}}) == "m"
    assert _extract_body_message({"message": "top-level"}) == "top-level"
    assert _extract_body_message("plain string") == "plain string"

    class FakeCause(Exception):
        response = FakeResp()

    assert _extract_cause_message(FakeCause()) == "nested error"
    assert _extract_cause_message(ValueError("fallback")) == "fallback"
