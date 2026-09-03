"""User-facing error message shaping for a failed agent turn.

Pure functions extracted from ``chat_sidebar.py`` — no GTK, no ``self``.
Normalises the several shapes a provider failure can arrive in (an httpx
response body, a pydantic-ai ``ModelHTTPError.body``, an exception cause
chain) into one readable message, and gives pydantic-ai's own bounded-retry
exhaustion a friendlier explanation than its developer-facing default text.
"""

from __future__ import annotations

from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)


def _extract_httpx_message(resp) -> str:
    """Provider JSON error message from an httpx response, if any."""
    try:
        data = resp.json()
    except Exception:
        return getattr(resp, "text", "")[:300]
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    if isinstance(err, str):
        return err
    if data.get("message"):
        return str(data["message"])
    if data.get("detail"):
        return str(data["detail"])
    return ""


def _extract_body_message(body) -> str:
    """Provider JSON error message from a body attribute, if any."""
    if not isinstance(body, dict):
        return str(body)
    err = body.get("error")
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    return str(body.get("message") or body.get("detail") or body)


def _extract_cause_message(cause: BaseException) -> str:
    """Best-effort human message from an exception chain cause.

    Prefers the provider's JSON error payload (httpx response or ``body``
    attribute) over the raw exception string, so the user sees e.g.
    "Invalid API key provided" instead of a bare status line.
    """
    cause_msg = ""
    resp = getattr(cause, "response", None)
    if resp is not None:
        cause_msg = _extract_httpx_message(resp)
    body = getattr(cause, "body", None)
    if not cause_msg and body:
        cause_msg = _extract_body_message(body)
    return cause_msg or str(cause)


def _format_turn_error(e: Exception) -> str:
    """User-facing message for a failed agent turn (_run_agent_turn's catch-all).
    Exposes exact status codes, provider error message details, and underlying causes.
    """
    cause_str = ""
    if hasattr(e, "__cause__") and e.__cause__:
        cause_msg = _extract_cause_message(e.__cause__)
        if cause_msg and cause_msg != str(e):
            cause_str = f" (Cause: {cause_msg})"

    if isinstance(e, ModelHTTPError):
        msg = f"Model HTTP {e.status_code} Error"
        if e.body:
            body_detail = ""
            if isinstance(e.body, dict):
                body_detail = (
                    e.body.get("message")
                    or e.body.get("error", {}).get("message")
                    or e.body.get("detail")
                    or str(e.body)
                )
            else:
                body_detail = str(e.body)
            return f"{msg}: {body_detail}{cause_str}"
        model_name = getattr(e, "model_name", "model")
        return f"{msg} from {model_name}{cause_str}"

    if isinstance(e, ModelAPIError):
        return f"Model API Error: {e}{cause_str}"

    if isinstance(e, UsageLimitExceeded):
        return f"Usage Limit Exceeded: {e}{cause_str}"

    if isinstance(e, UnexpectedModelBehavior):
        friendly = _friendly_exhaustion_message(e)
        if friendly is not None:
            return friendly
        return f"Unexpected Model Behavior: {e}{cause_str}"

    return f"Agent Error: {e}{cause_str}"


def _friendly_exhaustion_message(e: Exception) -> str | None:
    """Friendlier text when a turn died on pydantic-ai's bounded retry budgets.

    The raw UnexpectedModelBehavior text ("Consider raising the max retry
    limit…") is aimed at developers; a user needs to know the flowgraph is
    safe and that the next turn starts with a fresh budget. Returns None for
    any other error, so callers fall back to the standard formatting.
    """
    if not isinstance(e, UnexpectedModelBehavior):
        return None
    msg = str(e)
    if "exceeded max retries count of" in msg:
        tool = msg.split("'", 2)[1] if msg.count("'") >= 2 else "a tool"
        return (
            f"The agent ran out of fix attempts for '{tool}' this turn — the "
            "flowgraph is unchanged and safe (every failed batch was rolled back). "
            "Send Continue or rephrase the request; the next attempt has a fresh budget."
        )
    if "Exceeded maximum output retries" in msg:
        return (
            "The agent's final state failed flowgraph validation more than the allowed "
            "attempts this turn — the flowgraph is unchanged and safe. "
            "Send Continue to retry with the previous errors still in context."
        )
    return None
