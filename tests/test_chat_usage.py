"""Tests for grc_agent.chat.usage — token usage and native cost collection.
No GTK import: runs under the fast gate without xvfb.

These functions previously had no direct test coverage at all -- only the
GTK integration test_context_label_updates_with_pydantic_ai_usage exercised
them indirectly through a live ChatSidebar.
"""

from decimal import Decimal


def _resp(input_tokens=0, output_tokens=0, cost=None, details=None):
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.usage import RequestUsage

    return ModelResponse(
        parts=[],
        usage=RequestUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            details=details or {},
        ),
    )


def _req(text="hi"):
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    return ModelRequest(parts=[UserPromptPart(content=text)])


def test_collect_token_usage_sums_since_latest_user_prompt():
    from grc_agent.chat.usage import _collect_token_usage

    msgs = [
        _req("first turn"),
        _resp(input_tokens=100, output_tokens=50, cost=Decimal("0.001")),
        _req("second turn"),
        _resp(input_tokens=200, output_tokens=80, cost=Decimal("0.002")),
    ]
    last_input, last_output, last_reasoning, total, turn_cost, has_usage = _collect_token_usage(
        msgs
    )
    # Only the SECOND turn's usage counts -- the first user prompt resets the sum.
    assert last_input == 200
    assert last_output == 80
    assert has_usage is True
    assert turn_cost == Decimal("0.002")


def test_collect_token_usage_incomplete_cost_is_none():
    """A turn is cost-complete only when every response in it has usage.cost;
    a partial sum would understate the real cost."""
    from grc_agent.chat.usage import _collect_token_usage

    msgs = [
        _req(),
        _resp(input_tokens=100, output_tokens=10, cost=Decimal("0.001")),
        _resp(input_tokens=100, output_tokens=10, cost=None),
    ]
    *_rest, turn_cost, has_usage = _collect_token_usage(msgs)
    assert has_usage is True
    assert turn_cost is None


def test_collect_token_usage_reads_reasoning_tokens_from_details():
    from grc_agent.chat.usage import _collect_token_usage

    msgs = [_req(), _resp(input_tokens=50, output_tokens=20, details={"reasoning_tokens": 7})]
    _last_input, _last_output, last_reasoning, *_rest = _collect_token_usage(msgs)
    assert last_reasoning == 7


def test_collect_token_usage_empty_history():
    from grc_agent.chat.usage import _collect_token_usage

    last_input, last_output, last_reasoning, total, turn_cost, has_usage = _collect_token_usage([])
    assert (last_input, last_output, last_reasoning, total) == (0, 0, 0, 0)
    assert turn_cost is None
    assert has_usage is False


def test_format_native_cost_no_invented_precision():
    from grc_agent.chat.usage import _format_native_cost

    assert _format_native_cost(Decimal("0.0012345")) == "$0.0012345"
    assert _format_native_cost(Decimal("1.50")) == "$1.5"
    assert _format_native_cost(Decimal("0")) == "$0"


def test_run_usage_output_override_prefers_live_run():
    from unittest.mock import MagicMock

    from grc_agent.chat.usage import _run_usage_output_override

    # No run -> the last-response figures pass through unchanged.
    assert _run_usage_output_override(None, 10, 2) == (10, 2)

    run = MagicMock()
    run.usage.output_tokens = 500
    run.usage.details = {"reasoning_tokens": 42}
    assert _run_usage_output_override(run, 10, 2) == (500, 42)

    # A run whose usage is None also passes through unchanged.
    run_no_usage = MagicMock(usage=None)
    assert _run_usage_output_override(run_no_usage, 10, 2) == (10, 2)


def test_run_usage_cost_override_requires_real_activity():
    from unittest.mock import MagicMock

    from grc_agent.chat.usage import _run_usage_cost_override

    assert _run_usage_cost_override(None, Decimal("1"), True) == (Decimal("1"), True)

    run = MagicMock()
    run.usage.requests = 0
    run.usage.input_tokens = 0
    run.usage.output_tokens = 0
    run.usage.cost = None
    # A run with genuinely zero activity does not override -- the caller's
    # own figures stand.
    assert _run_usage_cost_override(run, Decimal("1"), True) == (Decimal("1"), True)

    active_run = MagicMock()
    active_run.usage.requests = 3
    active_run.usage.cost = Decimal("0.05")
    assert _run_usage_cost_override(active_run, Decimal("1"), True) == (Decimal("0.05"), True)
