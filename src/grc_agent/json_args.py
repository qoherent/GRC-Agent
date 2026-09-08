"""One home for the JSON-stringified-argument repair rule.

Many small, fast, or remote models format composite tool parameters (lists and
objects) as serialized JSON strings instead of native JSON arrays/objects.
Exactly one rule handles that everywhere: *a string whose stripped form opens
and closes as a JSON array or object becomes the parsed value; anything else is
left alone.*

Two callers apply it, for two different reasons:

- ``agent.JsonRepairCapability.before_tool_validate`` repairs the arguments
  pydantic-ai is about to validate, gated on the parameter's own JSON schema.
- ``ui.approval_card`` repairs the arguments it renders for human consent.
  pydantic-ai keeps the validation repair in a local (``tool_manager.py``
  assigns ``raw_args = await cap.before_tool_validate(...)``) and builds
  ``DeferredToolRequests`` from the original ``ToolCallPart``, so the card is
  handed the model's raw arguments and has to apply the rule itself.

The module is deliberately dependency-free (stdlib + pydantic's
``BeforeValidator``) so the GTK-side card can import it without pulling the
agent layer, GNU Radio's platform, or pydantic-ai's capability machinery.

``repair_json_args`` never mutates its input. That matters: ``raw_args`` IS
``call.args`` (same object) when a provider delivers arguments as a dict, so
repairing in place would rewrite the recorded message history and destroy the
record of what the model actually emitted -- the evidence any post-mortem of a
malformed call depends on.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BeforeValidator
from pydantic_ai import ModelRetry


def coerce_json_mapping(v: Any) -> Any:
    """Decode a JSON string if the argument was passed as a serialized JSON object."""
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                return json.loads(s)
            except Exception as exc:
                raise ModelRetry(f"Invalid JSON object string: {exc}") from exc
        elif not s:
            return {}
    return v


JsonCoercedMapping = BeforeValidator(coerce_json_mapping)


def coerce_json_sequence(v: Any) -> Any:
    """Decode a JSON string, wrap a single element, or coerce None/empty to list."""
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                return json.loads(s)
            except Exception as exc:
                raise ModelRetry(f"Invalid JSON array string: {exc}") from exc
        elif not s:
            return []
        return [s]
    elif isinstance(v, dict):
        return [v]
    return v


JsonCoercedSequence = BeforeValidator(coerce_json_sequence)


def is_composite_schema(spec: dict[str, Any]) -> bool:
    """Whether a JSON schema specification describes an array or object."""
    if not isinstance(spec, dict):
        return False
    target_type = spec.get("type")
    if target_type in ("array", "object") or "items" in spec or "$ref" in spec:
        return True
    for branch in spec.get("anyOf", []) + spec.get("oneOf", []) + spec.get("allOf", []):
        if isinstance(branch, dict) and is_composite_schema(branch):
            return True
    return False


def _decoded(value: Any) -> Any:
    """The rule itself: a bracketed JSON string becomes the parsed value.

    Anything else -- a scalar, a non-bracketed string, an already-decoded list
    or dict, or a bracketed string that does not parse -- is returned
    unchanged. Malformed JSON is deliberately not an error here: the caller's
    own validation reports it with actionable feedback (``coerce_json_sequence``
    raises ``ModelRetry``; the card renders the literal), and a raise from this
    function would either abort a turn or blank an approval card.
    """
    if not isinstance(value, str):
        return value
    s = value.strip()
    if not ((s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}"))):
        return value
    try:
        return json.loads(s)
    except Exception:
        return value


def repair_json_args(
    args: str | dict[str, Any] | None,
    properties: dict[str, Any] | None = None,
) -> Any:
    """Decode JSON-stringified composite arguments, returning a new value.

    ``args`` may arrive as a whole JSON string (several providers deliver tool
    arguments that way), a dict, or ``None``.

    ``properties`` is the tool's JSON-schema ``properties`` map. When given,
    only parameters whose schema expects an array or object are decoded -- a
    genuinely string-typed parameter holding bracket characters is left intact.
    When ``None`` (a renderer, which has no schema in hand), the same rule
    applies to every key.

    The two modes differ in exactly one further way, deliberately. Without
    a schema, list *elements* are decoded one level deeper, since a model
    that stringifies a list often stringifies its items too
    (``["{...}"]``). With a schema they are not: that would make an
    argument the tool contract currently rejects start validating, and the
    strictness of that contract is a recorded decision (`docs/backlog.md`,
    "Scenario suite default model" -- no tolerated coercion of
    string-encoded arrays). A renderer has no contract to keep and every
    reason to show the human what was actually proposed, so it decodes;
    the validation path stays exactly as strict as before.
    """
    if isinstance(args, str):
        decoded = _decoded(args)
        if not isinstance(decoded, dict):
            return args
        args = decoded
    if not isinstance(args, dict):
        return args
    repaired: dict[str, Any] = dict(args)
    for key, value in repaired.items():
        if properties is None:
            decoded = _decoded(value)
            if isinstance(decoded, list):
                decoded = [_decoded(element) for element in decoded]
        elif is_composite_schema(properties.get(key, {})):
            decoded = _decoded(value)
        else:
            continue
        repaired[key] = decoded
    return repaired
