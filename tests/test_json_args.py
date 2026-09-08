"""The one JSON-stringified-argument repair rule (`grc_agent.json_args`).

Both callers of the rule are covered here: the schema-driven mode the
`before_tool_validate` capability uses, and the schema-free mode the approval
card uses. The payload shapes are the ones session 165's model actually
emitted (every composite argument arrived as a JSON string).
"""

from __future__ import annotations

import json

from grc_agent.json_args import is_composite_schema, repair_json_args

_CHANGE_GRAPH_PROPS = {
    "reason": {"type": "string"},
    "add_blocks": {"type": "array", "items": {"$ref": "#/$defs/BlockAdd"}},
    "remove_blocks": {"type": "array", "items": {"type": "string"}},
    "add_connections": {"type": "array", "items": {"type": "string"}},
    "force": {"type": "boolean"},
}

_BLOCK = {"block_id": "analog_sig_source_x", "instance_name": "src0", "params": {"freq": "1000"}}


def test_schema_mode_decodes_composite_and_leaves_string_params_alone():
    args = {
        "reason": "[not json, just a bracketed sentence]",
        "add_blocks": json.dumps([_BLOCK]),
        "force": True,
    }
    out = repair_json_args(args, _CHANGE_GRAPH_PROPS)

    assert out["add_blocks"] == [_BLOCK]
    # `reason` is string-typed in the schema: the brackets are content, not JSON.
    assert out["reason"] == "[not json, just a bracketed sentence]"
    assert out["force"] is True


def test_schema_mode_ignores_unknown_keys():
    """A key absent from the schema gets `{}` as its spec, which is not composite."""
    out = repair_json_args({"mystery": json.dumps([1, 2])}, _CHANGE_GRAPH_PROPS)
    assert out["mystery"] == json.dumps([1, 2])


def test_schema_free_mode_decodes_every_bracketed_string():
    args = {
        "add_blocks": json.dumps([_BLOCK]),
        "add_connections": json.dumps(["src0:0->throttle:0"]),
        "remove_blocks": json.dumps(["old"]),
        "reason": "wire the source",
        "force": "true",
        "count": 3,
        "already": [{"instance_name": "kept"}],
    }
    out = repair_json_args(args)

    assert out["add_blocks"] == [_BLOCK]
    assert out["add_connections"] == ["src0:0->throttle:0"]
    assert out["remove_blocks"] == ["old"]
    # Untouched: not bracketed, not a string, or already decoded.
    assert out["reason"] == "wire the source"
    assert out["force"] == "true"
    assert out["count"] == 3
    assert out["already"] == [{"instance_name": "kept"}]


def test_malformed_json_string_is_returned_unchanged():
    """Session 165's real `write_plan` payload: a valid array plus a trailing tail.

    The rule must not raise -- the caller's own validation reports it (with
    actionable feedback for the model), and a raise here would abort a turn or
    blank an approval card.
    """
    malformed = '[{"content": "step one", "status": "pending"}], "warnings": []}'
    out = repair_json_args({"items": malformed})
    assert out["items"] == malformed


def test_list_elements_are_decoded_one_level_deeper_for_the_renderer_only():
    """The card decodes stringified elements; validation stays strict.

    Loosening the schema-driven path would make an argument the tool contract
    currently rejects start validating, and that strictness is a recorded
    decision (docs/backlog.md, "Scenario suite default model"). The renderer
    has no contract to keep and must show the human what was proposed.
    """
    out = repair_json_args({"add_blocks": [json.dumps(_BLOCK), _BLOCK]})
    assert out["add_blocks"] == [_BLOCK, _BLOCK]

    strict = repair_json_args({"add_blocks": [json.dumps(_BLOCK)]}, _CHANGE_GRAPH_PROPS)
    assert strict["add_blocks"] == [json.dumps(_BLOCK)], "validation must stay as strict as before"


def test_plain_string_elements_survive_elementwise_decoding():
    out = repair_json_args({"add_connections": ["src0:0->throttle:0", "throttle:0->sink:0"]})
    assert out["add_connections"] == ["src0:0->throttle:0", "throttle:0->sink:0"]


def test_whole_args_arriving_as_a_json_string_is_parsed():
    out = repair_json_args(json.dumps({"add_blocks": json.dumps([_BLOCK])}))
    assert out["add_blocks"] == [_BLOCK]


def test_non_dict_args_are_returned_as_is():
    assert repair_json_args("[1, 2, 3]") == "[1, 2, 3]"
    assert repair_json_args("not json at all") == "not json at all"
    assert repair_json_args(None) is None


def test_repair_never_mutates_its_input():
    """`raw_args` IS `call.args` in pydantic-ai's tool_manager, so in-place
    repair would rewrite the recorded history of what the model emitted."""
    args = {"add_blocks": json.dumps([_BLOCK])}
    snapshot = dict(args)
    out = repair_json_args(args, _CHANGE_GRAPH_PROPS)

    assert args == snapshot
    assert out is not args


def test_is_composite_schema_matches_the_shapes_the_tools_generate():
    assert is_composite_schema({"type": "array"}) is True
    assert is_composite_schema({"type": "object"}) is True
    assert is_composite_schema({"items": {"type": "string"}}) is True
    assert is_composite_schema({"$ref": "#/$defs/BlockAdd"}) is True
    assert is_composite_schema({"anyOf": [{"type": "array"}, {"type": "null"}]}) is True
    assert is_composite_schema({"type": "string"}) is False
    assert is_composite_schema({}) is False
    assert is_composite_schema(None) is False
